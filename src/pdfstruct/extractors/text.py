"""Core text extraction from PDF using pdfminer.six.

This module extracts characters with their positions, font information,
and page dimensions — the raw building blocks for all higher-level analysis.
"""

from __future__ import annotations

import logging
from io import BytesIO
from typing import BinaryIO, Iterator

from pdfminer.converter import PDFPageAggregator
from pdfminer.pdfinterp import PDFPageInterpreter, PDFResourceManager
from pdfminer.layout import (
    LAParams,
    LTAnno,
    LTChar,
    LTFigure,
    LTLine,
    LTRect,
    LTCurve,
    LTPage,
    LTTextBox,
    LTTextLine,
)
from pdfminer.pdfpage import PDFPage
from pdfminer.pdfparser import PDFParser
from pdfminer.pdfdocument import PDFDocument

from pdfstruct.models.document import BBox, Character, FontInfo, TextLine
from pdfstruct.utils.fonts import parse_font_info

logger = logging.getLogger(__name__)


class RawPage:
    """Raw extraction result for a single page."""

    def __init__(self, number: int, width: float, height: float):
        self.number = number
        self.width = width
        self.height = height
        self.characters: list[Character] = []
        self.lines: list[TextLine] = []
        self.rects: list[BBox] = []  # rectangles/lines (for table detection)
        self.curves: list[BBox] = []
        self.figures: list[BBox] = []  # embedded figures/images
        self.page_layout: LTPage | None = None  # preserved for image extraction
        self.mediabox: tuple[float, float, float, float] = (0, 0, width, height)
        self.rotation: int = 0
        self.is_scanned: bool = False
        self.ocr_applied: bool = False
        self.ocr_confidence: float | None = None
        self.ocr_backend: str | None = None


def extract_from_page(layout: LTPage) -> tuple[list[TextLine], list[Character], list[BBox], list[BBox]]:
    """Extract text lines, characters, rectangles, and figures from a page layout.

    Uses pdfminer's built-in LTTextLine objects for proper word spacing,
    rather than reconstructing lines from individual characters.

    Returns:
        (text_lines, characters, rects, figures)
    """
    text_lines: list[TextLine] = []
    characters: list[Character] = []
    rects: list[BBox] = []
    figures: list[BBox] = []

    def _extract_line_chars(text_line_element: LTTextLine) -> tuple[str, list[Character], FontInfo]:
        """Extract characters from an LTTextLine, preserving pdfminer's spacing."""
        line_chars: list[Character] = []
        for element in text_line_element:
            if isinstance(element, LTChar):
                bbox = BBox(x0=element.x0, y0=element.y0, x1=element.x1, y1=element.y1)
                font = parse_font_info(element.fontname, element.size)
                line_chars.append(Character(text=element.get_text(), bbox=bbox, font=font))
            elif isinstance(element, LTAnno):
                # LTAnno includes inferred characters like spaces — keep them for text
                if line_chars:
                    last = line_chars[-1]
                    bbox = BBox(
                        x0=last.bbox.x1, y0=last.bbox.y0,
                        x1=last.bbox.x1, y1=last.bbox.y1,
                    )
                    font = last.font
                else:
                    bbox = BBox(x0=0, y0=0, x1=0, y1=0)
                    font = FontInfo(name="unknown", size=10.0)
                line_chars.append(Character(text=element.get_text(), bbox=bbox, font=font))

        # Use pdfminer's get_text() for the line — it has proper spacing
        text = text_line_element.get_text().strip()

        # Determine dominant font
        visible = [c for c in line_chars if c.text.strip()]
        if visible:
            from collections import Counter
            font_counts: Counter[str] = Counter()
            font_map: dict[str, FontInfo] = {}
            for c in visible:
                key = f"{c.font.name}|{c.font.size:.1f}"
                font_counts[key] += 1
                font_map[key] = c.font
            dominant_key = font_counts.most_common(1)[0][0]
            font = font_map[dominant_key]
        else:
            font = FontInfo(name="unknown", size=10.0)

        return text, line_chars, font

    def _traverse(element):
        if isinstance(element, LTTextLine):
            text, line_chars, font = _extract_line_chars(element)
            if text:
                bbox = BBox(x0=element.x0, y0=element.y0, x1=element.x1, y1=element.y1)
                text_lines.append(TextLine(
                    text=text,
                    bbox=bbox,
                    font=font,
                    characters=[c for c in line_chars if c.text.strip()],
                ))
                characters.extend(line_chars)

        elif isinstance(element, (LTRect, LTLine)):
            rects.append(BBox(x0=element.x0, y0=element.y0, x1=element.x1, y1=element.y1))

        elif isinstance(element, LTCurve):
            rects.append(BBox(x0=element.x0, y0=element.y0, x1=element.x1, y1=element.y1))

        elif isinstance(element, LTFigure):
            figures.append(BBox(x0=element.x0, y0=element.y0, x1=element.x1, y1=element.y1))
            for child in element:
                _traverse(child)

        elif isinstance(element, LTTextBox):
            # Recurse into text boxes to get individual lines
            for child in element:
                _traverse(child)

        elif hasattr(element, "__iter__") and not isinstance(element, (LTChar, LTAnno)):
            for child in element:
                _traverse(child)

    _traverse(layout)

    # Sort lines top-to-bottom (descending y in PDF coords)
    text_lines.sort(key=lambda l: -l.bbox.y1)

    return text_lines, characters, rects, figures


