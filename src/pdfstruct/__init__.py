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

License: Personal Use Only — see LICENSE file.
Copyright (c) 2026 Kyros Groupe.
"""

__version__ = "0.2.2"

from pdfstruct.parser import parse, PDFParser
from pdfstruct.models.document import (
    Document,
    Page,
    Section,
    Paragraph,
    TextLine,
    Table,
    TableRow,
    TableCell,
    BBox,
    FontInfo,
    HeadingLevel,
    ListType,
    ListItem,
    ImageInfo,
)
from pdfstruct.models.metadata import DocumentMetadata
from pdfstruct.output.json_output import to_json, to_dict
from pdfstruct.output.markdown import to_markdown
from pdfstruct.output.text_output import to_text
from pdfstruct.extractors.images import generate_thumbnail

__all__ = [
    # Core API
    "parse",
    "PDFParser",
    # Models
    "Document",
    "Page",
    "Section",
    "Paragraph",
    "TextLine",
    "Table",
    "TableRow",
    "TableCell",
    "BBox",
    "FontInfo",
    "HeadingLevel",
    "ListType",
    "ListItem",
    "ImageInfo",
    "DocumentMetadata",
    # Output
    "to_json",
    "to_dict",
    "to_markdown",
    "to_text",
    # Utilities
    "generate_thumbnail",
]
