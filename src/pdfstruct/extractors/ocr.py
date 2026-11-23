"""Optional, local OCR for scanned PDF pages.

PDFium renders one page at a time and an OCR backend supplies text boxes. Those
boxes are converted to pdfstruct's bottom-left PDF coordinates before the
normal layout/structure pipeline runs. No OCR dependencies are imported until
a page actually needs OCR, and no document data is sent to a service.
"""

from __future__ import annotations

import logging
import math
import os
import re
import shlex
import threading
import unicodedata
from collections import defaultdict
from contextlib import closing
from dataclasses import dataclass, replace
from difflib import SequenceMatcher
from itertools import pairwise
from pathlib import Path
from statistics import median
from time import monotonic
from typing import Literal

from pdfminer.layout import LTImage

from pdfstruct.extractors.text import RawPage
from pdfstruct.models.document import BBox, Character, FontInfo, OCRSpan, TextLine

logger = logging.getLogger(__name__)

OCRMode = bool | Literal["auto", "always"]
MAX_RENDER_PIXELS = 40_000_000
# PDFium is not thread-safe. Protect all renderer handles used by this module;
# use separate processes for parallel OCR throughput.
_PDFIUM_LOCK = threading.RLock()


class OCRDependencyError(ImportError):
    """The optional Python packages or Tesseract executable are missing."""


class OCRError(RuntimeError):
    """A page could not be rendered or recognized; no partial result is returned."""


@dataclass(frozen=True)
class OCROptions:
    mode: OCRMode = False
    language: str = "eng"
    dpi: int = 300
    min_text_chars: int = 50
    timeout: float = 60.0
    backend: Literal["rapidocr", "tesseract"] = "rapidocr"
    model_dir: str | os.PathLike[str] | None = None
    tessdata_dir: str | os.PathLike[str] | None = None
    offline: bool = False

    def __post_init__(self):
        if not (type(self.mode) is bool or self.mode in ("auto", "always")):
            raise ValueError('ocr must be False, True, "auto", or "always"')
        if self.backend not in ("rapidocr", "tesseract"):
            raise ValueError("ocr_backend must be 'rapidocr' or 'tesseract'")
        if not isinstance(self.language, str) or not re.fullmatch(
            r"[A-Za-z0-9_/-]+(?:\+[A-Za-z0-9_/-]+)*", self.language
        ):
            raise ValueError("ocr_language must be Tesseract language codes, e.g. 'eng+fra'")
        if type(self.dpi) is not int or not 72 <= self.dpi <= 600:
            raise ValueError("ocr_dpi must be an integer between 72 and 600")
        if type(self.min_text_chars) is not int or self.min_text_chars < 0:
            raise ValueError("ocr_min_text_chars must be a non-negative integer")
        if (
            isinstance(self.timeout, bool)
            or not isinstance(self.timeout, (int, float))
            or not math.isfinite(self.timeout)
            or self.timeout <= 0
        ):
            raise ValueError("ocr_timeout must be a positive, finite number of seconds")
        if type(self.offline) is not bool:
            raise ValueError("ocr_offline must be a boolean")
        for name in ("model_dir", "tessdata_dir"):
            value = getattr(self, name)
            if value is not None and (
                not isinstance(value, (str, os.PathLike)) or not os.fspath(value)
            ):
                raise ValueError(f"ocr_{name} must be a non-empty local directory path")


def _usable_text(text: str) -> bool:
    """Reject visibly broken PDF character maps, without an English dictionary.

    This intentionally cannot identify plausible but incorrect hidden OCR text.
    Accents, non-Latin scripts, currency symbols and punctuation remain valid.
    """
    visible = [char for char in text if not char.isspace()]
    if not visible:
        return False
    cid_chars = sum(len(value) for value in re.findall(r"\(cid:\d+\)", text))
    bad = sum(
        char == "\ufffd" or unicodedata.category(char) in ("Cc", "Cs", "Co", "Cn")
        for char in visible
    )
    return (bad + cid_chars) / len(visible) < 0.2


