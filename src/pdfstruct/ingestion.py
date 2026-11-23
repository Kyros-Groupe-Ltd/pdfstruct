"""Local PDF, DOCX and TXT ingestion with explicit, format-specific source anchors.

This adapter normalizes extraction output. It does not store documents, execute
links, call an AI service, or provide application access controls. DOCX support
requires the optional ``documents`` extra; PDF OCR remains explicitly opt-in.
"""

from __future__ import annotations

import codecs
import hashlib
import io
import os
import re
import zipfile
import zlib
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO


class DocumentInputError(ValueError):
    """Unsupported, malformed, incorrectly encoded or oversized input."""


class DocumentDependencyError(ImportError):
    """An optional ingestion dependency is not installed."""


@dataclass
class DocumentBlock:
    """A source block with an anchor, not a guessed pagination or layout."""

    id: str
    kind: str
    text: str
    locator: dict[str, Any]
    heading_level: int | None = None
    rows: list[list[str]] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class IngestedDocument:
    """Canonical blocks and extraction limitations for one source revision."""

    source_name: str
    format: str
    sha256: str
    blocks: list[DocumentBlock] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return "\n\n".join(block.text for block in self.blocks if block.text)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _positive_limit(name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise DocumentInputError(f"{name} must be a positive integer")


def _bounded_read(stream: BinaryIO, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while total <= max_bytes:
        chunk = stream.read(min(1_048_576, max_bytes + 1 - total))
        if not isinstance(chunk, bytes):
            raise DocumentInputError("source must be binary; pass encoded bytes, not a text stream")
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    return b"".join(chunks)


def _read_source(
    source: str | os.PathLike[str] | bytes | BinaryIO,
    filename: str | None,
    max_bytes: int,
) -> tuple[bytes, str]:
    if filename is not None and (not isinstance(filename, str) or not filename.strip()):
        raise DocumentInputError("filename must be a nonempty string")
    if isinstance(source, (str, os.PathLike)):
        path = Path(source)
        if path.stat().st_size > max_bytes:
            raise DocumentInputError(f"Document exceeds max_bytes={max_bytes}")
        with path.open("rb") as stream:
            raw = _bounded_read(stream, max_bytes)
        name = filename or path.name
    elif isinstance(source, bytes):
        raw, name = source, filename
    elif hasattr(source, "read"):
        name = filename
        if not name:
            raise DocumentInputError("filename is required for bytes and stream inputs")
        position = None
        try:
            if source.seekable():
                position = source.tell()
                source.seek(0)
        except (AttributeError, OSError, io.UnsupportedOperation):
            position = None
        try:
            raw = _bounded_read(source, max_bytes)
        finally:
            if position is not None:
                source.seek(position)
    else:
        raise DocumentInputError("source must be a local path, bytes or a binary stream")
    if not name:
        raise DocumentInputError("filename is required for bytes and stream inputs")
    if not isinstance(raw, bytes):
        raise DocumentInputError("source must be binary; pass encoded bytes, not a text stream")
    if len(raw) > max_bytes:
        raise DocumentInputError(f"Document exceeds max_bytes={max_bytes}")
    return raw, Path(name).name


def _add_block(document: IngestedDocument, block: DocumentBlock, max_blocks: int) -> None:
    if len(document.blocks) >= max_blocks:
        raise DocumentInputError(f"Document exceeds max_blocks={max_blocks}")
    document.blocks.append(block)


def _block_id(document: IngestedDocument, anchor: str) -> str:
    return f"{document.sha256[:16]}:{anchor}"


def _table_text(rows: list[list[str]]) -> str:
    return "\n".join("\t".join(row) for row in rows)


def _ingest_txt(
    raw: bytes, document: IngestedDocument, encoding: str | None, max_blocks: int
) -> None:
    if encoding is None:
        if raw.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
            encoding = "utf-32"
        elif raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
            encoding = "utf-16"
        else:
            encoding = "utf-8-sig"
    try:
        text = raw.decode(encoding, errors="strict")
    except (LookupError, UnicodeError) as exc:
        raise DocumentInputError(
            f"TXT cannot be decoded as {encoding!r}; supply its known encoding explicitly"
        ) from exc
    if "\x00" in text:
        raise DocumentInputError("TXT contains NUL characters; check the file type or encoding")
    document.metadata["encoding"] = encoding
    document.metadata["anchor_units"] = "one-based text lines"
    # Only CR, LF and CRLF are line separators; a form feed is not an invented page.
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    start = 0
    for index in range(len(lines) + 1):
        if index < len(lines) and lines[index].strip():
            continue
        if index > start:
            _add_block(document, DocumentBlock(
                id=_block_id(document, f"lines-{start + 1}-{index}"),
                kind="paragraph", text="\n".join(lines[start:index]),
                locator={"line_start": start + 1, "line_end": index},
            ), max_blocks)
        start = index + 1


def _preflight_docx(raw: bytes, max_expanded: int, max_entries: int) -> list[str]:
    """Bound ZIP expansion and reject DTD/entity declarations before XML parsing."""
    warnings: list[str] = []
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            entries = archive.infolist()
            if len(entries) > max_entries:
                raise DocumentInputError(f"DOCX exceeds max_docx_entries={max_entries}")
            names = [entry.filename for entry in entries]
            if len(names) != len(set(names)):
                raise DocumentInputError("DOCX has duplicate ZIP member names")
            if not {"[Content_Types].xml", "word/document.xml"}.issubset(names):
                raise DocumentInputError("DOCX is missing required Word package members")
            if sum(entry.file_size for entry in entries) > max_expanded:
                raise DocumentInputError(
                    f"DOCX exceeds max_docx_uncompressed_bytes={max_expanded}"
                )
            expanded_bytes = 0
            for entry in entries:
                name = entry.filename
                path = PurePosixPath(name)
                if path.is_absolute() or ".." in path.parts or "\\" in name:
                    raise DocumentInputError("DOCX has an unsafe ZIP member name")
                if entry.flag_bits & 1:
                    raise DocumentInputError("Encrypted DOCX ZIP members are unsupported")
                if entry.file_size > max(1, entry.compress_size) * 1000:
                    raise DocumentInputError("DOCX ZIP member exceeds the compression-ratio limit")
                if "vbaproject" in name.lower():
                    raise DocumentInputError("Macro-enabled Word documents are unsupported")
                # Inspect every member: OPC XML parts need not have an .xml suffix.
                # Reading also checks CRCs instead of trusting ZIP directory sizes alone.
                with archive.open(entry) as member:
                    data = member.read(max_expanded - expanded_bytes + 1)
                expanded_bytes += len(data)
                if expanded_bytes > max_expanded:
                    raise DocumentInputError("DOCX exceeds the expansion limit")
                # The NUL removal also detects declarations in UTF-16/UTF-32 XML.
                normalized = data.replace(b"\x00", b"").upper()
                if b"<!DOCTYPE" in normalized or b"<!ENTITY" in normalized:
                    raise DocumentInputError("DOCX XML DTD/entity declarations are unsupported")
            if any(name.startswith(("word/header", "word/footer")) for name in names):
                warnings.append("DOCX headers and footers are not included in body extraction.")
            if any(name in names for name in ("word/footnotes.xml", "word/endnotes.xml")):
                warnings.append("DOCX footnotes and endnotes are not included in body extraction.")
            if any(name.startswith("word/embeddings/") for name in names):
                warnings.append("DOCX embedded files are not opened or included.")
    except (zipfile.BadZipFile, zlib.error, OSError, RuntimeError, NotImplementedError, EOFError) as exc:
        raise DocumentInputError("DOCX is not a supported, readable ZIP package") from exc
    return warnings


def _docx_heading_level(paragraph: Any) -> int | None:
    # Built-in style IDs are stable across translated Word user interfaces.
    style = paragraph.style
    visited: set[str] = set()
    while style is not None and style.style_id not in visited:
        visited.add(style.style_id)
        for candidate in (style.style_id, style.name):
            match = re.fullmatch(r"Heading\s*([1-9])", candidate or "", re.IGNORECASE)
            if match:
                return int(match.group(1))
        style = style.base_style
    # Direct outline level is actual document structure, unlike bold appearance.
    from docx.oxml.ns import qn

    properties = paragraph._p.pPr
    if properties is not None:
        outline = properties.find(qn("w:outlineLvl"))
        if outline is not None:
            try:
                level = int(outline.get(qn("w:val"), "9"))
            except ValueError:
                return None
            if 0 <= level <= 8:
                return level + 1
    return None


def _docx_table(
    table: Any, max_cells: int, warnings: list[str], *, depth: int = 0
) -> tuple[list[list[str]], list[dict[str, Any]]]:
    from docx.table import Table, _Cell
    from docx.text.paragraph import Paragraph

    if depth > 12:
        raise DocumentInputError("DOCX nested table depth exceeds 12")
    rows: list[list[str]] = []
    anchors: list[dict[str, Any]] = []
    previous: dict[int, dict[str, Any]] = {}
    width = 0
    cell_count = 0
    for row_index, row in enumerate(table.rows, 1):
        values = [""] * row.grid_cols_before if row.grid_cols_before <= max_cells else []
        column = row.grid_cols_before
        if column < 0 or column > max_cells or row.grid_cols_after < 0:
            raise DocumentInputError("DOCX table grid is invalid or exceeds max_table_cells")
        current: dict[int, dict[str, Any]] = {}
        for cell_xml in row._tr.tc_lst:
            span = cell_xml.grid_span
            cell_count += 1
            if span < 1 or column + span > max_cells or cell_count > max_cells:
                raise DocumentInputError("DOCX table exceeds max_table_cells")
            texts: list[str] = []
            for item in _Cell(cell_xml, table).iter_inner_content():
                if isinstance(item, Paragraph):
                    texts.append(item.text)
                elif isinstance(item, Table):
                    nested_rows, _ = _docx_table(
                        item, max_cells, warnings, depth=depth + 1
                    )
                    texts.append(_table_text(nested_rows))
                    warning = "Nested DOCX tables are flattened inside their parent cell text."
                    if warning not in warnings:
                        warnings.append(warning)
            cell_text = "\n".join(texts).strip("\n")
            anchor = previous.get(column) if cell_xml.vMerge == "continue" else None
            if anchor is not None and (
                anchor["col_span"] != span or anchor["col"] != column + 1
                or any(previous.get(col) is not anchor for col in range(column, column + span))
            ):
                anchor = None
            if anchor is None:
                if cell_xml.vMerge == "continue":
                    warning = "A DOCX vertical merge lacked a matching anchor; its text was retained."
                    if warning not in warnings:
                        warnings.append(warning)
                anchor = {
                    "row": row_index, "col": column + 1, "row_span": 1,
                    "col_span": span, "text": cell_text,
                }
                anchors.append(anchor)
                values.extend([cell_text] + [""] * (span - 1))
            else:
                anchor["row_span"] += 1
                if cell_text:
                    anchor["text"] += "\n" + cell_text
                    rows[anchor["row"] - 1][anchor["col"] - 1] = anchor["text"]
                values.extend([""] * span)
            if cell_xml.vMerge is not None:
                for col in range(column, column + span):
                    current[col] = anchor
            column += span
        if column + row.grid_cols_after > max_cells:
            raise DocumentInputError("DOCX table exceeds max_table_cells")
        values.extend([""] * row.grid_cols_after)
        width = max(width, len(values))
        if width * row_index > max_cells:
            raise DocumentInputError("DOCX table exceeds max_table_cells")
        rows.append(values)
        previous = current
    for values in rows:
        values.extend([""] * (width - len(values)))
    return rows, anchors


def _ingest_docx(
    raw: bytes, document: IngestedDocument, max_blocks: int,
    max_expanded: int, max_entries: int, max_cells: int,
) -> None:
    document.warnings.extend(_preflight_docx(raw, max_expanded, max_entries))
    try:
        from docx import Document
        from docx.exceptions import PythonDocxError
        from docx.opc.exceptions import OpcError
        from docx.oxml.exceptions import XmlchemyError
        from docx.oxml.ns import qn
        from docx.table import Table
        from docx.text.paragraph import Paragraph
        from lxml.etree import XMLSyntaxError
    except ImportError as exc:
        raise DocumentDependencyError(
            "DOCX support requires: pip install 'pdfstructx[documents]'"
        ) from exc
    try:
        parsed = Document(io.BytesIO(raw))
        omitted_tags = ("w:ins", "w:del", "w:sdt", "w:txbxContent", "w:altChunk", "w:drawing")
        present = {element.tag for element in parsed.element.iter()}
        if any(qn(tag) in present for tag in omitted_tags):
            document.warnings.append(
                "DOCX contains revisions, content controls, text boxes, imported content or "
                "drawings; body extraction does not fully represent these features."
            )
        paragraph_index = table_index = 0
        body_indices = {element: index for index, element in enumerate(parsed.element.body, 1)}
        for item in parsed.iter_inner_content():
            body_index = body_indices[item._element]
            if isinstance(item, Paragraph):
                paragraph_index += 1
                if not item.text.strip():
                    continue
                heading_level = _docx_heading_level(item)
                _add_block(document, DocumentBlock(
                    id=_block_id(document, f"body-{body_index}-p-{paragraph_index}"),
                    kind="heading" if heading_level else "paragraph", text=item.text,
                    locator={"body_index": body_index, "paragraph_index": paragraph_index},
                    heading_level=heading_level,
                    metadata={
                        "style": item.style.name,
                        "has_explicit_bold_run": any(run.bold for run in item.runs),
                    },
                ), max_blocks)
            elif isinstance(item, Table):
                table_index += 1
                rows, cells = _docx_table(item, max_cells, document.warnings)
                _add_block(document, DocumentBlock(
                    id=_block_id(document, f"body-{body_index}-table-{table_index}"),
                    kind="table", text=_table_text(rows), rows=rows,
                    locator={"body_index": body_index, "table_index": table_index},
                    metadata={"cells": cells, "cell_index_base": 1},
                ), max_blocks)
        document.metadata["anchor_units"] = "one-based body, paragraph, table and cell indices"
    except (
        XMLSyntaxError, PythonDocxError, OpcError, XmlchemyError,
        KeyError, ValueError, TypeError, IndexError, AttributeError,
    ) as exc:
        if isinstance(exc, DocumentInputError):
            raise
        raise DocumentInputError("DOCX has invalid or unsupported Word XML structure") from exc


def _ingest_pdf(
    raw: bytes, document: IngestedDocument, options: dict[str, Any], max_blocks: int,
    max_cells: int,
) -> None:
    from pdfminer.pdfparser import PDFSyntaxError

    from pdfstruct.models.document import Table
    from pdfstruct.parser import parse
    from pdfstruct.structure.blocks import content_blocks

    if b"%PDF-" not in raw[:1024]:
        raise DocumentInputError("PDF header is missing")
    try:
        parsed = parse(raw, **options)
    except PDFSyntaxError as exc:
        raise DocumentInputError("PDF syntax is invalid") from exc
    document.metadata["page_count"] = parsed.page_count
    document.metadata["anchor_units"] = "one-based PDF page and bottom-left layout points"
    for page in parsed.pages:
        document.warnings.extend(f"PDF page {page.number}: {warning}" for warning in page.ocr_warnings)
        document.warnings.extend(
            f"PDF page {page.number}: {warning}" for warning in page.table_warnings
        )
        if page.is_scanned and not page.ocr_applied:
            document.warnings.append(
                f"PDF page {page.number} is image-based; OCR was not applied."
            )
        table_anchors = {paragraph.table_id for paragraph in page.paragraphs if paragraph.table_id}
        if any(table.element_id not in table_anchors for table in page.tables):
            document.warnings.append(
                f"PDF page {page.number}: an unanchored table uses heuristic physical reading order."
            )
        for block_index, block in enumerate(content_blocks(page.paragraphs, page.tables), 1):
            locator: dict[str, Any] = {
                "page": page.number, "block_index": block_index,
                "coordinate_system": "pdfminer_layout_bottom_left_points",
            }
            if block.bbox is not None:
                locator["bbox"] = block.bbox.to_dict()
            metadata: dict[str, Any] = {"source_element_id": block.element_id}
            rows = None
            heading_level = None
            if isinstance(block, Table):
                if block.num_rows * block.num_cols > max_cells:
                    raise DocumentInputError("PDF table exceeds max_table_cells")
                rows = block.to_list()
                kind, text = "table", _table_text(rows)
                metadata["table"] = block.to_dict()
            else:
                text = block.text
                heading_level = block.heading_level.value if block.heading_level else None
                kind = "heading" if block.is_heading else "paragraph"
                metadata["source"] = block.source
                metadata["lines"] = [line.to_dict() for line in block.lines]
            _add_block(document, DocumentBlock(
                id=_block_id(document, f"page-{page.number}-block-{block_index}"),
                kind=kind, text=text, locator=locator, heading_level=heading_level,
                rows=rows, metadata=metadata,
            ), max_blocks)


def parse_document(
    source: str | os.PathLike[str] | bytes | BinaryIO,
    *,
    filename: str | None = None,
    encoding: str | None = None,
    pdf_options: dict[str, Any] | None = None,
    max_bytes: int = 25_000_000,
    max_docx_uncompressed_bytes: int = 100_000_000,
    max_docx_entries: int = 2000,
    max_blocks: int = 10_000,
    max_table_cells: int = 100_000,
) -> IngestedDocument:
    """Extract local PDF/DOCX/TXT with source anchors and explicit limitations.

    Bytes and binary streams require ``filename`` for type selection. Seekable
    streams are read from their beginning and their original position is restored.
    TXT uses strict UTF-8, or UTF-16/32 when a BOM is present; ``encoding`` may
    explicitly override that choice. PDF parser/OCR options go in ``pdf_options``.

    Byte, ZIP, table and output limits reduce accidental resource exhaustion.
    Untrusted uploads still belong in an isolated worker with CPU/memory/time
    limits; these checks are not an antivirus or a parser sandbox.
    """
    for name, value in (
        ("max_bytes", max_bytes), ("max_docx_uncompressed_bytes", max_docx_uncompressed_bytes),
        ("max_docx_entries", max_docx_entries), ("max_blocks", max_blocks),
        ("max_table_cells", max_table_cells),
    ):
        _positive_limit(name, value)
    raw, name = _read_source(source, filename, max_bytes)
    format_name = Path(name).suffix.lower().lstrip(".")
    if format_name not in {"pdf", "docx", "txt"}:
        raise DocumentInputError("Supported document extensions are .pdf, .docx and .txt")
    if encoding is not None and format_name != "txt":
        raise DocumentInputError("encoding is only supported for TXT input")
    if pdf_options is not None and format_name != "pdf":
        raise DocumentInputError("pdf_options is only supported for PDF input")
    document = IngestedDocument(
        source_name=name, format=format_name, sha256=hashlib.sha256(raw).hexdigest(),
        metadata={"size_bytes": len(raw)},
    )
    if format_name == "txt":
        _ingest_txt(raw, document, encoding, max_blocks)
    elif format_name == "docx":
        _ingest_docx(raw, document, max_blocks, max_docx_uncompressed_bytes,
                     max_docx_entries, max_table_cells)
    else:
        _ingest_pdf(raw, document, pdf_options or {}, max_blocks, max_table_cells)
    if not any(block.text.strip() for block in document.blocks):
        document.warnings.append("No nonempty text was extracted; review the source before indexing.")
    return document
