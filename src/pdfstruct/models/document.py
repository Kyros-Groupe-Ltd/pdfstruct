"""Core document models for pdfstruct.

These models represent the structured output of PDF parsing — from individual
characters up to the full document hierarchy.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from enum import Enum

from pdfstruct.models.metadata import DocumentMetadata


class HeadingLevel(Enum):
    """Heading hierarchy levels."""

    H1 = 1
    H2 = 2
    H3 = 3
    H4 = 4
    H5 = 5
    H6 = 6


class ListType(Enum):
    """Types of lists detected in documents."""

    BULLETED = "bulleted"
    NUMBERED = "numbered"
    LETTERED = "lettered"
    ROMAN = "roman"


@dataclass
class BBox:
    """Bounding box for any element on a page.

    Origin is bottom-left of the page (PDF coordinate system).
    """

    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    @property
    def midpoint(self) -> tuple[float, float]:
        return ((self.x0 + self.x1) / 2, (self.y0 + self.y1) / 2)

    @property
    def area(self) -> float:
        return self.width * self.height

    def overlaps(self, other: BBox, threshold: float = 0.0) -> bool:
        """Check if this box overlaps another by at least `threshold` fraction."""
        overlap_x = max(0, min(self.x1, other.x1) - max(self.x0, other.x0))
        overlap_y = max(0, min(self.y1, other.y1) - max(self.y0, other.y0))
        overlap_area = overlap_x * overlap_y
        if overlap_area == 0:
            return False
        min_area = min(self.area, other.area)
        if min_area == 0:
            return False
        return (overlap_area / min_area) >= threshold

    def contains(self, other: BBox) -> bool:
        """Check if this box fully contains another."""
        return (
            self.x0 <= other.x0
            and self.y0 <= other.y0
            and self.x1 >= other.x1
            and self.y1 >= other.y1
        )

    def merge(self, other: BBox) -> BBox:
        """Return a new BBox that encompasses both boxes."""
        return BBox(
            x0=min(self.x0, other.x0),
            y0=min(self.y0, other.y0),
            x1=max(self.x1, other.x1),
            y1=max(self.y1, other.y1),
        )

    def to_dict(self) -> dict:
        return {"x0": self.x0, "y0": self.y0, "x1": self.x1, "y1": self.y1}


@dataclass
class ImageInfo:
    """Rich metadata and optional data for an extracted PDF image.

    Provides image dimensions, format, DPI, colorspace, optional raw bytes,
    caption detection, and content-hash-based deduplication — exceeding what
    PyMuPDF offers.
    """

    bbox: BBox
    width_px: int = 0
    height_px: int = 0
    format: str = "unknown"  # jpeg, png, jbig2, ccitt, jpeg2000, raw
    colorspace: str = "unknown"  # rgb, cmyk, grayscale, indexed
    bits_per_component: int = 8
    dpi_x: float = 0.0
    dpi_y: float = 0.0
    has_mask: bool = False

    # Optional image data (only when extract_images=True with include_data=True)
    image_bytes: bytes | None = None
    file_size_bytes: int = 0
    content_hash: str | None = None  # SHA-256 truncated, for dedup

    # Context
    page_number: int = 0
    image_index: int = 0
    caption: str | None = None

    # Deduplication
    is_duplicate: bool = False
    duplicate_of_index: int | None = None

    # Error tracking
    extraction_error: str | None = None

    @property
    def dpi(self) -> float:
        """Average DPI."""
        if self.dpi_x and self.dpi_y:
            return (self.dpi_x + self.dpi_y) / 2
        return self.dpi_x or self.dpi_y

    @property
    def aspect_ratio(self) -> float:
        """Width/height ratio in pixels."""
        if self.height_px == 0:
            return 0.0
        return self.width_px / self.height_px

    def save(self, path: str, fmt: str | None = None) -> bool:
        """Save image data to disk.

        Args:
            path: output file path
            fmt: override output format (e.g. "PNG", "JPEG").
                 If None, writes raw bytes as-is.

        Returns:
            True if saved successfully, False otherwise.
        """
        if not self.image_bytes:
            return False
        try:
            if fmt:
                from PIL import Image as PILImage

                img = PILImage.open(io.BytesIO(self.image_bytes))
                if fmt.upper() == "JPEG" and img.mode in ("RGBA", "P"):
                    img = img.convert("RGB")
                img.save(path, format=fmt)
            else:
                with open(path, "wb") as f:
                    f.write(self.image_bytes)
            return True
        except (OSError, ValueError):
            return False

    def to_dict(self, include_bytes: bool = False) -> dict:
        """Serialize to a JSON-compatible dictionary."""
        import base64

        result = {
            "bbox": self.bbox.to_dict(),
            "width_px": self.width_px,
            "height_px": self.height_px,
            "format": self.format,
            "colorspace": self.colorspace,
            "bits_per_component": self.bits_per_component,
            "dpi_x": self.dpi_x,
            "dpi_y": self.dpi_y,
            "dpi": round(self.dpi, 1),
            "has_mask": self.has_mask,
            "file_size_bytes": self.file_size_bytes,
            "content_hash": self.content_hash,
            "page_number": self.page_number,
            "image_index": self.image_index,
            "aspect_ratio": round(self.aspect_ratio, 3),
        }
        if self.caption:
            result["caption"] = self.caption
        if self.is_duplicate:
            result["is_duplicate"] = True
            result["duplicate_of_index"] = self.duplicate_of_index
        if self.extraction_error:
            result["extraction_error"] = self.extraction_error
        if include_bytes and self.image_bytes:
            result["image_data_base64"] = base64.b64encode(self.image_bytes).decode("ascii")
        return result


@dataclass
class FontInfo:
    """Font information for a text element."""

    name: str
    size: float
    is_bold: bool = False
    is_italic: bool = False
    color: str | None = None
    is_estimated: bool = False

    @property
    def normalized_name(self) -> str:
        """Strip common font name suffixes to get the base family."""
        name = self.name
        for suffix in [
            "-Bold",
            "-Italic",
            "-BoldItalic",
            ",Bold",
            ",Italic",
            "-Regular",
            ",Regular",
            "-Roman",
        ]:
            name = name.replace(suffix, "")
        return name

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "size": round(self.size, 2),
            "is_bold": self.is_bold,
            "is_italic": self.is_italic,
            "is_estimated": self.is_estimated,
        }


@dataclass
class Character:
    """A single character with position and font information."""

    text: str
    bbox: BBox
    font: FontInfo
    geometry_is_estimated: bool = False


@dataclass
class OCRSpan:
    """An engine-measured word or line, in normalized PDF page coordinates.

    Confidence is an engine score on a 0–100 scale, not an accuracy probability.
    Keep the polygon when available instead of presenting interpolated character
    positions as measurements.
    """

    text: str
    bbox: BBox
    confidence: float | None = None
    polygon: list[tuple[float, float]] = field(default_factory=list)
    level: str = "word"

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "bbox": self.bbox.to_dict(),
            "polygon": [list(point) for point in self.polygon],
            "level": self.level,
            "confidence": self.confidence,
            "confidence_scale": "engine_score_0_100",
        }


@dataclass
class TextLine:
    """A line of text — a horizontal sequence of characters."""

    text: str
    bbox: BBox
    font: FontInfo
    characters: list[Character] = field(default_factory=list)
    is_heading: bool = False
    heading_level: HeadingLevel | None = None
    is_header: bool = False  # page header (repeating)
    is_footer: bool = False  # page footer (repeating)
    ocr_confidence: float | None = None  # engine score, 0-100; None for native text
    ocr_spans: list[OCRSpan] = field(default_factory=list)
    source: str = "native"
    geometry_is_estimated: bool = False
    page_number: int = 0
    element_id: str | None = None

    def to_dict(self) -> dict:
        result = {
            "text": self.text,
            "bbox": self.bbox.to_dict(),
            "font": self.font.to_dict(),
            "page": self.page_number,
            "source": self.source,
            "geometry_is_estimated": self.geometry_is_estimated,
        }
        if self.element_id:
            result["id"] = self.element_id
        if self.is_heading:
            result["is_heading"] = True
            result["heading_level"] = self.heading_level.value if self.heading_level else None
        if self.ocr_confidence is not None:
            result["ocr_confidence"] = round(self.ocr_confidence, 2)
        if self.ocr_spans:
            result["ocr_spans"] = [span.to_dict() for span in self.ocr_spans]
        return result


@dataclass
class ListItem:
    """An item in a bulleted or numbered list."""

    text: str
    marker: str  # "•", "1.", "a)", "i.", etc.
    level: int = 0  # nesting level (0 = top)
    bbox: BBox | None = None

    def to_dict(self) -> dict:
        result = {"marker": self.marker, "text": self.text, "level": self.level}
        if self.bbox:
            result["bbox"] = self.bbox.to_dict()
        return result


@dataclass
class Paragraph:
    """A paragraph — a logical block of text lines."""

    text: str
    lines: list[TextLine] = field(default_factory=list)
    bbox: BBox | None = None
    font: FontInfo | None = None
    is_heading: bool = False
    heading_level: HeadingLevel | None = None
    list_items: list[ListItem] | None = None
    list_type: ListType | None = None
    page_number: int = 0
    source: str = "native"
    element_id: str | None = None
    table_id: str | None = None

    def to_dict(self) -> dict:
        result = {"text": self.text, "page": self.page_number, "source": self.source}
        if self.element_id:
            result["id"] = self.element_id
        if self.table_id:
            result["table_id"] = self.table_id
        if self.bbox:
            result["bbox"] = self.bbox.to_dict()
        if self.lines:
            result["lines"] = [line.to_dict() for line in self.lines]
        if self.is_heading and self.heading_level:
            result["heading_level"] = self.heading_level.value
        if self.list_items:
            result["list_type"] = self.list_type.value if self.list_type else None
            result["list_items"] = [item.to_dict() for item in self.list_items]
        return result


@dataclass
class TableCell:
    """A single cell in a table."""

    text: str
    row_index: int
    col_index: int
    rowspan: int = 1
    colspan: int = 1
    bbox: BBox | None = None
    is_header: bool = False

    def to_dict(self) -> dict:
        result = {
            "text": self.text,
            "row": self.row_index,
            "col": self.col_index,
        }
        if self.rowspan > 1:
            result["rowspan"] = self.rowspan
        if self.colspan > 1:
            result["colspan"] = self.colspan
        if self.is_header:
            result["is_header"] = True
        if self.bbox:
            result["bbox"] = self.bbox.to_dict()
        return result


@dataclass
class TableRow:
    """A row in a table."""

    cells: list[TableCell] = field(default_factory=list)
    is_header: bool = False
    bbox: BBox | None = None

    def to_dict(self) -> dict:
        result = {
            "cells": [cell.to_dict() for cell in self.cells],
            "is_header": self.is_header,
        }
        if self.bbox:
            result["bbox"] = self.bbox.to_dict()
        return result


@dataclass
class Table:
    """A table detected in the document."""

    rows: list[TableRow] = field(default_factory=list)
    bbox: BBox | None = None
    page_number: int = 0
    caption: str | None = None
    element_id: str | None = None
    quality_flags: list[str] = field(default_factory=list)

    @property
    def num_rows(self) -> int:
        return max(
            len(self.rows),
            max(
                (cell.row_index + cell.rowspan for row in self.rows for cell in row.cells),
                default=0,
            ),
        )

    @property
    def num_cols(self) -> int:
        return max(
            (cell.col_index + cell.colspan for row in self.rows for cell in row.cells), default=0
        )

    @property
    def header_row(self) -> TableRow | None:
        for row in self.rows:
            if row.is_header:
                return row
        return None

    def to_list(self) -> list[list[str]]:
        """Return a rectangular grid; merged content appears only at its anchor.

        The full span information remains available in ``to_dict()``. Empty
        strings occupy covered positions instead of duplicating a merged value.
        """
        grid = [[""] * self.num_cols for _ in range(self.num_rows)]
        for row in self.rows:
            for cell in row.cells:
                grid[cell.row_index][cell.col_index] = cell.text
        return grid

    def to_dicts(self) -> list[dict[str, str]]:
        """Return rows without losing values to duplicate or empty header names."""
        grid = self.to_list()
        header = self.header_row
        if not header:
            return [{f"col_{i}": text for i, text in enumerate(row)} for row in grid]

        header_index = min((cell.row_index for cell in header.cells), default=0)
        labels = grid[header_index]
        keys: list[str] = []
        reserved = {label.strip() for label in labels if label.strip()}
        for i, label in enumerate(labels):
            base = label.strip() or f"col_{i}"
            key = base
            suffix = 2
            while key in keys or (key != label.strip() and key in reserved):
                key = f"{base}_{suffix}"
                suffix += 1
            keys.append(key)
        header_indices = {
            cell.row_index for row in self.rows if row.is_header for cell in row.cells
        }
        return [
            dict(zip(keys, values))
            for index, values in enumerate(grid)
            if index not in header_indices
        ]

    def to_dict(self) -> dict:
        result = {
            "rows": [row.to_dict() for row in self.rows],
            "num_rows": self.num_rows,
            "num_cols": self.num_cols,
            "page": self.page_number,
        }
        if self.caption:
            result["caption"] = self.caption
        if self.bbox:
            result["bbox"] = self.bbox.to_dict()
        if self.element_id:
            result["id"] = self.element_id
        if self.quality_flags:
            result["quality_flags"] = self.quality_flags
        return result


@dataclass
class Section:
    """A section of a document — a heading plus its content."""

    heading: str | None = None
    heading_level: HeadingLevel | None = None
    content: str = ""
    paragraphs: list[Paragraph] = field(default_factory=list)
    tables: list[Table] = field(default_factory=list)
    subsections: list[Section] = field(default_factory=list)
    page_number: int = 0
    page_end: int = 0
    heading_paragraph: Paragraph | None = None
    element_id: str | None = None
    blocks: list[Paragraph | Table] = field(default_factory=list, repr=False)

    def to_dict(self) -> dict:
        result = {}
        if self.heading:
            result["heading"] = self.heading
            result["level"] = self.heading_level.value if self.heading_level else None
        result["content"] = self.content
        if self.paragraphs:
            result["paragraphs"] = [p.to_dict() for p in self.paragraphs]
        if self.tables:
            result["tables"] = [t.to_dict() for t in self.tables]
        if self.subsections:
            result["subsections"] = [s.to_dict() for s in self.subsections]
        result["page"] = self.page_number
        result["page_start"] = self.page_number
        result["page_end"] = self.page_end or self.page_number
        if self.heading_paragraph:
            result["heading_source"] = self.heading_paragraph.to_dict()
        if self.element_id:
            result["id"] = self.element_id
        if self.blocks:
            result["block_order"] = [block.element_id for block in self.blocks]
        return result


@dataclass
class Page:
    """A single page of the document."""

    number: int
    width: float
    height: float
    lines: list[TextLine] = field(default_factory=list)
    paragraphs: list[Paragraph] = field(default_factory=list)
    tables: list[Table] = field(default_factory=list)
    images: list[ImageInfo] = field(default_factory=list)
    is_scanned: bool = False  # pre-OCR heuristic, retained after recognition
    ocr_applied: bool = False
    ocr_confidence: float | None = None
    ocr_backend: str | None = None
    ocr_warnings: list[str] = field(default_factory=list)
    ocr_regions: list[BBox] = field(default_factory=list)
    table_warnings: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(line.text for line in self.lines)

    def to_dict(self) -> dict:
        result = {
            "number": self.number,
            "width": round(self.width, 2),
            "height": round(self.height, 2),
            "text": self.text,
            "coordinate_system": "pdfminer_layout_bottom_left_points",
            "lines": [line.to_dict() for line in self.lines],
            "paragraphs": [p.to_dict() for p in self.paragraphs],
            "tables": [t.to_dict() for t in self.tables],
            "is_scanned": self.is_scanned,
            "ocr_applied": self.ocr_applied,
            "ocr_backend": self.ocr_backend,
            "ocr_warnings": self.ocr_warnings,
            "ocr_regions": [region.to_dict() for region in self.ocr_regions],
            "table_warnings": self.table_warnings,
            "ocr_confidence": (
                round(self.ocr_confidence, 2) if self.ocr_confidence is not None else None
            ),
        }
        if self.images:
            result["images"] = [img.to_dict() for img in self.images]
        return result


@dataclass
class Document:
    """The top-level parsed document."""

    title: str | None = None
    pages: list[Page] = field(default_factory=list)
    sections: list[Section] = field(default_factory=list)
    tables: list[Table] = field(default_factory=list)
    metadata: DocumentMetadata | None = None

    @property
    def text(self) -> str:
        """Full document text, concatenated from all pages."""
        return "\n\n".join(page.text for page in self.pages)

    @property
    def page_count(self) -> int:
        return len(self.pages)

    def to_dict(self, include_pages: bool = True) -> dict:
        result = {
            "title": self.title,
            "page_count": self.page_count,
            "text": self.text,
            "pages": [p.to_dict() for p in self.pages] if include_pages else [],
            "sections": [s.to_dict() for s in self.sections],
            "tables": [t.to_dict() for t in self.tables],
        }
        if self.metadata:
            result["metadata"] = self.metadata.to_dict()
        return result
