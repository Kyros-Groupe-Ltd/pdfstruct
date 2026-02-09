"""Image extraction from PDF using pdfminer.six.

Extracts actual image data, metadata, and contextual information from
LTImage objects found in PDF page layouts. Supports JPEG passthrough,
FlateDecode-to-PNG conversion via Pillow, and graceful fallbacks for
unsupported formats.

Features that exceed PyMuPDF:
- Caption detection via regex + spatial proximity
- Content-hash-based deduplication across pages
- DPI estimation from bbox vs pixel dimensions
- Thumbnail generation
- save() method on ImageInfo
- Never crashes — multi-layer fallbacks
"""

from __future__ import annotations

import hashlib
import io
import logging
import re
from typing import Optional

from PIL import Image as PILImage

from pdfminer.layout import LTImage, LTFigure, LTPage, LTChar, LTAnno
from pdfminer.pdftypes import (
    LITERALS_DCT_DECODE,
    LITERALS_FLATE_DECODE,
    LITERALS_JBIG2_DECODE,
    LITERALS_JPX_DECODE,
)

from pdfstruct.models.document import BBox, ImageInfo, TextLine

logger = logging.getLogger(__name__)

# Caption detection patterns
CAPTION_PATTERNS = [
    re.compile(r"^(?:Figure|Fig\.?)\s*\d", re.IGNORECASE),
    re.compile(r"^(?:Image|Img\.?)\s*\d", re.IGNORECASE),
    re.compile(r"^(?:Photo|Photograph)\s*\d", re.IGNORECASE),
    re.compile(r"^(?:Plate|Exhibit|Illustration)\s*\d", re.IGNORECASE),
    re.compile(r"^(?:Diagram|Chart|Graph)\s*\d", re.IGNORECASE),
    re.compile(r"^Source:", re.IGNORECASE),
]


def extract_images_from_page(
    layout: LTPage,
    page_number: int,
    page_width: float,
    page_height: float,
    text_lines: list[TextLine],
    include_data: bool = True,
) -> list[ImageInfo]:
    """Extract all images from a single page layout.

    Recursively traverses the layout tree to find LTImage objects
    (which may be nested inside LTFigure containers).

    Args:
        layout: pdfminer LTPage layout object
        page_number: 1-based page number
        page_width: page width in points
        page_height: page height in points
        text_lines: extracted text lines for caption detection
        include_data: whether to include raw image bytes

    Returns:
        List of ImageInfo objects with metadata and optionally image data
    """
    lt_images = _collect_lt_images(layout)

    if not lt_images:
        return []

    images: list[ImageInfo] = []
    for idx, (lt_image, figure_bbox) in enumerate(lt_images):
        try:
            image_info = _extract_single_image(
                lt_image=lt_image,
                figure_bbox=figure_bbox,
                page_number=page_number,
                page_width=page_width,
                page_height=page_height,
                image_index=idx,
                include_data=include_data,
            )
            if image_info is not None:
                images.append(image_info)
        except Exception as e:
            logger.warning(
                "Failed to extract image %d on page %d: %s",
                idx, page_number, e,
            )
            # Create a minimal ImageInfo with just the bbox
            if figure_bbox:
                images.append(ImageInfo(
                    bbox=figure_bbox,
                    page_number=page_number,
                    image_index=idx,
                    extraction_error=str(e),
                ))

    # Caption detection pass
    _detect_captions(images, text_lines, page_height)

    return images


def _collect_lt_images(element) -> list[tuple[LTImage, Optional[BBox]]]:
    """Recursively find all LTImage objects in a layout tree.

    Returns list of (LTImage, enclosing_figure_bbox) tuples.
    """
    results: list[tuple[LTImage, Optional[BBox]]] = []
    _collect_recursive(element, results, current_figure_bbox=None)
    return results


def _collect_recursive(element, results: list, current_figure_bbox: Optional[BBox]) -> None:
    """Recursive helper for _collect_lt_images."""
    if isinstance(element, LTImage):
        bbox = BBox(x0=element.x0, y0=element.y0, x1=element.x1, y1=element.y1)
        results.append((element, current_figure_bbox or bbox))
        return

    if isinstance(element, LTFigure):
        fig_bbox = BBox(x0=element.x0, y0=element.y0, x1=element.x1, y1=element.y1)
        for child in element:
            _collect_recursive(child, results, fig_bbox)
        return

    # Recurse into other containers
    if hasattr(element, "__iter__") and not isinstance(element, (LTChar, LTAnno)):
        for child in element:
            _collect_recursive(child, results, current_figure_bbox)