def extract_pages_raw(
    source: str | bytes | BinaryIO,
    line_margin: float = 0.5,
    word_margin: float = 0.1,
    char_margin: float = 2.0,
    boxes_flow: float | None = 0.5,
) -> Iterator[RawPage]:
    """Extract raw page data from a PDF.

    This is the primary extraction function. It yields RawPage objects
    containing characters, lines, rectangles, and figures for each page.

    Args:
        source: file path, bytes, or file-like object
        line_margin: pdfminer LAParams line_margin
        word_margin: pdfminer LAParams word_margin
        char_margin: pdfminer LAParams char_margin
        boxes_flow: pdfminer LAParams boxes_flow (None for no layout analysis)
    """
    if isinstance(source, str):
        fp = open(source, "rb")
        should_close = True
    elif isinstance(source, bytes):
        fp = BytesIO(source)
        should_close = True
    else:
        fp = source
        should_close = False

    try:
        laparams = LAParams(
            line_margin=line_margin,
            word_margin=word_margin,
            char_margin=char_margin,
            boxes_flow=boxes_flow,
        )

        # Match pdfminer's high-level extraction while retaining inherited page
        # geometry. OCR must render the same rotated MediaBox, not the CropBox.
        manager = PDFResourceManager()
        device = PDFPageAggregator(manager, laparams=laparams)
        interpreter = PDFPageInterpreter(manager, device)
        for page_num, pdf_page in enumerate(PDFPage.get_pages(fp), start=1):
            interpreter.process_page(pdf_page)
            page_layout = device.get_result()
            raw_page = RawPage(
                number=page_num,
                width=page_layout.width,
                height=page_layout.height,
            )
            raw_page.mediabox = tuple(pdf_page.mediabox)
            raw_page.rotation = pdf_page.rotate

            lines, chars, rects, figures = extract_from_page(page_layout)
            raw_page.characters = chars
            raw_page.lines = lines
            raw_page.rects = rects
            raw_page.figures = figures
            raw_page.page_layout = page_layout  # preserve for image extraction

            logger.debug(
                "Page %d: %d chars, %d lines, %d rects, %d figures",
                page_num, len(chars), len(raw_page.lines), len(rects), len(figures),
            )

            yield raw_page
    finally:
        if should_close:
            fp.close()


def extract_pdf_metadata(source: str | bytes | BinaryIO) -> dict:
    """Extract PDF document-level metadata (info dict).

    Returns dict with keys: title, author, subject, creator, producer,
    creation_date, modification_date, keywords.
    """
    if isinstance(source, str):
        fp = open(source, "rb")
        should_close = True
    elif isinstance(source, bytes):
        fp = BytesIO(source)
        should_close = True
    else:
        fp = source
        should_close = False

    try:
        parser = PDFParser(fp)
        doc = PDFDocument(parser)

        info = doc.info[0] if doc.info else {}

        def _decode(val) -> str | None:
            if val is None:
                return None
            if isinstance(val, bytes):
                # Handle UTF-16 BOM (Big Endian: \xfe\xff, Little Endian: \xff\xfe)
                if val.startswith(b'\xfe\xff'):
                    try:
                        return val[2:].decode("utf-16-be").replace('\x00', '')
                    except UnicodeDecodeError:
                        pass
                if val.startswith(b'\xff\xfe'):
                    try:
                        return val[2:].decode("utf-16-le").replace('\x00', '')
                    except UnicodeDecodeError:
                        pass
                try:
                    return val.decode("utf-8")
                except UnicodeDecodeError:
                    return val.decode("latin-1", errors="replace")
            return str(val)

        # Count pages
        page_count = sum(1 for _ in PDFPage.create_pages(doc))

        return {
            "title": _decode(info.get("Title")),
            "author": _decode(info.get("Author")),
            "subject": _decode(info.get("Subject")),
            "creator": _decode(info.get("Creator")),
            "producer": _decode(info.get("Producer")),
            "creation_date": _decode(info.get("CreationDate")),
            "modification_date": _decode(info.get("ModDate")),
            "keywords": _decode(info.get("Keywords")),
            "page_count": page_count,
        }
    finally:
        if should_close:
            fp.close()
