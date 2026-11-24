"""A list heuristic must never discard prose or continuation evidence."""

from io import BytesIO

import pytest
from reportlab.pdfgen import canvas

import pdfstruct
from pdfstruct.models.document import BBox, FontInfo, Paragraph, TextLine
from pdfstruct.structure.lists import detect_lists_in_paragraphs


def _paragraph(texts_and_x):
    lines = [
        TextLine(
            text, BBox(x, 700 - index * 16, x + 220, 712 - index * 16),
            FontInfo("Helvetica", 12), page_number=2, element_id=f"p2:line{index + 1}",
        )
        for index, (text, x) in enumerate(texts_and_x)
    ]
    return Paragraph(" ".join(line.text for line in lines), lines=lines, page_number=2)


@pytest.mark.parametrize("texts", [
    ["Preamble must remain.", "- First obligation", "- Second obligation"],
    ["- First obligation", "Unindented continuation must remain.", "- Second obligation"],
    ["- First obligation", "- Second obligation", "Closing sentence must remain."],
])
def test_ambiguous_mixed_paragraph_is_preserved_whole(texts):
    paragraph = _paragraph([(text, 50) for text in texts])
    original_text = paragraph.text
    original_lines = list(paragraph.lines)
    detect_lists_in_paragraphs([paragraph])
    assert paragraph.list_items is None
    assert paragraph.text == original_text
    assert paragraph.lines == original_lines


def test_indented_continuation_keeps_text_and_expands_item_evidence():
    paragraph = _paragraph([
        ("- First obligation", 50),
        ("La suite précise les conditions du Québec.", 65),
        ("- Second obligation", 50),
    ])
    original_lines = list(paragraph.lines)
    detect_lists_in_paragraphs([paragraph])
    assert len(paragraph.list_items) == 2
    assert paragraph.list_items[0].text == (
        "First obligation La suite précise les conditions du Québec."
    )
    assert paragraph.list_items[0].bbox == original_lines[0].bbox.merge(original_lines[1].bbox)
    assert paragraph.lines == original_lines
    assert [line.element_id for line in paragraph.lines] == ["p2:line1", "p2:line2", "p2:line3"]
    assert [line.page_number for line in paragraph.lines] == [2, 2, 2]


@pytest.mark.parametrize("markers", [("5)", "7)"), ("A)", "B)"), ("ii)", "iii)")])
def test_original_list_markers_are_not_renumbered_or_normalized(markers):
    paragraph = _paragraph([(f"{marker} Original item", 50) for marker in markers])
    detect_lists_in_paragraphs([paragraph])
    assert [item.marker for item in paragraph.list_items] == list(markers)
    for marker in markers:
        assert f"{marker} Original item" in paragraph.text


def test_mixed_marker_types_remain_original_text():
    paragraph = _paragraph([("- Bullet", 50), ("a) Letter", 50), ("- More", 50)])
    original = paragraph.text
    detect_lists_in_paragraphs([paragraph])
    assert paragraph.list_items is None
    assert paragraph.text == original


@pytest.mark.parametrize("continuation_x", [50, 65])
def test_real_pdf_exports_retain_list_continuation(continuation_x):
    output = BytesIO()
    pdf = canvas.Canvas(output, pagesize=(600, 800))
    pdf.setTitle("List continuation regression")
    pdf.setFont("Helvetica", 12)
    for x, y, text in [
        (50, 700, "- First obligation"),
        (continuation_x, 684, "UNIQUE_CONTINUATION must remain in the application."),
        (50, 668, "- Second obligation"),
    ]:
        pdf.drawString(x, y, text)
    pdf.showPage()
    pdf.save()
    document = pdfstruct.parse(output.getvalue(), detect_tables=False)
    for text in (document.text, pdfstruct.to_markdown(document), pdfstruct.to_text(document)):
        assert "UNIQUE_CONTINUATION" in text
        assert "First obligation" in text
        assert "Second obligation" in text
    assert any(
        "UNIQUE_CONTINUATION" in line.text and line.page_number == 1 and line.element_id
        for paragraph in document.pages[0].paragraphs for line in paragraph.lines
    )