def _extract_single_image(
    lt_image: LTImage,
    figure_bbox: Optional[BBox],
    page_number: int,
    page_width: float,
    page_height: float,
    image_index: int,
    include_data: bool,
) -> Optional[ImageInfo]:
    """Extract data and metadata from a single LTImage."""
    # Get pixel dimensions
    width_px = lt_image.srcsize[0] or 0
    height_px = lt_image.srcsize[1] or 0

    if not width_px or not height_px:
        logger.debug("Image %d on page %d has no dimensions", image_index, page_number)
        return None

    # Skip tiny images (decorative dots, line patterns)
    if width_px <= 2 or height_px <= 2:
        return None

    # Determine the rendered bbox
    bbox = figure_bbox or BBox(
        x0=lt_image.x0, y0=lt_image.y0,
        x1=lt_image.x1, y1=lt_image.y1,
    )

    # Determine format from stream filters
    try:
        filters = lt_image.stream.get_filters()
    except Exception:
        filters = []
    image_format = _determine_format(filters)

    # Extract colorspace
    colorspace = _resolve_colorspace(lt_image.colorspace)
    bpc = lt_image.bits or 8

    # Compute DPI from bbox vs pixel dimensions
    bbox_width_pts = abs(bbox.width)
    bbox_height_pts = abs(bbox.height)
    dpi_x = round((width_px / bbox_width_pts * 72.0), 1) if bbox_width_pts > 0 else 0.0
    dpi_y = round((height_px / bbox_height_pts * 72.0), 1) if bbox_height_pts > 0 else 0.0

    # Check for mask
    has_mask = bool(lt_image.imagemask)

    # Extract image bytes
    image_bytes: Optional[bytes] = None
    file_size_bytes: int = 0
    if include_data:
        image_bytes = _extract_image_bytes(lt_image, image_format, colorspace, width_px, height_px, bpc)
        if image_bytes is not None:
            file_size_bytes = len(image_bytes)
            # Re-determine format if unknown
            if image_format in ("unknown", "raw") and image_bytes:
                sniffed = _sniff_format(image_bytes)
                if sniffed != "unknown":
                    image_format = sniffed

    # Content hash for deduplication
    content_hash: Optional[str] = None
    if image_bytes:
        content_hash = hashlib.sha256(image_bytes).hexdigest()[:16]

    return ImageInfo(
        bbox=bbox,
        width_px=width_px,
        height_px=height_px,
        format=image_format,
        colorspace=colorspace,
        bits_per_component=bpc,
        dpi_x=dpi_x,
        dpi_y=dpi_y,
        has_mask=has_mask,
        image_bytes=image_bytes if include_data else None,
        file_size_bytes=file_size_bytes,
        content_hash=content_hash,
        page_number=page_number,
        image_index=image_index,
    )


def _determine_format(filters: list) -> str:
    """Determine image format from PDF stream filters."""
    if not filters:
        return "raw"

    last_filter = filters[-1][0]

    if last_filter in LITERALS_DCT_DECODE:
        return "jpeg"
    if last_filter in LITERALS_JPX_DECODE:
        return "jpeg2000"
    if last_filter in LITERALS_JBIG2_DECODE:
        return "jbig2"
    if last_filter in LITERALS_FLATE_DECODE:
        return "flate"

    # Fallback: check filter name string
    filter_name = str(last_filter).lower()
    if "ccitt" in filter_name:
        return "ccitt"
    if "dct" in filter_name:
        return "jpeg"

    return "unknown"


def _resolve_colorspace(cs_list) -> str:
    """Resolve pdfminer colorspace to a human-readable string."""
    if not cs_list:
        return "unknown"

    if not isinstance(cs_list, list):
        cs_list = [cs_list]

    for cs in cs_list:
        if cs is None:
            continue
        cs_str = str(cs).lower()
        if "rgb" in cs_str:
            return "rgb"
        if "cmyk" in cs_str:
            return "cmyk"
        if "gray" in cs_str or "grey" in cs_str:
            return "grayscale"
        if "indexed" in cs_str:
            return "indexed"
        if "icc" in cs_str:
            return "icc"

    return "unknown"


