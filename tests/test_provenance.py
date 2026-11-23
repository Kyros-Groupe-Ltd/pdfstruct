"""Citation and reading-order regressions using public synthetic documents."""

from io import BytesIO

from reportlab.pdfgen import canvas

import pdfstruct
from pdfstruct.layout.analyzer import group_lines_into_paragraphs
from pdfstruct.models.document import (
    BBox,
    FontInfo,
    HeadingLevel,
    Paragraph,
    Table,
    TableCell,
    TableRow,
    TextLine,
)
from pdfstruct.structure.headings import _regex_heading_level, detect_headings
from pdfstruct.structure.sections import build_sections


def _pdf():
    output = BytesIO()
    pdf = canvas.Canvas(output, pagesize=(600, 800))
    pdf.setTitle("Répertoire Québec")
    for index in (1, 2):
        pdf.setFont("Helvetica-Bold", 20)
        pdf.drawString(50, 740, f"Section {index}")
        pdf.setFont("Helvetica", 11)
        pdf.drawString(
            50, 710, f"Le document présente les critères de financement du Québec {index}."
        )
        pdf.drawString(
            50, 695, "Les sources et les montants doivent rester vérifiables par les utilisateurs."
        )
        for x in (50, 160, 500):
            pdf.line(x, 560, x, 650)
        for y in (560, 590, 620, 650):
            pdf.line(50, y, 500, y)
        pdf.setFont("Helvetica-Bold", 11)
        pdf.drawString(55, 630, "Code")
        pdf.drawString(165, 630, "Critère")
        pdf.setFont("Helvetica", 11)
        pdf.drawString(55, 600, f"UNIQUE{index}A")
        pdf.drawString(165, 600, "Énergie renouvelable")
        pdf.drawString(55, 570, f"UNIQUE{index}B")
        pdf.drawString(165, 570, "Montant admissible")
        pdf.drawString(50, 530, f"Après le tableau de la section {index}, le texte reprend ici.")
        pdf.showPage()
    pdf.save()
    return output.getvalue()


def _flatten(sections):
    for section in sections:
        yield section
        yield from _flatten(section.subsections)


def test_parsed_elements_have_real_page_and_box_citations():
    document = pdfstruct.parse(_pdf())
    assert document.page_count == 2
    assert len(document.tables) == 2
    identifiers = []
    for page in document.pages:
        exported = page.to_dict()
        assert exported["coordinate_system"] == "pdfminer_layout_bottom_left_points"
        for line in page.lines:
            assert line.page_number == page.number
            assert line.bbox.area > 0
            identifiers.append(line.element_id)
        for paragraph in page.paragraphs:
            assert paragraph.page_number == page.number
            assert paragraph.to_dict()["bbox"]
            assert paragraph.to_dict()["lines"][0]["page"] == page.number
            identifiers.append(paragraph.element_id)
        for table in page.tables:
            assert table.page_number == page.number
            assert all(cell.to_dict().get("bbox") for row in table.rows for cell in row.cells)
            identifiers.append(table.element_id)
    assert None not in identifiers
    assert len(set(identifiers)) == len(identifiers)
    sections = list(_flatten(document.sections))
    assert sections and all(
        1 <= section.page_number <= section.page_end <= 2 for section in sections
    )
    for section in sections:
        if section.heading:
            assert section.to_dict()["heading_source"]["page"] in (1, 2)
    owned = [id(table) for section in sections for table in section.tables]
    assert sorted(owned) == sorted(id(table) for table in document.tables)


def test_structured_exports_emit_table_once_and_keep_surrounding_content():
    document = pdfstruct.parse(_pdf())
    for rendered in (pdfstruct.to_markdown(document), pdfstruct.to_text(document)):
        for index in (1, 2):
            assert rendered.count(f"UNIQUE{index}A") == 1
            assert rendered.count(f"UNIQUE{index}B") == 1
            assert rendered.index(f"UNIQUE{index}B") < rendered.index(
                f"Après le tableau de la section {index}"
            )
    assert "Code" not in [section.heading for section in _flatten(document.sections)]


