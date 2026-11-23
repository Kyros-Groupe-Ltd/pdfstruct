"""Representative native documents and adversarial container/encoding inputs."""

from __future__ import annotations

import builtins
import io
import json
import zipfile
from pathlib import Path

import pytest

from pdfstruct.ingestion import (
    DocumentDependencyError,
    DocumentInputError,
    parse_document,
)


@pytest.fixture
def word_document():
    docx = pytest.importorskip("docx", reason="install pdfstructx[documents] for DOCX tests")
    return docx.Document()


def word_bytes(document) -> bytes:
    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


def zip_members(members: dict[str, bytes], compression=zipfile.ZIP_DEFLATED) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=compression) as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return output.getvalue()


def replace_member(raw: bytes, name: str, data: bytes) -> bytes:
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        members = {entry: archive.read(entry) for entry in archive.namelist()}
    members[name] = data
    return zip_members(members)


def test_txt_french_line_anchors_hash_and_json():
    raw = "\ufeffAide financière au Québec\r\nPour les PME\r\n\r\nAprès l’admissibilité".encode()
    document = parse_document(raw, filename="programme.TXT")
    assert document.format == "txt"
    assert document.blocks[0].text == "Aide financière au Québec\nPour les PME"
    assert document.blocks[0].locator == {"line_start": 1, "line_end": 2}
    assert document.blocks[1].locator == {"line_start": 4, "line_end": 4}
    assert len(document.sha256) == 64
    assert document.blocks[0].id == parse_document(raw, filename="renamed.txt").blocks[0].id
    assert json.loads(json.dumps(document.to_dict(), ensure_ascii=False))["blocks"][1]["text"]
    assert all("page" not in block.locator for block in document.blocks)


@pytest.mark.parametrize("encoding", ["utf-16", "utf-32"])
def test_txt_bom_decoding(encoding):
    document = parse_document("Québec français".encode(encoding), filename="test.txt")
    assert document.text == "Québec français"
    assert document.metadata["encoding"] == encoding


def test_txt_encoding_is_not_silently_guessed():
    raw = "Québec – 250 $".encode("cp1252")
    with pytest.raises(DocumentInputError, match="known encoding"):
        parse_document(raw, filename="legacy.txt")
    assert parse_document(raw, filename="legacy.txt", encoding="cp1252").text == "Québec – 250 $"
    with pytest.raises(DocumentInputError, match="known encoding"):
        parse_document(b"text", filename="test.txt", encoding="not-an-encoding")
    with pytest.raises(DocumentInputError, match="NUL"):
        parse_document(b"binary\x00text", filename="test.txt")


def test_stream_position_restored_and_local_path(tmp_path):
    stream = io.BytesIO(b"line one\n\nline three")
    stream.seek(5)
    assert parse_document(stream, filename="test.txt").text == "line one\n\nline three"
    assert stream.tell() == 5
    assert not stream.closed
    source = tmp_path / "texte.txt"
    source.write_text("Québec", encoding="utf-8")
    document = parse_document(source)
    assert document.source_name == "texte.txt"
    assert document.text == "Québec"


def test_short_reads_are_not_silently_truncated():
    class ShortReadStream(io.BytesIO):
        def read(self, size=-1):
            return super().read(min(size, 2))

    stream = ShortReadStream("Québec français".encode())
    assert parse_document(stream, filename="test.txt").text == "Québec français"
    assert stream.tell() == 0


@pytest.mark.parametrize("raw", [b"", b" \n\r\n\t"])
def test_empty_txt_is_explicit(raw):
    document = parse_document(raw, filename="empty.txt")
    assert document.blocks == []
    assert "No nonempty text" in document.warnings[0]


def test_input_and_output_limits_are_enforced():
    with pytest.raises(DocumentInputError, match="filename is required"):
        parse_document(b"text")
    with pytest.raises(DocumentInputError, match="binary"):
        parse_document(io.StringIO("text"), filename="text.txt")
    with pytest.raises(DocumentInputError, match="max_bytes"):
        parse_document(b"12345", filename="text.txt", max_bytes=4)
    with pytest.raises(DocumentInputError, match="max_blocks"):
        parse_document(b"one\n\ntwo", filename="text.txt", max_blocks=1)
    with pytest.raises(DocumentInputError, match="positive integer"):
        parse_document(b"text", filename="text.txt", max_blocks=True)
    with pytest.raises(DocumentInputError, match="extensions"):
        parse_document(b"text", filename="old.doc")
    with pytest.raises(DocumentInputError, match="only supported for TXT"):
        parse_document(b"pdf", filename="test.pdf", encoding="utf-8")
    with pytest.raises(DocumentInputError, match="only supported for PDF"):
        parse_document(b"text", filename="test.txt", pdf_options={})