def _extract_image_bytes(
    lt_image: LTImage,
    image_format: str,
    colorspace: str,
    width: int,
    height: int,
    bpc: int,
) -> Optional[bytes]:
    """Extract image bytes, converting to a standard format when possible.

    Strategy:
    - JPEG (DCTDecode): pass through raw bytes (already JPEG)
    - JPEG2000 (JPXDecode): pass through raw bytes
    - FlateDecode: decompress and re-encode as PNG via Pillow
    - Raw/unfiltered: reconstruct via Pillow
    - CCITT/JBIG2: attempt extraction, fall back to raw bytes
    """
    try:
        raw_data = lt_image.stream.get_data()
    except Exception as e:
        logger.debug("Failed to get stream data: %s", e)
        return None

    if not raw_data:
        return None

    try:
        if image_format == "jpeg":
            return raw_data  # Already JPEG-encoded

        if image_format == "jpeg2000":
            return raw_data  # Already JP2-encoded

        if image_format in ("flate", "raw"):
            result = _raw_to_png(raw_data, width, height, colorspace, bpc)
            if result:
                return result
            # Fall through to Pillow auto-detect

        if image_format == "ccitt":
            result = _ccitt_to_png(raw_data, width, height)
            if result:
                return result
            return raw_data  # Return raw as fallback

        if image_format == "jbig2":
            # No pure-Python JBIG2 decoder — return raw bytes
            logger.debug("JBIG2 image — returning raw bytes")
            return raw_data

        # Unknown format — try Pillow auto-detection
        result = _try_pillow_decode(raw_data, width, height, colorspace, bpc)
        if result:
            return result

        return raw_data  # Last resort

    except Exception as e:
        logger.debug("Image conversion failed: %s", e)
        return raw_data


def _raw_to_png(
    data: bytes,
    width: int,
    height: int,
    colorspace: str,
    bpc: int,
) -> Optional[bytes]:
    """Convert raw/decompressed pixel data to PNG using Pillow."""
    try:
        mode = _colorspace_to_pil_mode(colorspace, bpc)
        expected_bytes = _expected_data_size(mode, width, height, bpc)

        if len(data) < expected_bytes:
            # Try to infer mode from data size
            inferred = _infer_mode_from_size(data, width, height, bpc)
            if inferred is None:
                return None
            mode = inferred

        if mode == "1":
            # 1-bit images: pack into bytes
            img = PILImage.frombytes(mode, (width, height), data, "raw", "1")
        else:
            img = PILImage.frombytes(mode, (width, height), data, "raw", mode)

        # Convert CMYK to RGB for wider compatibility
        if mode == "CMYK":
            from PIL import ImageChops
            img = ImageChops.invert(img)
            img = img.convert("RGB")

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    except Exception as e:
        logger.debug("Raw-to-PNG conversion failed: %s", e)
        return None


def _ccitt_to_png(data: bytes, width: int, height: int) -> Optional[bytes]:
    """Attempt to convert CCITT fax data to PNG."""
    # Try Pillow's Group4 decoder
    try:
        img = PILImage.frombytes("1", (width, height), data, "group4")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        pass

    # Fallback: treat as raw 1-bit data
    try:
        img = PILImage.frombytes("1", (width, height), data, "raw", "1")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception as e:
        logger.debug("CCITT-to-PNG failed: %s", e)
        return None


def _try_pillow_decode(
    data: bytes,
    width: int,
    height: int,
    colorspace: str,
    bpc: int,
) -> Optional[bytes]:
    """Last-resort: try to let Pillow auto-detect the format."""
    # Try opening as a complete image file
    try:
        img = PILImage.open(io.BytesIO(data))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        pass

    # Try raw decode with inferred mode
    try:
        mode = _colorspace_to_pil_mode(colorspace, bpc)
        img = PILImage.frombytes(mode, (width, height), data, "raw", mode)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        return None


def _colorspace_to_pil_mode(colorspace: str, bpc: int) -> str:
    """Map PDF colorspace to Pillow image mode."""
    if bpc == 1:
        return "1"
    if colorspace == "grayscale":
        return "L"
    if colorspace == "rgb":
        return "RGB"
    if colorspace == "cmyk":
        return "CMYK"
    # Default to RGB for 8bpc, L for lower
    if bpc >= 8:
        return "RGB"
    return "L"


