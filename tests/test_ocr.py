"""Scan detection, source handling and OCR adapters, plus real-engine smoke tests."""

import builtins
import io
import json
import shutil
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

import pdfstruct
from pdfstruct.extractors import ocr as ocr_module
from pdfstruct.extractors.ocr import (
    OCRDependencyError,
    OCRError,
    OCROptions,
    _data_to_lines,
    is_scanned_page,
)
from pdfstruct.extractors.text import extract_pages_raw
from pdfstruct.models.document import BBox, FontInfo, TextLine

SCAN_TEXT = "Scanned invoice number 12345"
NATIVE_TEXT = "This native digital page contains enough text to avoid unnecessary OCR processing."


def make_pdf(*kinds, rotation=0, crop=False, scan_text=SCAN_TEXT):
    """Generate small PDFs in memory; no committed binary fixtures are needed."""
    output = io.BytesIO()
    pdf = canvas.Canvas(output, pagesize=(600, 400))
    pdf.setTitle("OCR regression fixture")
    pdf.setPageRotation(rotation)
    if crop:
        pdf.setCropBox((100, 100, 500, 300))
    for kind in kinds:
        if kind in (
            "scan", "sparse_scan", "searchable", "header_scan", "small_scan",
            "logo", "tiled", "overlap", "blank_scan",
        ):
            image = Image.new("RGB", (1200, 800), "white")
            draw = ImageDraw.Draw(image)
            try:
                font = ImageFont.truetype("DejaVuSans.ttf", 40)
            except OSError:
                font = ImageFont.load_default()
            if kind != "blank_scan":
                draw.text((100, 160), scan_text, fill="black", font=font)
                draw.text((100, 240), "Amount due 250 dollars", fill="black", font=font)
                draw.text((100, 320), "Please contact our finance team", fill="black", font=font)
            reader = ImageReader(image)
            if kind == "logo":
                pdf.drawImage(reader, 20, 20, width=60, height=40)
            elif kind == "small_scan":
                pdf.drawImage(reader, 180, 120, width=240, height=160)
            elif kind == "tiled":
                pdf.drawImage(reader, 0, 0, width=300, height=400)
                pdf.drawImage(reader, 300, 0, width=300, height=400)
            elif kind == "overlap":
                for _ in range(3):
                    pdf.drawImage(reader, 0, 0, width=240, height=400)
            else:
                pdf.drawImage(reader, 0, 0, width=600, height=400)
        if kind in ("native", "header_scan", "small_scan"):
            pdf.drawString(50, 350, NATIVE_TEXT)
        if kind == "searchable":
            # A complete usable text layer in the image body, not just a header.
            pdf.drawString(50, 300, scan_text)
            pdf.drawString(50, 260, "Amount due 250 dollars")
            pdf.drawString(50, 220, "Please contact our finance team")
        if kind in ("short", "sparse_scan"):
            pdf.drawString(50, 30, "Page 1")
        if kind == "vector":
            pdf.beginForm("drawing")
            pdf.rect(0, 0, 600, 400)
            pdf.endForm()
            pdf.doForm("drawing")
        pdf.showPage()
    pdf.save()
    return output.getvalue()


def word_data():
    return {
        "text": ["", "Hello", "world", "ignored"],
        "conf": [-1, 90, 80, -1],
        "left": [0, 20, 100, 0],
        "top": [0, 40, 40, 0],
        "width": [0, 50, 50, 0],
        "height": [0, 20, 20, 0],
        "block_num": [0, 1, 1, 1],
        "par_num": [0, 1, 1, 1],
        "line_num": [0, 1, 1, 1],
    }


@pytest.mark.parametrize(
    "kind, expected",
    [
        ("scan", True),
        ("sparse_scan", True),
        ("tiled", True),
        ("native", False),
        ("searchable", False),
        ("header_scan", True),
        ("small_scan", True),
        ("short", False),
        ("blank", False),
        ("logo", False),
        ("overlap", True),  # One real 40%-page region still needs recognition.
        ("vector", False),
    ],
)
def test_scan_detection(kind, expected):
    page = next(extract_pages_raw(make_pdf(kind)))
    assert is_scanned_page(page) is expected


def test_zero_threshold_still_recognizes_image_only_pages():
    assert is_scanned_page(next(extract_pages_raw(make_pdf("scan"))), 0)
    # A footer does not make the raster body searchable, even with threshold=0.
    assert is_scanned_page(next(extract_pages_raw(make_pdf("sparse_scan"))), 0)
    assert not is_scanned_page(next(extract_pages_raw(make_pdf("searchable"))), 0)


