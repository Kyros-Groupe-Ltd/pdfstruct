"""Regression cases for mixed native/raster pages and lossless reconciliation."""

import io
from contextlib import closing
from types import SimpleNamespace

import pytest

import pdfstruct
from pdfstruct.extractors import ocr as ocr_module
from pdfstruct.extractors.ocr import (
    OCROptions,
    _data_to_lines,
    _reconcile_lines,
    _union_area,
    assess_ocr_regions,
)
from pdfstruct.extractors.text import extract_pages_raw
from pdfstruct.models.document import BBox, FontInfo, TextLine

from .test_ocr import NATIVE_TEXT, make_pdf, require_engine, word_data


def line(text, box=(20, 200, 300, 220), source="native"):
    return TextLine(text, BBox(*box), FontInfo("Test", 12), source=source)


def test_image_overlap_does_not_inflate_coverage():
    box = BBox(0, 0, 240, 400)
    assert _union_area([box, box, box]) == 240 * 400


def test_header_does_not_hide_scan_body():
    page = next(extract_pages_raw(make_pdf("header_scan")))
    assert len(page.lines[0].text) > 50
    assert assess_ocr_regions(page) == [BBox(0, 0, 600, 400)]


def test_small_scan_region_selected_independently_of_native_text():
    page = next(extract_pages_raw(make_pdf("small_scan")))
    assert assess_ocr_regions(page) == [BBox(180, 120, 420, 280)]


def test_bad_character_map_does_not_count_as_searchable():
    page = next(extract_pages_raw(make_pdf("searchable")))
    assert not assess_ocr_regions(page)
    page.lines = [line("(cid:123)" * 30)]
    assert assess_ocr_regions(page)
    page.lines = [line("\ufffd" * 100)]
    assert assess_ocr_regions(page)


@pytest.mark.parametrize("text", ["Québec — projet d’économie, 12 345,67 $", "中文资料 العربية"])
def test_non_english_text_is_not_rejected_as_garbled(text):
    page = next(extract_pages_raw(make_pdf("scan")))
    page.lines = [line(text * 5)]
    assert not assess_ocr_regions(page)


def test_auto_preserves_native_and_removes_duplicate_ocr():
    native = line("Facture Québec 12345")
    duplicate = line("Facture Québec 12345", (21, 201, 299, 221), source="ocr")
    body = line("Nouveau texte numérisé", (20, 100, 300, 120), source="ocr")
    result = _reconcile_lines([native], [duplicate, body])
    assert result == [native, body]
    assert result[0] is native


def test_force_prefers_recovered_text_but_keeps_unmatched_native():
    header = line("Native header", (20, 350, 300, 370))
    native = line("Invoice l2345")
    corrected = line("Invoice 12345", source="ocr")
    assert _reconcile_lines([header, native], [corrected], force=True) == [header, corrected]
    assert _reconcile_lines([header, native], [], force=True) == [header, native]


def test_auto_replaces_broken_native_only_where_recovered():
    damaged = line("(cid:123)" * 10)
    footer = line("Page 7", (20, 10, 80, 25))
    recovered = line("Projet Québec", source="ocr")
    assert _reconcile_lines([damaged, footer], [recovered]) == [recovered, footer]


def test_crop_offset_keeps_original_page_coordinates():
    lines, _ = _data_to_lines(word_data(), (1200, 800), 600, 400, offset=(200, 100))
    assert lines[0].bbox == BBox(110, 320, 175, 330)
    assert lines[0].ocr_spans[0].polygon == [
        (110, 330), (135, 330), (135, 320), (110, 320),
    ]


def test_empty_ocr_is_visible_and_does_not_erase_native(monkeypatch):
    pytest.importorskip("pypdfium2")
    empty = word_data()
    empty["text"] = [""] * len(empty["text"])
    monkeypatch.setattr(
        ocr_module, "_load_backend",
        lambda options: SimpleNamespace(recognize=lambda image, options: empty),
    )
    doc = pdfstruct.parse(make_pdf("header_scan"), ocr=True)
    assert NATIVE_TEXT in doc.text
    assert doc.pages[0].ocr_applied
    assert doc.pages[0].ocr_confidence is None
    assert doc.pages[0].ocr_warnings


def test_white_raster_page_does_not_initialize_ocr(monkeypatch):
    pytest.importorskip("pypdfium2")

    def unexpected(options):
        pytest.fail("A blank bitmap should not initialize an OCR engine")

    monkeypatch.setattr(ocr_module, "_load_backend", unexpected)
    doc = pdfstruct.parse(make_pdf("blank_scan"), ocr=True)
    assert not doc.text
    assert not doc.pages[0].ocr_applied


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_nonzero_media_origin_rotation_and_crop_match_native_geometry(monkeypatch, rotation):
    pdfium = pytest.importorskip("pypdfium2")
    buffer = io.BytesIO()
    with pdfium.PdfDocument(make_pdf("native")) as pdf:
        with closing(pdf[0]) as page:
            page.set_mediabox(50, 75, 650, 475)
            page.set_cropbox(120, 90, 500, 390)
            page.set_rotation(rotation)
        pdf.save(buffer)
    source = buffer.getvalue()
    pages = list(extract_pages_raw(source))
    expected = pages[0].lines[0].bbox
    for native in pages[0].lines[1:]:
        expected = expected.merge(native.bbox)

    def recognize(image, options):
        with (
            image.convert("L") as gray,
            gray.point(lambda value: 255 if value < 180 else 0) as ink,
        ):
            left, top, right, bottom = ink.getbbox()
        return {
            "text": [NATIVE_TEXT], "conf": [95],
            "left": [left], "top": [top],
            "width": [right - left], "height": [bottom - top],
            "block_num": [0], "par_num": [0], "line_num": [0],
        }

    monkeypatch.setattr(
        ocr_module, "_load_backend", lambda options: SimpleNamespace(recognize=recognize)
    )
    ocr_module.apply_ocr(source, pages, OCROptions(mode="always", dpi=144))
    recovered = [item for item in pages[0].lines if item.source == "ocr"]
    assert len(recovered) == 1
    actual = recovered[0].bbox
    # Glyph ink is slightly smaller than PDF font ascent/descent bounds.
    assert abs(actual.x0 - expected.x0) < 5
    assert abs(actual.x1 - expected.x1) < 5
    assert abs(actual.y0 - expected.y0) < 5
    assert abs(actual.y1 - expected.y1) < 5


@pytest.mark.ocr_integration
@pytest.mark.parametrize("backend", ["rapidocr", "tesseract"])
@pytest.mark.parametrize("kind", ["header_scan", "small_scan"])
def test_real_mixed_page_recovers_scan_without_losing_native(backend, kind):
    require_engine(backend)
    doc = pdfstruct.parse(make_pdf(kind), ocr=True, ocr_backend=backend, ocr_dpi=300)
    assert NATIVE_TEXT in doc.text
    assert doc.text.count(NATIVE_TEXT) == 1
    assert "12345" in doc.text
    assert "250" in doc.text
    assert doc.pages[0].ocr_applied
    assert any(item.source == "native" for item in doc.pages[0].lines)
    assert any(item.source == "ocr" for item in doc.pages[0].lines)
