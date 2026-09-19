"""Main PDF parser — the public API for pdfstruct.

Usage:
    import pdfstruct

    # Parse a PDF file
    doc = pdfstruct.parse("document.pdf")

    # Access structured content
    print(doc.title)
    for section in doc.sections:
        print(section.heading, section.content)

    # Get tables
    for table in doc.tables:
        print(table.to_dicts())

    # Export
    print(doc.to_dict())          # JSON-serializable dict
    print(pdfstruct.to_markdown(doc))  # Markdown string
"""

from __future__ import annotations

import logging
import os
from typing import BinaryIO, Optional

from pdfstruct.models.document import (
    Document, Page, Section, Paragraph, Table, TextLine, BBox, ImageInfo,
)
from pdfstruct.extractors.images import extract_images_from_page, deduplicate_images
from pdfstruct.extractors.ocr import OCROptions, OCRMode, apply_ocr, is_scanned_page
from pdfstruct.models.metadata import DocumentMetadata
from pdfstruct.extractors.text import (
    extract_pages_raw, extract_pdf_metadata, RawPage,
)
from pdfstruct.layout.analyzer import (
    group_lines_into_paragraphs, sort_lines_reading_order, detect_page_margins,
)
from pdfstruct.structure.headings import detect_headings
from pdfstruct.structure.headers_footers import (
    detect_headers_footers, filter_headers_footers,
)
from pdfstruct.structure.lists import detect_lists_in_paragraphs
from pdfstruct.structure.sections import build_sections
from pdfstruct.tables.detector import detect_tables
from pdfstruct.utils.fonts import compute_font_statistics
from pdfstruct.utils.language import detect_language
from pdfstruct.utils.geometry import find_columns

logger = logging.getLogger(__name__)


