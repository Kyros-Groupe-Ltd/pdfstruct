"""Core document models for pdfstruct.

These models represent the structured output of PDF parsing — from individual
characters up to the full document hierarchy.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


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
        return (self.x0 <= other.x0 and self.y0 <= other.y0
                and self.x1 >= other.x1 and self.y1 >= other.y1)

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
    format: str = "unknown"           # jpeg, png, jbig2, ccitt, jpeg2000, raw
    colorspace: str = "unknown"       # rgb, cmyk, grayscale, indexed
    bits_per_component: int = 8
    dpi_x: float = 0.0
    dpi_y: float = 0.0
    has_mask: bool = False

    # Optional image data (only when extract_images=True with include_data=True)
    image_bytes: Optional[bytes] = None
    file_size_bytes: int = 0
    content_hash: Optional[str] = None  # SHA-256 truncated, for dedup

    # Context
    page_number: int = 0
    image_index: int = 0
    caption: Optional[str] = None

    # Deduplication
    is_duplicate: bool = False
    duplicate_of_index: Optional[int] = None

    # Error tracking
    extraction_error: Optional[str] = None

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

    def save(self, path: str, fmt: Optional[str] = None) -> bool:
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
        except Exception:
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
    color: Optional[str] = None

    @property
    def normalized_name(self) -> str:
        """Strip common font name suffixes to get the base family."""
        name = self.name
        for suffix in ["-Bold", "-Italic", "-BoldItalic", ",Bold", ",Italic",
                       "-Regular", ",Regular", "-Roman"]:
            name = name.replace(suffix, "")
        return name

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "size": round(self.size, 2),
            "is_bold": self.is_bold,
            "is_italic": self.is_italic,
        }


@dataclass
class Character:
    """A single character with position and font information."""
    text: str
    bbox: BBox
    font: FontInfo


@dataclass
class TextLine:
    """A line of text — a horizontal sequence of characters."""
    text: str
    bbox: BBox
    font: FontInfo
    characters: list[Character] = field(default_factory=list)
    is_heading: bool = False
    heading_level: Optional[HeadingLevel] = None
    is_header: bool = False  # page header (repeating)
    is_footer: bool = False  # page footer (repeating)

    def to_dict(self) -> dict:
        result = {
            "text": self.text,
            "bbox": self.bbox.to_dict(),
            "font": self.font.to_dict(),
        }
        if self.is_heading:
            result["is_heading"] = True
            result["heading_level"] = self.heading_level.value if self.heading_level else None
        return result


@dataclass
class ListItem:
    """An item in a bulleted or numbered list."""
    text: str
    marker: str  # "•", "1.", "a)", "i.", etc.
    level: int = 0  # nesting level (0 = top)
    bbox: Optional[BBox] = None

    def to_dict(self) -> dict:
        return {"marker": self.marker, "text": self.text, "level": self.level}


@dataclass
class Paragraph:
    """A paragraph — a logical block of text lines."""
    text: str
    lines: list[TextLine] = field(default_factory=list)
    bbox: Optional[BBox] = None
    font: Optional[FontInfo] = None
    is_heading: bool = False
    heading_level: Optional[HeadingLevel] = None
    list_items: Optional[list[ListItem]] = None
    list_type: Optional[ListType] = None

    def to_dict(self) -> dict:
        result = {"text": self.text}
        if self.bbox:
            result["bbox"] = self.bbox.to_dict()
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
    bbox: Optional[BBox] = None
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
        return result


@dataclass
class TableRow:
    """A row in a table."""
    cells: list[TableCell] = field(default_factory=list)
    is_header: bool = False
    bbox: Optional[BBox] = None

    def to_dict(self) -> dict:
        return {
            "cells": [cell.to_dict() for cell in self.cells],
            "is_header": self.is_header,
        }


@dataclass
class Table:
    """A table detected in the document."""
    rows: list[TableRow] = field(default_factory=list)
    bbox: Optional[BBox] = None
    page_number: int = 0
    caption: Optional[str] = None

    @property
    def num_rows(self) -> int:
        return len(self.rows)

    @property
    def num_cols(self) -> int:
        if not self.rows:
            return 0
        return max(len(row.cells) for row in self.rows)

    @property
    def header_row(self) -> Optional[TableRow]:
        for row in self.rows:
            if row.is_header:
                return row
        return None

    def to_list(self) -> list[list[str]]:
        """Convert to a 2D list of strings."""
        return [[cell.text for cell in row.cells] for row in self.rows]

    def to_dicts(self) -> list[dict[str, str]]:
        """Convert to a list of dicts using header row as keys."""
        header = self.header_row
        if not header:
            return [{"col_" + str(i): cell.text for i, cell in enumerate(row.cells)}
                    for row in self.rows]

        keys = [cell.text for cell in header.cells]
        return [
            {keys[i] if i < len(keys) else f"col_{i}": cell.text
             for i, cell in enumerate(row.cells)}
            for row in self.rows if not row.is_header
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
        return result


@dataclass
class Section:
    """A section of a document — a heading plus its content."""
    heading: Optional[str] = None
    heading_level: Optional[HeadingLevel] = None
    content: str = ""
    paragraphs: list[Paragraph] = field(default_factory=list)
    tables: list[Table] = field(default_factory=list)
    subsections: list[Section] = field(default_factory=list)
    page_number: int = 0

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

    @property
    def text(self) -> str:
        return "\n".join(line.text for line in self.lines)

    def to_dict(self) -> dict:
        result = {
            "number": self.number,
            "width": round(self.width, 2),
            "height": round(self.height, 2),
            "text": self.text,
            "paragraphs": [p.to_dict() for p in self.paragraphs],
            "tables": [t.to_dict() for t in self.tables],
        }
        if self.images:
            result["images"] = [img.to_dict() for img in self.images]
        return result


@dataclass
class Document:
    """The top-level parsed document."""
    title: Optional[str] = None
    pages: list[Page] = field(default_factory=list)
    sections: list[Section] = field(default_factory=list)
    tables: list[Table] = field(default_factory=list)
    metadata: Optional[DocumentMetadata] = None

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
