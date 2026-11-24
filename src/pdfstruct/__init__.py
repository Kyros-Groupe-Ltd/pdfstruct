"""pdfstruct — Intelligent PDF parser with structure detection.

Parse PDFs into structured documents with headings, sections, tables,
lists, and metadata. Font-aware heading detection. Multi-column support.
Grid and whitespace table extraction.

Basic usage:
    >>> import pdfstruct
    >>> doc = pdfstruct.parse("document.pdf")
    >>> print(doc.title)
    >>> print(doc.sections)
    >>> print(doc.tables)

Export formats:
    >>> pdfstruct.to_json(doc)
    >>> pdfstruct.to_markdown(doc)
    >>> pdfstruct.to_text(doc)
    >>> pdfstruct.to_dict(doc)

License: Apache 2.0 — see LICENSE file.
Copyright (c) 2026 Kyros Groupe.
"""

__version__ = "0.3.0"

from pdfstruct.extractors.images import generate_thumbnail
from pdfstruct.extractors.ocr import OCRDependencyError, OCRError
from pdfstruct.ingestion import (
    DocumentBlock,
    DocumentDependencyError,
    DocumentInputError,
    IngestedDocument,
    parse_document,
)
from pdfstruct.models.document import (
    BBox,
    Document,
    FontInfo,
    HeadingLevel,
    ImageInfo,
    ListItem,
    ListType,
    OCRSpan,
    Page,
    Paragraph,
    Section,
    Table,
    TableCell,
    TableRow,
    TextLine,
)
from pdfstruct.models.metadata import DocumentMetadata
from pdfstruct.output.json_output import to_dict, to_json
from pdfstruct.output.markdown import to_markdown
from pdfstruct.output.text_output import to_text
from pdfstruct.parser import PDFParser, parse
from pdfstruct.tables.detector import TableDetectionLimitError, TableDetectionLimits

__all__ = [
    "BBox",
    "Document",
    "DocumentBlock",
    "DocumentDependencyError",
    "DocumentInputError",
    "DocumentMetadata",
    "FontInfo",
    "HeadingLevel",
    "ImageInfo",
    "IngestedDocument",
    "ListItem",
    "ListType",
    "OCRDependencyError",
    "OCRError",
    "OCRSpan",
    "PDFParser",
    "Page",
    "Paragraph",
    "Section",
    "Table",
    "TableCell",
    "TableDetectionLimitError",
    "TableDetectionLimits",
    "TableRow",
    "TextLine",
    "generate_thumbnail",
    "parse",
    "parse_document",
    "to_dict",
    "to_json",
    "to_markdown",
    "to_text",
]