def test_docx_body_order_headings_bold_and_table(word_document):
    word_document.add_heading("Programme Québec", level=1)
    word_document.add_paragraph("Entreprises admissibles.")
    bold = word_document.add_paragraph()
    bold.add_run("Important : conserver la preuve").bold = True
    table = word_document.add_table(rows=2, cols=2)
    table.cell(0, 0).text, table.cell(0, 1).text = "Critère", "Valeur"
    table.cell(1, 0).text, table.cell(1, 1).text = "Région", "Québec"
    word_document.add_paragraph("Après le tableau.")
    result = parse_document(word_bytes(word_document), filename="programme.docx")
    assert [block.kind for block in result.blocks] == [
        "heading", "paragraph", "paragraph", "table", "paragraph",
    ]
    assert result.blocks[0].heading_level == 1
    assert result.blocks[2].heading_level is None
    assert result.blocks[2].metadata["has_explicit_bold_run"] is True
    assert result.blocks[3].rows == [["Critère", "Valeur"], ["Région", "Québec"]]
    assert result.blocks[3].locator == {"body_index": 4, "table_index": 1}
    assert result.blocks[4].locator == {"body_index": 5, "paragraph_index": 4}
    assert all("page" not in block.locator for block in result.blocks)


def test_docx_merged_cells_have_one_value_and_real_spans(word_document):
    table = word_document.add_table(rows=3, cols=3)
    table.cell(0, 0).merge(table.cell(0, 1)).text = "En-tête fusionné"
    table.cell(1, 0).merge(table.cell(2, 0)).text = "Québec"
    table.cell(1, 1).text = "250"
    result = parse_document(word_bytes(word_document), filename="merged.docx")
    block = result.blocks[0]
    assert block.text.count("En-tête fusionné") == 1
    assert block.text.count("Québec") == 1
    assert block.rows[0] == ["En-tête fusionné", "", ""]
    assert block.rows[2][0] == ""
    anchors = {cell["text"]: cell for cell in block.metadata["cells"]}
    assert anchors["En-tête fusionné"]["col_span"] == 2
    assert anchors["Québec"]["row_span"] == 2
    assert anchors["Québec"]["row"] == 2


def test_empty_and_table_only_docx(word_document):
    empty = parse_document(word_bytes(word_document), filename="empty.docx")
    assert empty.blocks == []
    assert any("No nonempty text" in warning for warning in empty.warnings)
    word_document.add_table(rows=1, cols=1).cell(0, 0).text = "Tableau seul"
    table_only = parse_document(word_bytes(word_document), filename="table.docx")
    assert len(table_only.blocks) == 1
    assert table_only.blocks[0].kind == "table"
    assert table_only.text == "Tableau seul"


def test_nested_docx_table_is_preserved_with_warning(word_document):
    outer = word_document.add_table(rows=1, cols=1).cell(0, 0)
    outer.text = "Avant"
    outer.add_table(rows=1, cols=1).cell(0, 0).text = "Imbriqué"
    result = parse_document(word_bytes(word_document), filename="nested.docx")
    assert "Avant\nImbriqué" in result.text
    assert any("Nested DOCX tables" in warning for warning in result.warnings)


def test_docx_table_cell_limit(word_document):
    word_document.add_table(rows=2, cols=2)
    with pytest.raises(DocumentInputError, match="max_table_cells"):
        parse_document(word_bytes(word_document), filename="table.docx", max_table_cells=3)


def test_docx_missing_dependency_is_actionable(word_document, monkeypatch):
    raw = word_bytes(word_document)
    real_import = builtins.__import__

    def missing(name, *args, **kwargs):
        if name == "docx":
            raise ImportError("not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", missing)
    with pytest.raises(DocumentDependencyError, match=r"pdfstructx\[documents\]"):
        parse_document(raw, filename="test.docx")


def test_docx_zip_limits_and_malformed_xml(word_document):
    raw = word_bytes(word_document)
    with pytest.raises(DocumentInputError, match="max_docx_entries"):
        parse_document(raw, filename="test.docx", max_docx_entries=2)
    with pytest.raises(DocumentInputError, match="max_docx_uncompressed_bytes"):
        parse_document(raw, filename="test.docx", max_docx_uncompressed_bytes=100)
    with pytest.raises(DocumentInputError, match="Word XML"):
        parse_document(replace_member(raw, "word/document.xml", b"<broken"), filename="test.docx")
    with pytest.raises(DocumentInputError, match="ZIP package"):
        parse_document(b"not a zip", filename="test.docx")
    with pytest.raises(DocumentInputError, match="required Word"):
        parse_document(zip_members({"test.txt": b"test"}), filename="test.docx")