def _raster_boxes(page: RawPage) -> list[BBox]:
    if page.page_layout is None or page.width <= 0 or page.height <= 0:
        return []

    boxes: list[BBox] = []

    def visit(element):
        if isinstance(element, LTImage):
            box = BBox(
                max(0, element.x0),
                max(0, element.y0),
                min(page.width, element.x1),
                min(page.height, element.y1),
            )
            if box.width > 0 and box.height > 0:
                boxes.append(box)
        elif hasattr(element, "__iter__"):
            for child in element:
                visit(child)

    visit(page.page_layout)
    return boxes


def _union_area(boxes: list[BBox]) -> float:
    # Rectangle union prevents repeated/overlapping images being double-counted.
    xs = sorted({x for box in boxes for x in (box.x0, box.x1)})
    area = 0.0
    for left, right in pairwise(xs):
        intervals = sorted((box.y0, box.y1) for box in boxes if box.x0 < right and box.x1 > left)
        covered = 0.0
        end = 0.0
        for bottom, top in intervals:
            covered += max(0, top - max(bottom, end))
            end = max(end, top)
        area += (right - left) * covered
    return area


def _merged_regions(boxes: list[BBox]) -> list[BBox]:
    """Combine overlapping/tiled raster regions, never double-recognize them."""
    regions: list[BBox] = []
    for box in boxes:
        pending = box
        changed = True
        while changed:
            changed = False
            remaining = []
            for other in regions:
                # Include touching image tiles, with a small PDF-unit tolerance.
                if (
                    pending.x0 <= other.x1 + 1 and pending.x1 >= other.x0 - 1
                    and pending.y0 <= other.y1 + 1 and pending.y1 >= other.y0 - 1
                ):
                    pending = pending.merge(other)
                    changed = True
                else:
                    remaining.append(other)
            regions = remaining
        regions.append(pending)
    return regions


def assess_ocr_regions(page: RawPage, min_text_chars: int = 50) -> list[BBox]:
    """Select raster regions lacking a usable native text layer.

    The decision is local to each image, so a native header or a separate text
    column cannot hide a scanned body. Very small images are ignored as likely
    logos. Native text only in a large image's header/footer margins does not
    make the body searchable. Photos and large logos remain ambiguous; force
    mode is available for outlined text or plausible-but-incorrect OCR layers.
    """
    raster_boxes = _raster_boxes(page)
    candidates = _merged_regions(raster_boxes)
    selected = []
    for box in candidates:
        raster_area = _union_area([image for image in raster_boxes if box.contains(image)])
        if (
            raster_area < max(1800.0, page.width * page.height * 0.02)
            or max(box.width, box.height) < 120
            or min(box.width, box.height) < 24
        ):
            continue
        native = [line for line in page.lines if box.overlaps(line.bbox, threshold=0.5)]
        usable = [line for line in native if _usable_text(line.text)]
        # Whole-page scans often have a digital page number or running header.
        # Ignore those margins for coverage assessment, but retain their text.
        if box.height >= page.height * 0.5:
            lower = box.y0 + box.height * 0.12
            upper = box.y1 - box.height * 0.12
            usable = [line for line in usable if lower <= line.bbox.midpoint[1] <= upper]
        char_count = sum(not c.isspace() for line in usable for c in line.text)
        broken = any(not _usable_text(line.text) and line.text.strip() for line in native)
        if broken or not char_count or char_count < min_text_chars:
            selected.append(box)
    return selected


def is_scanned_page(page: RawPage, min_text_chars: int = 50) -> bool:
    """Whether the pre-OCR assessment finds a raster region needing recognition."""
    return bool(assess_ocr_regions(page, min_text_chars))


def _load_pdfium():
    try:
        import pypdfium2
    except ImportError as exc:
        raise OCRDependencyError(
            'OCR rendering requires pypdfium2. Install "pdfstructx[ocr]" '
            'or "pdfstructx[tesseract]".'
        ) from exc
    return pypdfium2