class PDFParser:
    """Configurable PDF parser.

    For most use cases, use the module-level `pdfstruct.parse()` function
    instead of instantiating this class directly.
    """

    def __init__(
        self,
        detect_tables_enabled: bool = True,
        detect_headers_footers_enabled: bool = True,
        detect_lists_enabled: bool = True,
        detect_columns_enabled: bool = True,
        extract_images: bool = False,
        extract_image_data: bool = True,
        line_margin: float = 0.5,
        word_margin: float = 0.1,
        char_margin: float = 2.0,
        paragraph_spacing_factor: float = 1.8,
        ocr: OCRMode = False,
        ocr_language: str = "eng",
        ocr_dpi: int = 300,
        ocr_min_text_chars: int = 50,
        ocr_timeout: float = 60.0,
        ocr_backend: str = "rapidocr",
    ):
        self.detect_tables_enabled = detect_tables_enabled
        self.detect_headers_footers_enabled = detect_headers_footers_enabled
        self.detect_lists_enabled = detect_lists_enabled
        self.detect_columns_enabled = detect_columns_enabled
        self.extract_images = extract_images
        self.extract_image_data = extract_image_data
        self.line_margin = line_margin
        self.word_margin = word_margin
        self.char_margin = char_margin
        self.paragraph_spacing_factor = paragraph_spacing_factor
        self.ocr_options = OCROptions(
            mode=ocr, language=ocr_language, dpi=ocr_dpi,
            min_text_chars=ocr_min_text_chars, timeout=ocr_timeout, backend=ocr_backend,
        )

    def parse(self, source: str | os.PathLike[str] | bytes | BinaryIO) -> Document:
        """Parse a PDF and return a structured Document.

        Args:
            source: file path, raw bytes, or file-like object

        Returns:
            Document with pages, sections, tables, and metadata
        """
        # Snapshot streams once so metadata, text and OCR read identical bytes.
        # Never close or leave a caller-owned seekable stream repositioned.
        source = _normalize_source(source)

        # Step 1: Extract PDF metadata
        logger.info("Extracting PDF metadata...")
        try:
            pdf_meta = extract_pdf_metadata(source)
        except Exception as e:
            logger.warning("Failed to extract PDF metadata: %s", e)
            pdf_meta = {}

        # Step 2: Extract raw page data (characters, lines, rects)
        logger.info("Extracting page content...")
        raw_pages: list[RawPage] = list(extract_pages_raw(
            source,
            line_margin=self.line_margin,
            word_margin=self.word_margin,
            char_margin=self.char_margin,
        ))

        if not raw_pages:
            return self._empty_document(pdf_meta)

        # Classify from native content before OCR changes the text statistics.
        for raw_page in raw_pages:
            raw_page.is_scanned = is_scanned_page(raw_page, self.ocr_options.min_text_chars)
        apply_ocr(source, raw_pages, self.ocr_options)

        # Step 3: Compute global font statistics
        all_lines: list[TextLine] = []
        for rp in raw_pages:
            all_lines.extend(rp.lines)

        font_stats = compute_font_statistics(all_lines)
        logger.info(
            "Font stats: primary=%s@%.1f, %d size tiers",
            font_stats["primary_font"],
            font_stats["primary_size"],
            len(font_stats["size_tiers"]),
        )

        # Step 4: Detect headers/footers across pages
        if self.detect_headers_footers_enabled and len(raw_pages) >= 3:
            pages_lines = [rp.lines for rp in raw_pages]
            page_heights = [rp.height for rp in raw_pages]
            detect_headers_footers(pages_lines, page_heights)

        # Step 5: Process each page
        pages: list[Page] = []
        all_paragraphs: list[Paragraph] = []
        all_tables: list[Table] = []

        for rp in raw_pages:
            page = self._process_page(rp, font_stats)
            pages.append(page)
            all_paragraphs.extend(page.paragraphs)
            all_tables.extend(page.tables)

        # Step 5.5: Extract images (opt-in)
        all_images: list[ImageInfo] = []
        if self.extract_images:
            logger.info("Extracting images...")
            for rp, page in zip(raw_pages, pages):
                if rp.page_layout is not None:
                    page_images = extract_images_from_page(
                        layout=rp.page_layout,
                        page_number=rp.number,
                        page_width=rp.width,
                        page_height=rp.height,
                        text_lines=page.lines,
                        include_data=self.extract_image_data,
                    )
                    page.images = page_images
                    all_images.extend(page_images)

            # Cross-page deduplication
            deduplicate_images(all_images)

            logger.info(
                "Extracted %d images (%d unique)",
                len(all_images),
                sum(1 for img in all_images if not img.is_duplicate),
            )

        # Step 6: Detect lists
        if self.detect_lists_enabled:
            detect_lists_in_paragraphs(all_paragraphs)

        # Step 7: Build section hierarchy
        sections = build_sections(all_paragraphs, all_tables)

        # Step 8: Detect title
        title = self._detect_title(pdf_meta, pages, all_paragraphs)

        # Step 9: Build metadata
        full_text = "\n\n".join(page.text for page in pages)
        metadata = self._build_metadata(pdf_meta, full_text, font_stats, pages, all_tables)

        doc = Document(
            title=title,
            pages=pages,
            sections=sections,
            tables=all_tables,
            metadata=metadata,
        )

        logger.info(
            "Parsed: %d pages, %d sections, %d tables, %d words",
            doc.page_count,
            len(doc.sections),
            len(doc.tables),
            metadata.word_count,
        )

        return doc

    def _process_page(self, raw_page: RawPage, font_stats: dict) -> Page:
        """Process a single raw page into a structured Page."""
        lines = raw_page.lines

        # Filter headers/footers
        content_lines = filter_headers_footers(lines)

        # Detect columns and sort reading order
        if self.detect_columns_enabled:
            columns = find_columns(
                [l.bbox for l in content_lines],
                raw_page.width,
            )
            content_lines = sort_lines_reading_order(
                content_lines, raw_page.width, columns
            )
        else:
            content_lines = sorted(content_lines, key=lambda l: -l.bbox.y1)

        # Detect headings
        detect_headings(content_lines, font_stats)

        # Group into paragraphs
        paragraphs = group_lines_into_paragraphs(
            content_lines,
            raw_page.width,
            line_spacing_factor=self.paragraph_spacing_factor,
        )

        # Detect tables
        tables: list[Table] = []
        if self.detect_tables_enabled:
            tables = detect_tables(
                content_lines,
                raw_page.rects,
                raw_page.width,
                raw_page.height,
            )
            for t in tables:
                t.page_number = raw_page.number

        return Page(
            number=raw_page.number,
            width=raw_page.width,
            height=raw_page.height,
            is_scanned=raw_page.is_scanned,
            ocr_applied=raw_page.ocr_applied,
            ocr_confidence=raw_page.ocr_confidence,
            ocr_backend=raw_page.ocr_backend,
            lines=content_lines,
            paragraphs=paragraphs,
            tables=tables,
            images=[
                ImageInfo(bbox=fig, page_number=raw_page.number, image_index=i)
                for i, fig in enumerate(raw_page.figures)
            ] if not self.extract_images else [],
        )

    def _detect_title(
        self,
        pdf_meta: dict,
        pages: list[Page],
        paragraphs: list[Paragraph],
    ) -> Optional[str]:
        """Detect the document title.

        Priority:
        1. PDF metadata title (if present and meaningful)
        2. First heading on first page (if it's H1)
        3. First line of first page (if short and not a sentence)
        """
        # Check PDF metadata
        meta_title = pdf_meta.get("title")
        if meta_title and len(meta_title.strip()) > 3 and meta_title.strip().lower() != "untitled":
            return meta_title.strip()

        # Check first heading
        for para in paragraphs[:5]:
            if para.is_heading and para.heading_level:
                if para.heading_level.value <= 2:
                    return para.text.strip()

        # Check first line heuristic
        if pages and pages[0].lines:
            first_line = pages[0].lines[0]
            text = first_line.text.strip()
            if (len(text) < 100
                    and not text.endswith((".", "!", "?"))
                    and first_line.font.size > 0):
                return text

        return None

    def _build_metadata(
        self,
        pdf_meta: dict,
        full_text: str,
        font_stats: dict,
        pages: list[Page],
        tables: list[Table],
    ) -> DocumentMetadata:
        """Build comprehensive document metadata."""
        words = full_text.split()
        word_count = len(words)

        # Count images across all pages
        image_count = sum(len(page.images) for page in pages)
        all_imgs = [img for page in pages for img in page.images]
        unique_image_count = sum(1 for img in all_imgs if not img.is_duplicate)
        duplicate_image_count = image_count - unique_image_count
        total_image_bytes = sum(img.file_size_bytes for img in all_imgs)
        image_formats = sorted(set(
            img.format for img in all_imgs if img.format not in ("unknown", "raw")
        ))

        # Preserve scan provenance even after OCR produces plenty of text.
        page_count = len(pages)
        scanned_page_count = sum(page.is_scanned for page in pages)
        ocr_page_count = sum(page.ocr_applied for page in pages)

        return DocumentMetadata(
            author=pdf_meta.get("author"),
            creator=pdf_meta.get("creator"),
            producer=pdf_meta.get("producer"),
            subject=pdf_meta.get("subject"),
            title=pdf_meta.get("title"),
            creation_date=pdf_meta.get("creation_date"),
            modification_date=pdf_meta.get("modification_date"),
            keywords=pdf_meta.get("keywords"),
            page_count=page_count,
            word_count=word_count,
            char_count=len(full_text),
            char_count_no_spaces=len(full_text.replace(" ", "").replace("\n", "")),
            paragraph_count=sum(len(p.paragraphs) for p in pages),
            line_count=sum(len(p.lines) for p in pages),
            table_count=len(tables),
            image_count=image_count,
            unique_image_count=unique_image_count,
            duplicate_image_count=duplicate_image_count,
            total_image_bytes=total_image_bytes,
            image_formats=image_formats,
            language=detect_language(full_text),
            reading_time_minutes=word_count / 200.0,
            page_width=pages[0].width if pages else 0,
            page_height=pages[0].height if pages else 0,
            fonts_used=list(font_stats["font_names"].keys())[:20],
            primary_font=font_stats["primary_font"],
            primary_font_size=font_stats["primary_size"],
            is_scanned=scanned_page_count > 0,
            scanned_page_count=scanned_page_count,
            ocr_applied=ocr_page_count > 0,
            ocr_page_count=ocr_page_count,
            has_tables=len(tables) > 0,
            has_images=image_count > 0,
        )

    def _empty_document(self, pdf_meta: dict) -> Document:
        """Return an empty document when no content could be extracted."""
        metadata = DocumentMetadata(
            author=pdf_meta.get("author"),
            title=pdf_meta.get("title"),
            page_count=pdf_meta.get("page_count", 0),
            is_scanned=False,
        )
        return Document(metadata=metadata)