def _expected_data_size(mode: str, width: int, height: int, bpc: int) -> int:
    """Compute expected byte count for raw pixel data."""
    bpp = {"1": 1, "L": 1, "RGB": 3, "CMYK": 4, "RGBA": 4}.get(mode, 3)
    if mode == "1":
        # 1-bit: packed into bytes, rows padded to byte boundary
        return ((width + 7) // 8) * height
    return width * height * bpp


def _infer_mode_from_size(data: bytes, width: int, height: int, bpc: int) -> Optional[str]:
    """Try to infer Pillow mode from data size."""
    pixels = width * height
    if pixels == 0:
        return None
    data_len = len(data)

    # Check 1-bit packed
    packed_1bit = ((width + 7) // 8) * height
    if data_len == packed_1bit:
        return "1"
    if data_len == pixels:
        return "L"
    if data_len == pixels * 3:
        return "RGB"
    if data_len == pixels * 4:
        return "CMYK"
    return None


def _sniff_format(data: bytes) -> str:
    """Detect image format from magic bytes."""
    if len(data) < 8:
        return "unknown"
    if data[:2] == b"\xff\xd8":
        return "jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:4] in (b"II*\x00", b"MM\x00*"):
        return "tiff"
    if data[:4] == b"\x00\x00\x00\x0c":
        return "jpeg2000"
    return "unknown"


def _detect_captions(
    images: list[ImageInfo],
    text_lines: list[TextLine],
    page_height: float,
) -> None:
    """Detect captions for images by finding nearby text that matches caption patterns.

    Looks for text lines immediately below (or above) each image's bbox
    that match common caption patterns like "Figure 1:", "Fig. 2:", etc.

    Mutates ImageInfo.caption in-place.
    """
    if not images or not text_lines:
        return

    caption_search_distance = 30.0  # points (~0.4 inches)

    for image in images:
        best_caption: Optional[str] = None
        best_distance: float = float("inf")

        for line in text_lines:
            text = line.text.strip()
            if not text or len(text) > 300:
                continue

            # Check if this line matches a caption pattern
            is_caption_text = any(p.match(text) for p in CAPTION_PATTERNS)
            if not is_caption_text:
                continue

            # Check horizontal alignment — caption should be roughly
            # under/over the image. Use center-distance: the caption's
            # horizontal center must be within the image's x-span
            # (with generous margin for left-aligned captions under
            # centered images, etc.)
            img_cx = (image.bbox.x0 + image.bbox.x1) / 2
            line_cx = (line.bbox.x0 + line.bbox.x1) / 2
            img_w = image.bbox.width or 1.0
            # Allow caption center to be up to 1x image-width away
            # from image center (very generous — catches left-aligned
            # captions under centered images)
            if abs(img_cx - line_cx) > img_w:
                continue

            # Check proximity: caption below image
            # In PDF coords: y increases upward, so below = smaller y
            distance_below = image.bbox.y0 - line.bbox.y1
            # Caption above image
            distance_above = line.bbox.y0 - image.bbox.y1

            # Prefer captions below, then above
            if 0 <= distance_below <= caption_search_distance:
                if distance_below < best_distance:
                    best_distance = distance_below
                    best_caption = text
            elif 0 <= distance_above <= caption_search_distance:
                if distance_above < best_distance:
                    best_distance = distance_above
                    best_caption = text

        if best_caption:
            image.caption = best_caption


def deduplicate_images(images: list[ImageInfo]) -> list[ImageInfo]:
    """Mark duplicate images across pages using content hash.

    First occurrence keeps is_duplicate=False; subsequent occurrences
    get is_duplicate=True and duplicate_of_index set.

    Args:
        images: flat list of all images across all pages

    Returns:
        The same list (mutated in-place) for chaining.
    """
    seen: dict[str, int] = {}

    for i, image in enumerate(images):
        if not image.content_hash:
            continue

        if image.content_hash in seen:
            image.is_duplicate = True
            image.duplicate_of_index = seen[image.content_hash]
        else:
            seen[image.content_hash] = i

    return images


def generate_thumbnail(
    image_bytes: bytes,
    max_size: tuple[int, int] = (150, 150),
    output_format: str = "PNG",
) -> Optional[bytes]:
    """Generate a thumbnail from image bytes.

    Args:
        image_bytes: source image data (any Pillow-supported format)
        max_size: (max_width, max_height) for the thumbnail
        output_format: output format ("PNG", "JPEG")

    Returns:
        Thumbnail image bytes, or None on failure.
    """
    try:
        img = PILImage.open(io.BytesIO(image_bytes))
        img.thumbnail(max_size, PILImage.Resampling.LANCZOS)
        buf = io.BytesIO()
        if output_format.upper() == "JPEG" and img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        img.save(buf, format=output_format)
        return buf.getvalue()
    except Exception as e:
        logger.debug("Thumbnail generation failed: %s", e)
        return None