class _TesseractBackend:
    def __init__(self, options: OCROptions | None = None):
        try:
            import pytesseract
        except ImportError as exc:
            raise OCRDependencyError('Install "pdfstructx[tesseract]" for Tesseract OCR.') from exc
        self.engine = pytesseract
        if options is not None:
            self._preflight(options)

    @staticmethod
    def _config(options: OCROptions) -> str:
        config = f"--dpi {options.dpi} --oem 1"
        if options.tessdata_dir is not None:
            config += " --tessdata-dir " + shlex.quote(os.fspath(options.tessdata_dir))
        return config

    def _preflight(self, options: OCROptions) -> None:
        if options.tessdata_dir is not None:
            directory = Path(options.tessdata_dir)
            missing = [
                language for language in options.language.split("+")
                if not (directory / f"{language}.traineddata").is_file()
            ]
            if missing:
                raise OCRDependencyError(
                    "Missing local Tesseract language data: " + ", ".join(missing)
                    + "; provision .traineddata files in ocr_tessdata_dir."
                )
        try:
            available = self.engine.get_languages(config=self._config(options))
        except self.engine.TesseractNotFoundError as exc:
            raise OCRDependencyError(
                "Tesseract was not found; install its executable and put it on PATH."
            ) from exc
        missing = set(options.language.split("+")) - set(available)
        if missing:
            raise OCRDependencyError(
                "Tesseract language data not available: " + ", ".join(sorted(missing))
                + ". Install the language packs or set ocr_tessdata_dir."
            )

    def recognize(self, image, options: OCROptions) -> dict:
        try:
            return self.engine.image_to_data(
                image,
                lang=options.language,
                config=self._config(options),
                output_type=self.engine.Output.DICT,
                timeout=options.timeout,
            )
        except self.engine.TesseractNotFoundError as exc:
            raise OCRDependencyError(
                "Tesseract was not found. Install tesseract-ocr and the language "
                "data you need, and make sure 'tesseract' is on PATH."
            ) from exc