@pytest.mark.parametrize(
    "option,value",
    [
        ("ocr", "sometimes"),
        ("ocr", 1),
        ("ocr", None),
        ("ocr_backend", "cloud"),
        ("ocr_dpi", 0),
        ("ocr_dpi", 601),
        ("ocr_dpi", True),
        ("ocr_dpi", 150.5),
        ("ocr_min_text_chars", -1),
        ("ocr_min_text_chars", True),
        ("ocr_timeout", 0),
        ("ocr_timeout", float("nan")),
        ("ocr_timeout", float("inf")),
        ("ocr_timeout", True),
        ("ocr_language", ""),
        ("ocr_language", "eng --psm 6"),
    ],
)
def test_invalid_options(option, value):
    with pytest.raises(ValueError):
        pdfstruct.PDFParser(**{option: value})


@pytest.mark.parametrize("mode", [False, True, "auto", "always"])
def test_valid_modes(mode):
    assert OCROptions(mode=mode).mode == mode


def test_word_positions_and_confidence():
    lines, confidence = _data_to_lines(word_data(), (1200, 800), 600, 400)
    assert len(lines) == 1
    line = lines[0]
    assert line.text == "Hello world"
    assert line.bbox == BBox(10, 370, 75, 380)
    assert line.font.name == "OCR"
    assert line.font.size == 10
    assert "".join(char.text for char in line.characters) == "Hello world"
    assert line.ocr_confidence == confidence == 85
    assert line.to_dict()["ocr_confidence"] == 85
    assert line.source == "ocr"
    assert line.geometry_is_estimated and line.font.is_estimated
    assert all(char.geometry_is_estimated for char in line.characters)
    assert [span.text for span in line.ocr_spans] == ["Hello", "world"]
    assert [span.level for span in line.ocr_spans] == ["word", "word"]
    assert line.ocr_spans[0].bbox == BBox(10, 370, 35, 380)
    assert line.ocr_spans[0].confidence == 90


def test_line_grouping_includes_block_and_paragraph():
    data = word_data()
    data["block_num"][2] = 2
    assert len(_data_to_lines(data, (1200, 800), 600, 400)[0]) == 2


def test_no_ocr_text_has_no_confidence():
    data = word_data()
    data["text"] = [""] * 4
    assert _data_to_lines(data, (1200, 800), 600, 400) == ([], None)


def test_ocr_disabled_and_digital_auto_do_not_load_dependencies(monkeypatch):
    def unexpected(*args):
        pytest.fail("OCR dependencies must be lazy")

    monkeypatch.setattr(ocr_module, "_load_pdfium", unexpected)
    monkeypatch.setattr(ocr_module, "_load_backend", unexpected)
    scanned = pdfstruct.parse(make_pdf("scan"))
    assert scanned.metadata.is_scanned
    assert not scanned.metadata.ocr_applied
    assert not scanned.text
    native = make_pdf("native", "short", "blank")
    assert pdfstruct.parse(native, ocr=True).text == pdfstruct.parse(native).text


def test_missing_renderer_is_actionable(monkeypatch):
    original_import = builtins.__import__

    def no_pdfium(name, *args, **kwargs):
        if name == "pypdfium2":
            raise ImportError("missing test dependency")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_pdfium)
    with pytest.raises(OCRDependencyError, match="pypdfium2"):
        pdfstruct.parse(make_pdf("scan"), ocr=True)


@pytest.mark.parametrize(
    "module,backend,extra",
    [
        ("rapidocr", "_RapidOCRBackend", "ocr"),
        ("pytesseract", "_TesseractBackend", "tesseract"),
    ],
)
def test_missing_engine_is_actionable(monkeypatch, module, backend, extra):
    original_import = builtins.__import__

    def missing(name, *args, **kwargs):
        if name == module:
            raise ImportError("missing test dependency")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", missing)
    with pytest.raises(OCRDependencyError, match=rf"pdfstructx\[{extra}\]"):
        getattr(ocr_module, backend)()


def test_tesseract_configuration_and_missing_executable(monkeypatch):
    calls = []

    class MissingExecutable(Exception):
        pass

    engine = ocr_module._TesseractBackend.__new__(ocr_module._TesseractBackend)

    def recognize(image, **kwargs):
        calls.append(kwargs)
        return word_data()

    engine.engine = SimpleNamespace(
        image_to_data=recognize,
        Output=SimpleNamespace(DICT="dict"),
        TesseractNotFoundError=MissingExecutable,
    )
    engine.recognize(None, OCROptions(language="eng+fra", dpi=200, timeout=5))
    assert calls == [
        {"lang": "eng+fra", "config": "--dpi 200 --oem 1", "output_type": "dict", "timeout": 5}
    ]

    def missing(*args, **kwargs):
        raise MissingExecutable()

    engine.engine.image_to_data = missing
    with pytest.raises(OCRDependencyError, match="PATH"):
        engine.recognize(None, OCROptions())


