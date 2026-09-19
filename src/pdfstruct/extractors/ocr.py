"""Optional, local OCR for scanned PDF pages.

PDFium renders one page at a time and an OCR backend supplies text boxes. Those
boxes are converted to pdfstruct's bottom-left PDF coordinates before the
normal layout/structure pipeline runs. No OCR dependencies are imported until
a page actually needs OCR, and no document data is sent to a service.
"""

from __future__ import annotations

import logging
import math
import re
import threading
from collections import defaultdict
from contextlib import closing
from dataclasses import dataclass
from itertools import pairwise
from statistics import median
from typing import Literal

from pdfminer.layout import LTImage

from pdfstruct.extractors.text import RawPage
from pdfstruct.models.document import BBox, Character, FontInfo, TextLine

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


def is_scanned_page(page: RawPage, min_text_chars: int = 50) -> bool:
    """Heuristic: sparse native text and raster images covering half the page.

    Blank pages, short digital pages, small logos and vector-only figures do
    not trigger OCR. A searchable scan with sufficient native text is reused.
    Use ``ocr='always'`` for outlined text or a damaged/existing OCR layer.
    """
    char_count = sum(not char.isspace() for line in page.lines for char in line.text)
    if char_count >= min_text_chars and char_count > 0:
        return False
    if page.page_layout is None or page.width <= 0 or page.height <= 0:
        return False

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
    return area >= page.width * page.height * 0.5


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
    def __init__(self):
        try:
            import pytesseract
        except ImportError as exc:
            raise OCRDependencyError('Install "pdfstructx[tesseract]" for Tesseract OCR.') from exc
        self.engine = pytesseract

    def recognize(self, image, options: OCROptions) -> dict:
        try:
            return self.engine.image_to_data(
                image,
                lang=options.language,
                config=f"--dpi {options.dpi}",
                output_type=self.engine.Output.DICT,
                timeout=options.timeout,
            )
        except self.engine.TesseractNotFoundError as exc:
            raise OCRDependencyError(
                "Tesseract was not found. Install tesseract-ocr and the language "
                "data you need, and make sure 'tesseract' is on PATH."
            ) from exc


class _RapidOCRBackend:
    def __init__(self):
        try:
            import onnxruntime  # noqa: F401 - fail early if the runtime is missing
            from rapidocr import ModelType, OCRVersion, RapidOCR
        except ImportError as exc:
            raise OCRDependencyError('Install "pdfstructx[ocr]" for RapidOCR.') from exc
        # Select the model family explicitly rather than inheriting changing
        # upstream defaults. V6 recognizes Chinese, English and Latin languages
        # together; the v4 classifier corrects upside-down text-line crops.
        self.engine = RapidOCR(
            params={
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
            }
        )

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
        return data


def _load_backend(options: OCROptions):
    if options.backend == "tesseract":
        return _TesseractBackend()
    return _RapidOCRBackend()


def apply_ocr(source: str | bytes, pages: list[RawPage], options: OCROptions) -> None:
    """Replace selected pages' text with OCR, preserving their original layout."""
    selected = [page for page in pages if options.mode == "always" or page.is_scanned]
    if not options.mode or not selected:
        return
    pdfium = _load_pdfium()
    try:
        backend = _load_backend(options)
    except OCRDependencyError:
        raise
    except Exception as exc:
        raise OCRError(f"Unable to initialize {options.backend} OCR: {exc}") from exc
    try:
        with _PDFIUM_LOCK, pdfium.PdfDocument(source) as pdf:
            for raw_page in selected:
                logger.info("Running OCR on page %d", raw_page.number)
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
                            data = backend.recognize(image, options)
                            lines, confidence = _data_to_lines(
                                data, image.size, raw_page.width, raw_page.height
                            )
                except OCRDependencyError:
                    raise
                except Exception as exc:
                    raise OCRError(f"OCR failed on page {raw_page.number}: {exc}") from exc
                raw_page.lines = lines
                raw_page.characters = [char for line in lines for char in line.characters]
                raw_page.ocr_applied = True
                raw_page.ocr_confidence = confidence
                raw_page.ocr_backend = options.backend
                if not lines:
                    logger.warning("OCR found no text on page %d", raw_page.number)
    except pdfium.PdfiumError as exc:
        raise OCRError(f"Unable to render PDF for OCR: {exc}") from exc


def _data_to_lines(
    data: dict, image_size: tuple[int, int], page_width: float, page_height: float
) -> tuple[list[TextLine], float | None]:
    """Convert Tesseract TSV words to lines and estimated character positions.

    Word boxes and confidence are measured by OCR. Character boxes and font
    sizes are estimates, not original PDF font/character metadata.
    """
    scale_x = page_width / image_size[0]
    scale_y = page_height / image_size[1]
    grouped: dict[tuple[int, int, int], list[tuple[str, BBox, float]]] = defaultdict(list)
    for index, value in enumerate(data["text"]):
        text = value.strip()
        confidence = float(data["conf"][index])
        if not text or not math.isfinite(confidence) or not 0 <= confidence <= 100:
            continue
        left, top = float(data["left"][index]), float(data["top"][index])
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
        key = (data["block_num"][index], data["par_num"][index], data["line_num"][index])
        grouped[key].append((text, bbox, confidence))

    lines: list[TextLine] = []
    total_score = 0.0
    total_chars = 0
    for words in grouped.values():
        # Preserve Tesseract's word order (including right-to-left languages).
        bbox = words[0][1]
        font = FontInfo(name="OCR", size=round(median(box.height for _, box, _ in words), 1))
        characters: list[Character] = []
        weighted_score = 0.0
        char_count = 0
        for index, (text, box, confidence) in enumerate(words):
            bbox = bbox.merge(box)
            if index:
                previous = words[index - 1][1]
                characters.append(
                    Character(
                        text=" ",
                        bbox=BBox(previous.x1, previous.y0, previous.x1, previous.y1),
                        font=font,
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
                    )
                )
            weighted_score += confidence * len(text)
            char_count += len(text)
        lines.append(
            TextLine(
                text=" ".join(word[0] for word in words),
                bbox=bbox,
                font=font,
                characters=characters,
                ocr_confidence=weighted_score / char_count,
            )
        )
        total_score += weighted_score
        total_chars += char_count
    lines.sort(key=lambda line: (-line.bbox.y1, line.bbox.x0))
    return lines, total_score / total_chars if total_chars else None