class _RapidOCRBackend:
    def __init__(self, options: OCROptions | None = None):
        options = options or OCROptions()
        try:
            import onnxruntime
            import rapidocr
            from rapidocr import ModelType, OCRVersion, RapidOCR
        except ImportError as exc:
            raise OCRDependencyError('Install "pdfstructx[ocr]" for RapidOCR.') from exc
        # Select the model family explicitly rather than inheriting changing
        # upstream defaults. V6 recognizes Chinese, English and Latin languages
        # together; the v4 classifier corrects upside-down text-line crops.
        directory = (
            Path(options.model_dir) if options.model_dir is not None
            else Path(rapidocr.__file__).resolve().parent / "models"
        )
        paths = _rapid_model_paths(directory)
        params = {
                "Det.ocr_version": OCRVersion.PPOCRV6,
                "Det.model_type": ModelType.SMALL,
                "Rec.ocr_version": OCRVersion.PPOCRV6,
                "Rec.model_type": ModelType.SMALL,
                "Cls.ocr_version": OCRVersion.PPOCRV4,
                "Cls.model_type": ModelType.MOBILE,
                "Global.use_cls": True,
                "Global.text_score": 0.0,
                "Global.log_level": "warning",
                "EngineConfig.onnxruntime.intra_op_num_threads": 4,
                "EngineConfig.onnxruntime.inter_op_num_threads": 1,
                **{f"{component}.model_path": str(path) for component, path in paths.items()},
        }
        dictionary = directory / "rec_keys.txt"
        if dictionary.is_file():
            params["Rec.rec_keys_path"] = str(dictionary)
        if options.offline:
            # Without ONNX character metadata RapidOCR may download a dictionary.
            # Validate this before initializing the pipeline, not after it runs.
            session_options = onnxruntime.SessionOptions()
            session_options.intra_op_num_threads = 1
            session_options.inter_op_num_threads = 1
            session = onnxruntime.InferenceSession(
                str(paths["Rec"]), sess_options=session_options,
                providers=["CPUExecutionProvider"],
            )
            metadata = session.get_modelmeta().custom_metadata_map
            has_characters = bool(metadata.get("character", "").strip())
            del session
            if not has_characters and not dictionary.is_file():
                raise OCRDependencyError(
                    "Offline RapidOCR needs character metadata in the recognition ONNX "
                    "model or a local rec_keys.txt in ocr_model_dir."
                )
        self.engine = RapidOCR(params=params)

    def recognize(self, image, options: OCROptions) -> dict:
        import numpy as np

        # RapidOCR's numpy input is BGR; convert without changing the source.
        offset_x = offset_y = 0
        with image.convert("RGB") as rgb:
            result = self.engine(np.asarray(rgb)[:, :, ::-1].copy())
            if result.txts is None or len(result.txts) == 0:
                # Sparse pages can lose small text during detector resizing.
                # Retry once with whitespace removed, then map boxes back to
                # the full page. Blank pages do not trigger this retry.
                with (
                    rgb.convert("L") as gray,
                    gray.point(lambda value: 255 if value < 220 else 0) as ink,
                ):
                    bounds = ink.getbbox()
                if bounds is not None:
                    left, top, right, bottom = bounds
                    padding = 32
                    bounds = (
                        max(0, left - padding),
                        max(0, top - padding),
                        min(rgb.width, right + padding),
                        min(rgb.height, bottom + padding),
                    )
                    cropped_area = (bounds[2] - bounds[0]) * (bounds[3] - bounds[1])
                    if cropped_area < rgb.width * rgb.height * 0.8:
                        with rgb.crop(bounds) as cropped:
                            result = self.engine(np.asarray(cropped)[:, :, ::-1].copy())
                        offset_x, offset_y = bounds[:2]
        data = {
            key: []
            for key in (
                "text",
                "conf",
                "left",
                "top",
                "width",
                "height",
                "block_num",
                "par_num",
                "line_num",
                "polygons",
                "granularity",
            )
        }
        if result.txts is None or result.boxes is None or result.scores is None:
            return data
        if not len(result.txts) == len(result.boxes) == len(result.scores):
            raise OCRError("RapidOCR returned inconsistent text/box/score counts")
        for index, (text, polygon, score) in enumerate(
            zip(result.txts, result.boxes, result.scores)
        ):
            xs = [float(point[0]) + offset_x for point in polygon]
            ys = [float(point[1]) + offset_y for point in polygon]
            data["text"].append(text)
            data["conf"].append(float(score) * 100)
            data["left"].append(min(xs))
            data["top"].append(min(ys))
            data["width"].append(max(xs) - min(xs))
            data["height"].append(max(ys) - min(ys))
            data["block_num"].append(index)
            data["par_num"].append(0)
            data["line_num"].append(0)
            data["polygons"].append(list(zip(xs, ys)))
            data["granularity"].append("line")
        return data


def _rapid_model_paths(directory: Path) -> dict[str, Path]:
    """Resolve known PP-OCRv6 assets locally instead of invoking a downloader."""
    paths = {
        "Det": directory / "PP-OCRv6_det_small.onnx",
        "Rec": directory / "PP-OCRv6_rec_small.onnx",
        "Cls": directory / "ch_ppocr_mobile_v2.0_cls_mobile.onnx",
    }
    missing = [path.name for path in paths.values() if not path.is_file()]
    if missing:
        raise OCRDependencyError(
            "Missing local RapidOCR model assets: " + ", ".join(missing)
            + ". Install the supported RapidOCR wheel or provision ocr_model_dir; "
            "pdfstruct does not download missing model weights."
        )
    return paths


def _load_backend(options: OCROptions):
    if options.backend == "tesseract":
        return _TesseractBackend(options)
    return _RapidOCRBackend(options)