def test_stream_position_and_ownership():
    source = io.BytesIO(make_pdf("native"))
    source.seek(31)
    document = pdfstruct.parse(source)
    assert NATIVE_TEXT in document.text
    assert source.tell() == 31
    assert not source.closed


def test_nonseekable_input():
    class ReadOnlyStream:
        def read(self):
            return make_pdf("native")

    assert NATIVE_TEXT in pdfstruct.parse(ReadOnlyStream()).text


def test_text_stream_rejected():
    with pytest.raises(TypeError, match="binary"):
        pdfstruct.parse(io.StringIO("not binary"))


def test_pathlib_source(tmp_path):
    path = tmp_path / "digital.pdf"
    path.write_bytes(make_pdf("native"))
    assert NATIVE_TEXT in pdfstruct.parse(path).text


def test_mixed_metadata_is_based_on_pre_ocr_pages(monkeypatch):
    def fake_ocr(source, pages, options):
        for page in pages:
            if page.is_scanned:
                page.lines = [
                    TextLine(
                        "Recognized scan with plenty of words " * 5,
                        BBox(50, 300, 550, 330),
                        FontInfo("OCR", 12),
                    )
                ]
                page.ocr_applied = True
                page.ocr_backend = options.backend
                page.ocr_confidence = 92.5

    monkeypatch.setattr("pdfstruct.parser.apply_ocr", fake_ocr)
    doc = pdfstruct.parse(make_pdf("native", "scan", "blank"), ocr=True)
    assert doc.metadata.scanned_page_count == doc.metadata.ocr_page_count == 1
    assert doc.metadata.is_scanned and doc.metadata.ocr_applied
    assert [page.ocr_applied for page in doc.pages] == [False, True, False]
    assert doc.pages[1].to_dict()["ocr_confidence"] == 92.5
    exported = json.loads(pdfstruct.to_json(doc))
    assert exported["metadata"]["statistics"]["ocr_page_count"] == 1
    assert "Recognized scan" in pdfstruct.to_markdown(doc)
    assert "Recognized scan" in pdfstruct.to_text(doc)


def test_rapidocr_adapter_coordinates_and_bgr():
    np = pytest.importorskip("numpy")
    engine = ocr_module._RapidOCRBackend.__new__(ocr_module._RapidOCRBackend)

    def recognize(array):
        assert list(array[0, 0]) == [30, 20, 10]
        return SimpleNamespace(
            txts=("中文 English français",),
            scores=(0.94,),
            boxes=np.array([[[10, 20], [110, 20], [110, 40], [10, 40]]]),
        )

    engine.engine = recognize
    with Image.new("RGB", (200, 100), (10, 20, 30)) as image:
        data = engine.recognize(image, OCROptions())
    lines, confidence = _data_to_lines(data, (200, 100), 600, 300)
    assert lines[0].text == "中文 English français"
    assert lines[0].bbox == BBox(30, 180, 330, 240)
    assert confidence == pytest.approx(94)
    assert lines[0].ocr_spans[0].level == "line"
    assert lines[0].ocr_spans[0].polygon == [(30, 240), (330, 240), (330, 180), (30, 180)]


def require_engine(backend):
    pytest.importorskip("pypdfium2")
    if backend == "rapidocr":
        pytest.importorskip("rapidocr")
        pytest.importorskip("onnxruntime")
    else:
        pytest.importorskip("pytesseract")
        if not shutil.which("tesseract"):
            pytest.skip("Tesseract executable not installed")


def test_sparse_retry_restores_full_page_coordinates():
    np = pytest.importorskip("numpy")
    calls = []
    engine = ocr_module._RapidOCRBackend.__new__(ocr_module._RapidOCRBackend)

    def recognize(array):
        calls.append(array.shape)
        if len(calls) == 1:
            return SimpleNamespace(txts=None, boxes=None, scores=None)
        return SimpleNamespace(
            txts=("Text",),
            scores=(0.9,),
            boxes=np.array([[[32, 32], [82, 32], [82, 52], [32, 52]]]),
        )

    engine.engine = recognize
    with Image.new("RGB", (600, 400), "white") as image:
        ImageDraw.Draw(image).rectangle((100, 200, 150, 220), fill="black")
        data = engine.recognize(image, OCROptions())
    assert len(calls) == 2
    assert data["left"] == [100]
    assert data["top"] == [200]