def test_docx_missing_required_xml_attribute_is_an_input_error(word_document):
    from docx.oxml.ns import qn

    table = word_document.add_table(rows=1, cols=2)
    merged = table.cell(0, 0).merge(table.cell(0, 1))
    merged._tc.tcPr.gridSpan.attrib.pop(qn("w:val"))
    with pytest.raises(DocumentInputError, match="Word XML"):
        parse_document(word_bytes(word_document), filename="malformed.docx")


@pytest.mark.parametrize("compression", [zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED])
def test_docx_corrupt_member_is_an_input_error(compression):
    raw = bytearray(zip_members({
        "[Content_Types].xml": b"<Types/>", "word/document.xml": b"<document/>",
    }, compression=compression))
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        entry = archive.getinfo("word/document.xml")
        offset = entry.header_offset + 30 + len(entry.filename.encode()) + len(entry.extra)
    # A stored member fails its CRC; a deflated member has an invalid block type.
    raw[offset] = 255
    with pytest.raises(DocumentInputError, match="ZIP package"):
        parse_document(bytes(raw), filename="corrupt.docx")


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16", "utf-32"])
def test_docx_xml_entities_are_rejected_before_parser(word_document, encoding):
    raw = word_bytes(word_document)
    malicious = '<!DOCTYPE x [<!ENTITY file SYSTEM "file:///etc/passwd">]><x>&file;</x>'
    # Non-.xml extension ensures validation covers every OPC part, not just a suffix.
    raw = replace_member(raw, "word/arbitrary.part", malicious.encode(encoding))
    with pytest.raises(DocumentInputError, match="DTD/entity"):
        parse_document(raw, filename="test.docx")


def test_docx_unsafe_duplicate_and_macro_members(word_document):
    raw = word_bytes(word_document)
    for name, message in [("../bad.xml", "unsafe"), ("word/vbaProject.bin", "Macro")]:
        with pytest.raises(DocumentInputError, match=message):
            parse_document(replace_member(raw, name, b"bad"), filename="test.docx")
    output = io.BytesIO(raw)
    with pytest.warns(UserWarning, match="Duplicate"), zipfile.ZipFile(output, "a") as archive:
        archive.writestr("word/document.xml", b"duplicate")
    with pytest.raises(DocumentInputError, match="duplicate ZIP"):
        parse_document(output.getvalue(), filename="test.docx")


def test_docx_zip_bomb_compression_ratio(word_document):
    raw = replace_member(word_bytes(word_document), "word/bomb.bin", b"0" * 2_000_000)
    with pytest.raises(DocumentInputError, match="compression-ratio"):
        parse_document(raw, filename="bomb.docx")


def test_docx_unsupported_parts_have_visible_warnings(word_document):
    word_document.add_paragraph("Texte du corps")
    word_document.sections[0].header.paragraphs[0].text = "En-tête"
    result = parse_document(word_bytes(word_document), filename="header.docx")
    assert result.text == "Texte du corps"
    assert any("headers and footers" in warning for warning in result.warnings)


def test_pdf_native_file_has_real_page_and_geometry():
    fixture = Path(__file__).parent / "fixtures" / "structured.pdf"
    result = parse_document(fixture)
    assert result.format == "pdf"
    assert result.text.strip()
    assert result.metadata["page_count"] >= 1
    assert result.blocks[0].locator["page"] == 1
    assert "bbox" in result.blocks[0].locator
    assert result.blocks[0].locator["coordinate_system"] == "pdfminer_layout_bottom_left_points"


def test_pdf_table_and_associated_paragraph_are_not_duplicated(monkeypatch):
    from pdfstruct.models.document import (
        BBox,
        Document,
        Page,
        Paragraph,
        Table,
        TableCell,
        TableRow,
    )

    table = Table(
        rows=[TableRow(cells=[TableCell(text="250 $", row_index=0, col_index=0)])],
        bbox=BBox(0, 20, 100, 40), page_number=1, element_id="table-1",
    )
    page = Page(number=1, width=600, height=800, tables=[table], paragraphs=[
        Paragraph(text="Avant", page_number=1),
        Paragraph(text="250 $", page_number=1, table_id="table-1"),
        Paragraph(text="Après", page_number=1),
    ], table_warnings=["table detector resource limit reached"])
    monkeypatch.setattr("pdfstruct.parser.parse", lambda *args, **kwargs: Document(pages=[page]))
    result = parse_document(b"%PDF-1.7", filename="table.pdf")
    assert [block.kind for block in result.blocks] == ["paragraph", "table", "paragraph"]
    assert result.text.count("250 $") == 1
    assert result.blocks[1].rows == [["250 $"]]
    assert any("table detector resource limit" in warning for warning in result.warnings)


def test_invalid_pdf_header_and_syntax():
    with pytest.raises(DocumentInputError, match="header"):
        parse_document(b"not pdf", filename="bad.pdf")
    with pytest.raises(DocumentInputError, match="syntax"):
        parse_document(b"%PDF-1.7\ninvalid", filename="bad.pdf")