def apply_ocr(source: str | bytes, pages: list[RawPage], options: OCROptions) -> None:
    """Recognize selected regions and reconcile them with the native text layer."""
    if not options.mode:
        return
    selected = []
    for page in pages:
        regions = (
            [BBox(0, 0, page.width, page.height)] if options.mode == "always"
            else assess_ocr_regions(page, options.min_text_chars)
        )
        if regions:
            selected.append((page, regions))
    if not selected:
        return
    pdfium = _load_pdfium()
    backend = None
    try:
        with _PDFIUM_LOCK, pdfium.PdfDocument(source) as pdf:
            for raw_page, regions in selected:
                logger.info("Running OCR on page %d", raw_page.number)
                recognized: list[TextLine] = []
                attempted_regions: list[BBox] = []
                tesseract_deadline: float | None = None
                try:
                    with closing(pdf[raw_page.number - 1]) as page:
                        # pdfminer uses the full MediaBox, including its inherited
                        # offset and /Rotate. Normalize PDFium to that same space.
                        page.set_mediabox(*raw_page.mediabox)
                        page.set_cropbox(*raw_page.mediabox)
                        page.set_rotation(raw_page.rotation)
                        scale = options.dpi / 72.0
                        pixel_count = math.ceil(page.get_width() * scale) * math.ceil(
                            page.get_height() * scale
                        )
                        if pixel_count > MAX_RENDER_PIXELS:
                            raise OCRError(
                                f"Page {raw_page.number} exceeds the OCR render limit of "
                                f"{MAX_RENDER_PIXELS:,} pixels; lower ocr_dpi."
                            )
                        with closing(page.render(scale=scale)) as bitmap, bitmap.to_pil() as image:
                            # PDFium has been normalized to pdfminer's rotated
                            # MediaBox above. Pixel scale+Y-flip now maps into
                            # that same frame, including non-zero box origins.
                            for region in regions:
                                pixel_box = _region_pixels(region, image.size, raw_page)
                                with image.crop(pixel_box) as region_image:
                                    if not _has_visible_ink(region_image):
                                        continue
                                    if backend is None:
                                        backend = _load_backend(options)
                                    attempted_regions.append(region)
                                    region_options = options
                                    if options.backend == "tesseract":
                                        now = monotonic()
                                        if tesseract_deadline is None:
                                            tesseract_deadline = now + options.timeout
                                        remaining = tesseract_deadline - now
                                        if remaining <= 0:
                                            raise OCRError("Tesseract timeout: per-page OCR deadline exceeded")
                                        region_options = replace(options, timeout=remaining)
                                    data = backend.recognize(region_image, region_options)
                                    if (
                                        tesseract_deadline is not None
                                        and monotonic() >= tesseract_deadline
                                    ):
                                        raise OCRError("Tesseract timeout: per-page OCR deadline exceeded")
                                lines, _ = _data_to_lines(
                                    data, image.size, raw_page.width, raw_page.height,
                                    offset=pixel_box[:2],
                                )
                                recognized.extend(lines)
                                if not lines:
                                    raw_page.ocr_warnings.append(
                                        "OCR found no readable text in a selected raster region."
                                    )
                except OCRDependencyError:
                    raise
                except Exception as exc:
                    raise OCRError(f"OCR failed on page {raw_page.number}: {exc}") from exc
                raw_page.lines = _reconcile_lines(
                    raw_page.lines, recognized, force=options.mode == "always"
                )
                raw_page.characters = [
                    char for line in raw_page.lines for char in line.characters
                ]
                raw_page.ocr_applied = bool(attempted_regions)
                raw_page.ocr_confidence = _mean_confidence(recognized)
                raw_page.ocr_backend = options.backend if attempted_regions else None
                raw_page.ocr_regions = attempted_regions
                if not attempted_regions:
                    # A raster rectangle can simply be a blank scanned sheet.
                    raw_page.is_scanned = False
                if any(not _usable_text(line.text) for line in raw_page.lines):
                    raw_page.ocr_warnings.append(
                        "Unrecovered native text with a damaged character map remains; review it."
                    )
                if attempted_regions and not recognized:
                    logger.warning("OCR found no text on page %d", raw_page.number)
    except pdfium.PdfiumError as exc:
        raise OCRError(f"Unable to render PDF for OCR: {exc}") from exc