# Module-level convenience function
_default_parser = PDFParser()


def _normalize_source(source: str | os.PathLike[str] | bytes | BinaryIO) -> str | bytes:
    if isinstance(source, os.PathLike):
        return os.fspath(source)
    if isinstance(source, (str, bytes)):
        return source
    position = None
    try:
        position = source.tell()
        source.seek(0)
    except (AttributeError, OSError):
        position = None
    try:
        data = source.read()
    finally:
        if position is not None:
            source.seek(position)
    if not isinstance(data, bytes):
        raise TypeError("source must be a PDF path, bytes, or a binary file-like object")
    return data


def parse(
    source: str | os.PathLike[str] | bytes | BinaryIO,
    detect_tables: bool = True,
    detect_headers_footers: bool = True,
    detect_lists: bool = True,
    detect_columns: bool = True,
    extract_images: bool = False,
    extract_image_data: bool = True,
    ocr: OCRMode = False,
    ocr_language: str = "eng",
    ocr_dpi: int = 300,
    ocr_min_text_chars: int = 50,
    ocr_timeout: float = 60.0,
    ocr_backend: str = "rapidocr",
) -> Document:
    """Parse a PDF and return a structured Document.

    This is the primary entry point for pdfstruct.

    Args:
        source: file path (str), raw PDF bytes, or file-like object
        detect_tables: enable table detection
        detect_headers_footers: enable header/footer removal
        detect_lists: enable list detection
        detect_columns: enable multi-column detection
        extract_images: enable full image extraction (opt-in, default False)
        extract_image_data: include raw image bytes (only if extract_images=True)
        ocr: False disables OCR (default); True/"auto" recognizes likely scanned
            pages; "always" replaces native text on every page with OCR
        ocr_language: Tesseract language codes, e.g. "eng" or "eng+fra";
            RapidOCR uses automatic multilingual recognition instead
        ocr_dpi: render resolution, 72-600 DPI (default 300)
        ocr_min_text_chars: native non-whitespace character threshold for auto OCR
        ocr_timeout: maximum Tesseract runtime per page, in seconds (default 60);
            does not apply to in-process RapidOCR inference
        ocr_backend: "rapidocr" (default, PP-OCRv6/ONNX) or "tesseract"

    Raises:
        OCRDependencyError: a selected OCR page needs missing dependencies
        OCRError: page rendering, recognition, or the OCR timeout failed

    Returns:
        Document with pages, sections, tables, and metadata

    Example:
        >>> import pdfstruct
        >>> doc = pdfstruct.parse("contract.pdf")
        >>> print(doc.title)
        >>> for section in doc.sections:
        ...     print(section.heading, section.content[:100])
        >>> for table in doc.tables:
        ...     print(table.to_dicts())
        >>> # With image extraction
        >>> doc = pdfstruct.parse("report.pdf", extract_images=True)
        >>> for page in doc.pages:
        ...     for img in page.images:
        ...         print(img.format, img.width_px, img.dpi)
        ...         img.save(f"img_{img.page_number}_{img.image_index}.png")
    """
    parser = PDFParser(
        detect_tables_enabled=detect_tables,
        detect_headers_footers_enabled=detect_headers_footers,
        detect_lists_enabled=detect_lists,
        detect_columns_enabled=detect_columns,
        extract_images=extract_images,
        extract_image_data=extract_image_data,
        ocr=ocr,
        ocr_language=ocr_language,
        ocr_dpi=ocr_dpi,
        ocr_min_text_chars=ocr_min_text_chars,
        ocr_timeout=ocr_timeout,
        ocr_backend=ocr_backend,
    )
    return parser.parse(source)