@pytest.mark.ocr_integration
def test_real_rapidocr_french_accents():
    require_engine("rapidocr")
    doc = pdfstruct.parse(
        make_pdf("scan", scan_text="Facture Québec français 12345"), ocr=True, ocr_dpi=200
    )
    assert "Québec" in doc.text
    assert "français" in doc.text


@pytest.mark.ocr_integration
@pytest.mark.parametrize("backend", ["rapidocr", "tesseract"])
def test_real_ocr_mixed_pdf(backend):
    require_engine(backend)
    doc = pdfstruct.parse(
        make_pdf("native", "scan", "blank"), ocr=True, ocr_backend=backend, ocr_dpi=150
    )
    assert doc.pages[0].text == NATIVE_TEXT
    assert "12345" in doc.pages[1].text
    assert "250" in doc.pages[1].text
    assert not doc.pages[2].text
    assert [page.ocr_applied for page in doc.pages] == [False, True, False]
    assert doc.metadata.ocr_page_count == 1
    assert doc.pages[1].ocr_confidence > 50
    assert doc.pages[1].ocr_backend == backend
    assert doc.sections and doc.pages[1].paragraphs
    assert "12345" in pdfstruct.to_markdown(doc)
    assert "12345" in pdfstruct.to_text(doc)
    for line in doc.pages[1].lines:
        assert 0 <= line.bbox.x0 < line.bbox.x1 <= 600
        assert 0 <= line.bbox.y0 < line.bbox.y1 <= 400


@pytest.mark.ocr_integration
@pytest.mark.parametrize("backend", ["rapidocr", "tesseract"])
def test_real_force_ocr_does_not_duplicate_native_text(backend):
    require_engine(backend)
    doc = pdfstruct.parse(make_pdf("short"), ocr="always", ocr_backend=backend, ocr_dpi=200)
    assert doc.text.count("Page 1") == 1
    assert doc.metadata.ocr_page_count == 1
    assert not doc.metadata.is_scanned


@pytest.mark.ocr_integration
def test_real_tesseract_timeout():
    require_engine("tesseract")
    with pytest.raises(OCRError, match="page 1.*[Tt]imeout"):
        pdfstruct.parse(make_pdf("scan"), ocr=True, ocr_backend="tesseract", ocr_timeout=0.000001)


@pytest.mark.ocr_integration
def test_real_ocr_keeps_images_and_caller_stream():
    require_engine("rapidocr")
    source = io.BytesIO(make_pdf("scan"))
    source.seek(12)
    doc = pdfstruct.parse(source, ocr=True, extract_images=True, ocr_dpi=150)
    assert "12345" in doc.text
    assert doc.pages[0].images[0].image_bytes
    assert doc.metadata.image_count == 1
    assert source.tell() == 12
    assert not source.closed


@pytest.mark.ocr_integration
def test_backend_failure_is_not_silenced(monkeypatch):
    pytest.importorskip("pypdfium2")

    def fail(*args):
        raise RuntimeError("recognizer timeout")

    monkeypatch.setattr(
        ocr_module, "_load_backend", lambda options: SimpleNamespace(recognize=fail)
    )
    with pytest.raises(OCRError, match="page 1.*timeout"):
        pdfstruct.parse(make_pdf("scan"), ocr=True)


@pytest.mark.ocr_integration
def test_render_pixel_limit(monkeypatch):
    pytest.importorskip("pypdfium2")
    monkeypatch.setattr(ocr_module, "MAX_RENDER_PIXELS", 100)
    monkeypatch.setattr(ocr_module, "_load_backend", lambda options: None)
    with pytest.raises(OCRError, match="lower ocr_dpi"):
        pdfstruct.parse(make_pdf("scan"), ocr=True)


@pytest.mark.ocr_integration
@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_render_geometry_matches_pdfminer(monkeypatch, rotation):
    pytest.importorskip("pypdfium2")
    sizes = []

    def recognize(image, options):
        sizes.append(image.size)
        return word_data()

    monkeypatch.setattr(
        ocr_module, "_load_backend", lambda options: SimpleNamespace(recognize=recognize)
    )
    doc = pdfstruct.parse(make_pdf("scan", rotation=rotation, crop=True), ocr="always", ocr_dpi=72)
    assert sizes == [(int(doc.pages[0].width), int(doc.pages[0].height))]
    assert doc.pages[0].lines[0].bbox.x0 == 20
    assert doc.pages[0].lines[0].bbox.y1 == doc.pages[0].height - 40