def _has_visible_ink(image) -> bool:
    """Skip uniform/white renderings without loading or calling an OCR engine."""
    with image.convert("L") as gray:
        low, high = gray.getextrema()
    return low < 250 and high - low >= 5


def _region_pixels(region: BBox, size: tuple[int, int], page: RawPage) -> tuple[int, ...]:
    """Crop in the rendered, rotated MediaBox frame; retain a small text border."""
    sx, sy = size[0] / page.width, size[1] / page.height
    padding = 3
    return (
        max(0, math.floor(region.x0 * sx) - padding),
        max(0, math.floor((page.height - region.y1) * sy) - padding),
        min(size[0], math.ceil(region.x1 * sx) + padding),
        min(size[1], math.ceil((page.height - region.y0) * sy) + padding),
    )


def _intersection_area(a: BBox, b: BBox) -> float:
    return max(0.0, min(a.x1, b.x1) - max(a.x0, b.x0)) * max(
        0.0, min(a.y1, b.y1) - max(a.y0, b.y0)
    )


def _normalized_text(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKC", text).casefold() if c.isalnum())


def _reconcile_lines(
    native: list[TextLine], recognized: list[TextLine], *, force: bool = False
) -> list[TextLine]:
    """Keep native text outside recovered content, deduplicating by text+position.

    Auto mode prefers a usable native layer. Force mode prefers recovered OCR
    at the same location, but never erases unmatched native text when an engine
    returns only a subset of a page (or nothing at all).
    """
    kept_native = list(native)
    accepted: list[TextLine] = []
    for candidate in recognized:
        duplicate = False
        replace = []
        new_text = _normalized_text(candidate.text)
        for old in kept_native + accepted:
            overlap = _intersection_area(old.bbox, candidate.bbox)
            if overlap <= 0 or min(old.bbox.area, candidate.bbox.area) <= 0:
                continue
            old_text = _normalized_text(old.text)
            similar = bool(old_text and new_text) and (
                new_text in old_text
                or SequenceMatcher(None, old_text, new_text).ratio() >= 0.75
            )
            old_coverage = overlap / old.bbox.area
            new_coverage = overlap / candidate.bbox.area
            if max(old_coverage, new_coverage) < 0.35:
                continue
            if old in accepted:
                if similar and new_coverage >= 0.5:
                    duplicate = True
                    break
                continue
            if (force or not _usable_text(old.text)) and old_coverage >= 0.6:
                replace.append(old)
            elif _usable_text(old.text) and (similar or new_coverage >= 0.7):
                duplicate = True
                break
        if not duplicate:
            for old in replace:
                kept_native.remove(old)
            accepted.append(candidate)
    return sorted(kept_native + accepted, key=lambda line: (-line.bbox.y1, line.bbox.x0))


def _mean_confidence(lines: list[TextLine]) -> float | None:
    scored = [line for line in lines if line.ocr_confidence is not None and line.text]
    count = sum(len(line.text.replace(" ", "")) for line in scored)
    return (
        sum(line.ocr_confidence * len(line.text.replace(" ", "")) for line in scored) / count
        if count else None
    )


def _data_to_lines(
    data: dict, image_size: tuple[int, int], page_width: float, page_height: float,
    *, offset: tuple[int, int] = (0, 0),
) -> tuple[list[TextLine], float | None]:
    """Convert Tesseract TSV words to lines and estimated character positions.

    Word boxes and confidence are measured by OCR. Character boxes and font
    sizes are estimates, not original PDF font/character metadata.
    """
    scale_x = page_width / image_size[0]
    scale_y = page_height / image_size[1]
    required = ("text", "conf", "left", "top", "width", "height", "block_num", "par_num", "line_num")
    if any(key not in data for key in required):
        raise OCRError("OCR backend returned incomplete position data")
    if any(len(data[key]) != len(data["text"]) for key in required):
        raise OCRError("OCR backend returned inconsistent text/position counts")
    for optional in ("polygons", "granularity"):
        if optional in data and len(data[optional]) != len(data["text"]):
            raise OCRError(f"OCR backend returned inconsistent {optional} counts")
    grouped: dict[tuple[int, int, int], list[OCRSpan]] = defaultdict(list)
    for index, value in enumerate(data["text"]):
        text = value.strip()
        confidence = float(data["conf"][index])
        if not text or not math.isfinite(confidence) or not 0 <= confidence <= 100:
            continue
        left = float(data["left"][index]) + offset[0]
        top = float(data["top"][index]) + offset[1]
        width, height = float(data["width"][index]), float(data["height"][index])
        if not all(math.isfinite(v) for v in (left, top, width, height)):
            continue
        if width <= 0 or height <= 0:
            continue
        bbox = BBox(
            max(0.0, left * scale_x),
            max(0.0, page_height - (top + height) * scale_y),
            min(page_width, (left + width) * scale_x),
            min(page_height, page_height - top * scale_y),
        )
        if bbox.width <= 0 or bbox.height <= 0:
            continue
        if "polygons" in data:
            points = data["polygons"][index]
            if len(points) < 3 or any(
                len(point) != 2 or not all(math.isfinite(float(v)) for v in point)
                for point in points
            ):
                raise OCRError("OCR backend returned an invalid text polygon")
            polygon = [
                ((float(x) + offset[0]) * scale_x,
                 page_height - (float(y) + offset[1]) * scale_y)
                for x, y in points
            ]
        else:
            polygon = [
                (bbox.x0, bbox.y1), (bbox.x1, bbox.y1),
                (bbox.x1, bbox.y0), (bbox.x0, bbox.y0),
            ]
        key = (data["block_num"][index], data["par_num"][index], data["line_num"][index])
        grouped[key].append(OCRSpan(
            text=text, bbox=bbox, confidence=confidence, polygon=polygon,
            level=data["granularity"][index] if "granularity" in data else "word",
        ))

    lines: list[TextLine] = []
    total_score = 0.0
    total_chars = 0
    for words in grouped.values():
        # Preserve Tesseract's word order (including right-to-left languages).
        bbox = words[0].bbox
        font = FontInfo(
            name="OCR", size=round(median(word.bbox.height for word in words), 1),
            is_estimated=True,
        )
        characters: list[Character] = []
        weighted_score = 0.0
        char_count = 0
        for index, word in enumerate(words):
            text, box, confidence = word.text, word.bbox, word.confidence
            bbox = bbox.merge(box)
            if index:
                previous = words[index - 1].bbox
                characters.append(
                    Character(
                        text=" ",
                        bbox=BBox(previous.x1, previous.y0, previous.x1, previous.y1),
                        font=font,
                        geometry_is_estimated=True,
                    )
                )
            char_width = box.width / len(text)
            for char_index, char in enumerate(text):
                characters.append(
                    Character(
                        text=char,
                        bbox=BBox(
                            box.x0 + char_index * char_width,
                            box.y0,
                            box.x0 + (char_index + 1) * char_width,
                            box.y1,
                        ),
                        font=font,
                        geometry_is_estimated=True,
                    )
                )
            weighted_score += confidence * len(text)
            char_count += len(text)
        lines.append(
            TextLine(
                text=" ".join(word.text for word in words),
                bbox=bbox,
                font=font,
                characters=characters,
                ocr_confidence=weighted_score / char_count,
                ocr_spans=words,
                source="ocr",
                geometry_is_estimated=True,
            )
        )
        total_score += weighted_score
        total_chars += char_count
    lines.sort(key=lambda line: (-line.bbox.y1, line.bbox.x0))
    return lines, total_score / total_chars if total_chars else None
