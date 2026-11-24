"""Local asset preflight, explicit errors and network-independent recognition."""

import os
import re
import socket
from types import SimpleNamespace

import pytest

import pdfstruct
from pdfstruct.extractors import ocr as ocr_module
from pdfstruct.extractors.ocr import OCRDependencyError, OCRError, OCROptions, _data_to_lines
from pdfstruct.extractors.text import extract_pages_raw
from pdfstruct.models.document import BBox

from .test_ocr import make_pdf, require_engine, word_data


@pytest.mark.parametrize("field,value", [("offline", "yes"), ("model_dir", ""), ("tessdata_dir", 123)])
def test_asset_options_are_validated(field, value):
    with pytest.raises(ValueError):
        OCROptions(**{field: value})


def test_missing_models_fail_with_names_before_initialization(tmp_path):
    with pytest.raises(OCRDependencyError, match="PP-OCRv6_rec_small.onnx"):
        ocr_module._rapid_model_paths(tmp_path)


def test_custom_model_paths_are_exact(tmp_path):
    names = (
        "PP-OCRv6_det_small.onnx", "PP-OCRv6_rec_small.onnx",
        "ch_ppocr_mobile_v2.0_cls_mobile.onnx",
    )
    for name in names:
        (tmp_path / name).write_bytes(b"fixture")
    paths = ocr_module._rapid_model_paths(tmp_path)
    assert paths == dict(zip(("Det", "Rec", "Cls"), (tmp_path / name for name in names)))


def test_tesseract_missing_local_french_data_fails_before_engine(tmp_path):
    backend = ocr_module._TesseractBackend.__new__(ocr_module._TesseractBackend)
    backend.engine = SimpleNamespace()
    (tmp_path / "eng.traineddata").write_bytes(b"fixture")
    with pytest.raises(OCRDependencyError, match="fra"):
        backend._preflight(OCROptions(language="fra+eng", tessdata_dir=tmp_path))


def test_tesseract_model_directory_with_spaces(tmp_path):
    directory = tmp_path / "language data"
    directory.mkdir()
    for language in ("eng", "fra"):
        (directory / f"{language}.traineddata").write_bytes(b"fixture")
    calls = []
    backend = ocr_module._TesseractBackend.__new__(ocr_module._TesseractBackend)
    backend.engine = SimpleNamespace(
        get_languages=lambda config: calls.append(config) or ["eng", "fra"]
    )
    backend._preflight(OCROptions(language="fra+eng", tessdata_dir=directory))
    assert "--tessdata-dir" in calls[0]
    assert str(directory) in calls[0]


def test_inconsistent_backend_output_fails_instead_of_partial_success():
    data = word_data()
    data["conf"].pop()
    with pytest.raises(OCRError, match="inconsistent"):
        _data_to_lines(data, (1200, 800), 600, 400)


def _mock_region_clock(monkeypatch, durations):
    """Real renderer, deterministic engine/clock: no sleeps or slow timeouts."""
    pytest.importorskip("pypdfium2")
    clock = SimpleNamespace(now=100.0)
    calls = []
    times = iter(durations)

    def recognize(image, options):
        calls.append(options.timeout)
        clock.now += next(times)
        return word_data()

    monkeypatch.setattr(ocr_module, "monotonic", lambda: clock.now)
    monkeypatch.setattr(ocr_module, "_has_visible_ink", lambda image: True)
    monkeypatch.setattr(
        ocr_module, "assess_ocr_regions",
        lambda page, threshold: [BBox(0, 0, 300, 400), BBox(300, 0, 600, 400)],
    )
    monkeypatch.setattr(
        ocr_module, "_load_backend", lambda options: SimpleNamespace(recognize=recognize)
    )
    return calls


def test_tesseract_regions_share_one_page_deadline_without_partial_commit(monkeypatch):
    calls = _mock_region_clock(monkeypatch, [4, 7])
    source = make_pdf("scan")
    pages = list(extract_pages_raw(source))
    with pytest.raises(OCRError, match="page 1.*[Tt]imeout"):
        ocr_module.apply_ocr(
            source, pages, OCROptions(mode=True, backend="tesseract", dpi=72, timeout=10)
        )
    assert calls == [10, 6]
    assert pages[0].lines == []
    assert not pages[0].ocr_applied


def test_tesseract_deadline_resets_for_each_page(monkeypatch):
    calls = _mock_region_clock(monkeypatch, [3, 3, 3, 3])
    source = make_pdf("scan", "scan")
    pages = list(extract_pages_raw(source))
    ocr_module.apply_ocr(
        source, pages, OCROptions(mode=True, backend="tesseract", dpi=72, timeout=10)
    )
    assert calls == [10, 7, 10, 7]
    assert all(page.ocr_applied for page in pages)


@pytest.mark.ocr_integration
def test_real_rapidocr_offline_preflight_and_recognition(monkeypatch):
    require_engine("rapidocr")

    def network_forbidden(*args, **kwargs):
        raise AssertionError("OCR attempted an outbound network connection")

    monkeypatch.setattr(socket.socket, "connect", network_forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", network_forbidden)
    monkeypatch.setattr(socket, "create_connection", network_forbidden)
    doc = pdfstruct.parse(
        make_pdf("scan", scan_text="Facture Québec français 12345"),
        ocr=True, ocr_offline=True, ocr_dpi=200,
    )
    assert "Québec" in doc.text and "français" in doc.text
    assert "12345" in doc.text
    assert doc.pages[0].ocr_backend == "rapidocr"
    assert doc.pages[0].ocr_confidence is not None


@pytest.mark.ocr_integration
def test_real_tesseract_french_language_assets_and_amounts():
    require_engine("tesseract")
    directory = os.environ.get("PDFSTRUCT_TEST_TESSDATA")
    if directory is None:
        import pytesseract

        if "fra" not in pytesseract.get_languages():
            pytest.skip("Install fra data or set PDFSTRUCT_TEST_TESSDATA to a local data directory")
    doc = pdfstruct.parse(
        make_pdf("scan", scan_text="Facture Québec : énergie 12 345,67 $"),
        ocr=True, ocr_backend="tesseract", ocr_language="fra+eng",
        ocr_tessdata_dir=directory, ocr_offline=True, ocr_dpi=300,
    )
    assert "Québec" in doc.text
    assert "énergie" in doc.text
    assert "12345,67$" in re.sub(r"\s", "", doc.text)
    assert doc.pages[0].ocr_backend == "tesseract"
    assert all(
        span.level == "word" for item in doc.pages[0].lines for span in item.ocr_spans
    )
