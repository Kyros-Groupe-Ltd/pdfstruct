"""Document metadata models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class DocumentMetadata:
    """Metadata extracted from a PDF document."""

    # PDF info dict
    author: Optional[str] = None
    creator: Optional[str] = None
    producer: Optional[str] = None
    subject: Optional[str] = None
    title: Optional[str] = None
    creation_date: Optional[str] = None
    modification_date: Optional[str] = None
    keywords: Optional[str] = None

    # Computed stats
    page_count: int = 0
    word_count: int = 0
    char_count: int = 0
    char_count_no_spaces: int = 0
    paragraph_count: int = 0
    line_count: int = 0
    table_count: int = 0
    image_count: int = 0
    unique_image_count: int = 0
    duplicate_image_count: int = 0
    total_image_bytes: int = 0
    image_formats: list[str] = field(default_factory=list)

    # Language
    language: Optional[str] = None

    # Reading time
    reading_time_minutes: float = 0.0

    # Page dimensions (of first page)
    page_width: float = 0.0
    page_height: float = 0.0

    # Font statistics
    fonts_used: list[str] = field(default_factory=list)
    primary_font: Optional[str] = None
    primary_font_size: float = 0.0

    # Flags
    is_scanned: bool = False
    has_tables: bool = False
    has_images: bool = False
    is_encrypted: bool = False
    ocr_applied: bool = False
    scanned_page_count: int = 0
    ocr_page_count: int = 0

    def to_dict(self) -> dict:
        return {
            "pdf_info": {
                "author": self.author,
                "creator": self.creator,
                "producer": self.producer,
                "subject": self.subject,
                "title": self.title,
                "creation_date": self.creation_date,
                "modification_date": self.modification_date,
            },
            "statistics": {
                "page_count": self.page_count,
                "scanned_page_count": self.scanned_page_count,
                "ocr_page_count": self.ocr_page_count,
                "word_count": self.word_count,
                "char_count": self.char_count,
                "char_count_no_spaces": self.char_count_no_spaces,
                "paragraph_count": self.paragraph_count,
                "line_count": self.line_count,
                "table_count": self.table_count,
                "image_count": self.image_count,
                "unique_image_count": self.unique_image_count,
                "duplicate_image_count": self.duplicate_image_count,
                "total_image_bytes": self.total_image_bytes,
                "image_formats": self.image_formats,
                "reading_time_minutes": round(self.reading_time_minutes, 1),
            },
            "language": self.language,
            "fonts": {
                "primary_font": self.primary_font,
                "primary_font_size": round(self.primary_font_size, 2),
                "fonts_used": self.fonts_used,
            },
            "flags": {
                "is_scanned": self.is_scanned,
                "ocr_applied": self.ocr_applied,
                "has_tables": self.has_tables,
                "has_images": self.has_images,
                "is_encrypted": self.is_encrypted,
            },
        }