def test_same_size_heading_does_not_swallow_following_body():
    font = FontInfo("Arial", 11)
    heading = TextLine(
        "1.1 Contexte",
        BBox(20, 100, 160, 111),
        font,
        is_heading=True,
        heading_level=HeadingLevel.H2,
        page_number=3,
    )
    body = TextLine(
        "Une phrase explicative suit le titre.", BBox(20, 85, 220, 96), font, page_number=3
    )
    paragraphs = group_lines_into_paragraphs([heading, body], 600)
    assert len(paragraphs) == 2
    assert paragraphs[0].is_heading and not paragraphs[1].is_heading
    assert all(paragraph.page_number == 3 for paragraph in paragraphs)


def test_parent_page_range_includes_nested_content_and_table():
    paragraphs = [
        Paragraph("Titre", is_heading=True, heading_level=HeadingLevel.H1, page_number=1),
        Paragraph("Enfant", is_heading=True, heading_level=HeadingLevel.H2, page_number=2),
        Paragraph("Fin", page_number=3),
    ]
    table = Table([TableRow([TableCell("value", 0, 0)])], page_number=4)
    sections = build_sections(paragraphs, [table])
    assert (sections[0].page_number, sections[0].page_end) == (1, 4)
    assert sections[0].subsections[0].tables == [table]
    assert (sections[0].subsections[0].page_number, sections[0].subsections[0].page_end) == (2, 4)


def test_table_only_document_has_section_and_citation():
    table = Table([TableRow([TableCell("Donnée", 0, 0)])], page_number=5)
    sections = build_sections([], [table])
    assert sections[0].tables == [table]
    assert sections[0].page_number == sections[0].page_end == 5


def test_french_heading_patterns_and_contents_page_entries():
    assert _regex_heading_level("1.1 Contexte", False) == 3
    assert _regex_heading_level("ÉTUDES ET RÉFÉRENCES", True) == 2
    assert _regex_heading_level("1.99 $", False) is None
    line = TextLine("1. Contexte ............ 5", BBox(0, 0, 250, 20), FontInfo("Bold", 20, True))
    assert not detect_headings([line])[0].is_heading


def test_metadata_error_logs_class_not_document_content(monkeypatch, caplog):
    from pdfstruct import parser

    def invalid_metadata(_source):
        raise ValueError("PRIVATE_APPLICANT_DATA")

    monkeypatch.setattr(parser, "extract_pdf_metadata", invalid_metadata)
    document = pdfstruct.parse(_pdf())
    assert document.page_count == 2
    assert "ValueError" in caplog.text
    assert "PRIVATE_APPLICANT_DATA" not in caplog.text


def test_table_limit_preserves_native_text_and_emits_serialized_warning():
    document = pdfstruct.parse(
        _pdf(), table_detection_limits=pdfstruct.TableDetectionLimits(max_rectangles=1)
    )
    assert not document.tables
    assert "UNIQUE1A" in document.text and "UNIQUE2B" in document.text
    assert all(page.table_warnings for page in document.pages)
    assert "max_rectangles" in document.to_dict()["pages"][0]["table_warnings"][0]


def test_overflowing_table_text_is_preserved_with_review_warning():
    output = BytesIO()
    pdf = canvas.Canvas(output, pagesize=(600, 800))
    for x in (50, 160, 390):
        pdf.line(x, 560, x, 650)
    for y in (560, 590, 620, 650):
        pdf.line(50, y, 390, y)
    pdf.setFont("Helvetica", 12)
    sentence = "Leading cell text: EXTRATAIL_SENTINEL_OUTSIDE"
    for y, key, value in (
        (630, "Code", "Description"),
        (600, "A", sentence),
        (570, "B", "Regular text"),
    ):
        pdf.drawString(55, y, key)
        pdf.drawString(165, y, value)
    pdf.showPage()
    pdf.save()
    document = pdfstruct.parse(output.getvalue())
    assert "table_text_crosses_boundary" in document.pages[0].table_warnings
    for rendered in (pdfstruct.to_markdown(document), pdfstruct.to_text(document)):
        assert sentence in rendered
        assert rendered.count("EXTRATAIL_SENTINEL_OUTSIDE") == 1
