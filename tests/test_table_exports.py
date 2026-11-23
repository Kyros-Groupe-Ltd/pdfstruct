"""Export invariants for duplicate headers, merged anchors and table ownership."""

import pdfstruct
from pdfstruct.models.document import (
    BBox,
    Document,
    HeadingLevel,
    Paragraph,
    Section,
    Table,
    TableCell,
    TableRow,
)


def test_duplicate_and_blank_headers_do_not_overwrite_data():
    header = ["Nom", "Nom", "", "col_2", "Nom_2"]
    table = Table(
        [
            TableRow(
                [TableCell(text, 0, col, is_header=True) for col, text in enumerate(header)],
                is_header=True,
            ),
            TableRow([TableCell(str(col), 1, col) for col in range(5)]),
        ]
    )
    result = table.to_dicts()[0]
    assert len(result) == 5
    assert list(result.values()) == ["0", "1", "2", "3", "4"]
    assert result["Nom"] == "0"
    assert result["Nom_2"] == "4"
    assert result["col_2"] == "3"


def test_spans_export_anchor_once_without_collapsing_columns():
    table = Table(
        [
            TableRow([TableCell("Fusion", 0, 0, colspan=2), TableCell("C", 0, 2)]),
            TableRow(
                [
                    TableCell("Deux lignes", 1, 0, rowspan=2),
                    TableCell("B", 1, 1),
                    TableCell("D", 1, 2),
                ]
            ),
            TableRow([TableCell("E", 2, 1), TableCell("F", 2, 2)]),
        ]
    )
    assert table.num_rows == 3 and table.num_cols == 3
    assert table.to_list() == [["Fusion", "", "C"], ["Deux lignes", "B", "D"], ["", "E", "F"]]
    for rendered in (
        pdfstruct.to_markdown(Document(tables=[table])),
        pdfstruct.to_text(Document(tables=[table])),
    ):
        assert rendered.count("Fusion") == 1
        assert rendered.count("Deux lignes") == 1
    assert table.to_dict()["rows"][0]["cells"][0]["colspan"] == 2


def test_title_section_keeps_its_table_without_reemitting_it():
    table = Table([TableRow([TableCell("Une seule fois", 0, 0)])], page_number=1)
    section = Section(heading="Titre", heading_level=HeadingLevel.H1, tables=[table])
    document = Document(title="Titre", sections=[section], tables=[table])
    for rendered in (pdfstruct.to_markdown(document), pdfstruct.to_text(document)):
        assert rendered.count("Une seule fois") == 1
        assert rendered.count("Titre") == 1


def test_unowned_table_and_no_section_paragraph_are_retained():
    table = Table([TableRow([TableCell("A|B\nC", 0, 0)])])
    page = pdfstruct.Page(1, 600, 800, paragraphs=[Paragraph("Texte libre")])
    markdown = pdfstruct.to_markdown(Document(pages=[page], tables=[table]))
    assert "Texte libre" in markdown
    assert "A\\|B<br>C" in markdown
    assert "Texte libre" in pdfstruct.to_text(Document(pages=[page], tables=[table]))


def test_cell_and_row_boxes_survive_serialization():
    box = BBox(10, 20, 30, 40)
    cell = TableCell("Source", 0, 0, bbox=box)
    row = TableRow([cell], bbox=box)
    assert row.to_dict()["bbox"] == box.to_dict()
    assert row.to_dict()["cells"][0]["bbox"] == box.to_dict()


def test_manually_constructed_section_content_survives_its_table():
    table = Table([TableRow([TableCell("table value", 0, 0)])])
    section = Section(content="Manually supplied section body", tables=[table])
    for rendered in (
        pdfstruct.to_markdown(Document(sections=[section])),
        pdfstruct.to_text(Document(sections=[section])),
    ):
        assert rendered.count("Manually supplied section body") == 1
        assert rendered.count("table value") == 1
