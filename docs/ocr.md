# Scanned-document OCR

OCR is opt-in. Existing calls to `pdfstruct.parse(source)` keep using native PDF
text extraction and do not import or initialize either OCR engine. Inputs can
be paths (including `pathlib.Path`), PDF bytes, or binary streams. Seekable
caller-owned streams remain open and at their original position.

## Recommended backend: PaddleOCR models via RapidOCR

```bash
pip install "pdfstructx[ocr]"
# Development checkout:
pip install -e ".[dev,ocr]"
```

```python
import pdfstruct

doc = pdfstruct.parse("scan.pdf", ocr=True)
# Equivalent reusable parser configuration:
parser = pdfstruct.PDFParser(ocr="auto", ocr_backend="rapidocr", ocr_dpi=300)
doc = parser.parse("scan.pdf")
```

The backend explicitly selects **PP-OCRv6 small** detection and recognition
models, with the bundled PP-OCR text-line orientation classifier. These are
PaddleOCR neural models deployed through RapidOCR and ONNX Runtime on CPU, not
an API or a generative model. The multilingual recognition model handles Chinese,
English, Japanese and Latin-script languages including French without switching
language packs. `ocr_language` only configures the Tesseract backend.
If full-page detection returns no text on a sparse page, a single whitespace-crop
retry recovers small text; the resulting boxes are mapped back to the full page.

RapidOCR 3.9.2 includes the selected ONNX models in its wheel. No model weights
are vendored into this repository. Pin your resolved dependencies and provision
the models during deployment; test your own built image with networking disabled
before relying on offline operation. Recognition is local and pdfstruct never
sends document contents to a service.

## Alternative backend: Tesseract

```bash
pip install "pdfstructx[tesseract]"
# Ubuntu/Debian, including English and French language data:
sudo apt-get install tesseract-ocr tesseract-ocr-eng tesseract-ocr-fra
# macOS:
brew install tesseract tesseract-lang
```

On Windows, install Tesseract using the upstream installation guidance and put
its executable on PATH. For a non-standard installation, configure
`pytesseract.pytesseract.tesseract_cmd` before parsing.

```python
doc = pdfstruct.parse(
    "scan.pdf", ocr=True, ocr_backend="tesseract",
    ocr_language="eng+fra", ocr_dpi=300, ocr_timeout=60,
)
```

Use `chi_sim` / `chi_tra` with their corresponding Tesseract language packs for
simplified / traditional Chinese. The backend is explicit: missing RapidOCR does
not silently switch to Tesseract, and failures do not return a partial document.

## Page selection and provenance

| Mode | Behavior |
| --- | --- |
| `ocr=False` (default) | Native extraction only; scan detection metadata is still populated |
| `ocr=True` or `ocr="auto"` | OCR pages with fewer than `ocr_min_text_chars` native non-whitespace characters and raster images covering at least half the page |
| `ocr="always"` | OCR every page, replacing native text rather than appending duplicates |

Automatic selection handles image-only pages, scanned pages with a small digital
page number, and documents mixing scans with native text. Blank pages, short
digital pages, logos, vector-only figures and already-searchable pages are not
automatically OCRed. The raster coverage calculation clips images to the page
and avoids double-counting overlapping images. Photos can still trigger OCR;
an empty recognition result is logged and has `ocr_confidence=None`.

The auto heuristic cannot reliably identify a damaged text layer that already
contains many characters, image fragments covering less than half a page, or
text converted to vector outlines. Use `ocr="always"` in those cases.

Page properties and JSON export include `is_scanned`, `ocr_applied`,
`ocr_backend`, and `ocr_confidence`. Line objects expose `ocr_confidence` too.
Document metadata includes `scanned_page_count`, `ocr_page_count`, and
`ocr_applied`. `is_scanned` means the document contains at least one page flagged
by the **pre-OCR heuristic**; it is not reset when recognition succeeds, and is
not a claim that every page was scanned. A forced-OCR digital page can have
`ocr_applied=True` and `is_scanned=False`.

Confidence is a character-weighted mean on a 0-100 scale. It is an engine score,
not a calibrated probability; scores from different engines are not directly
comparable. Retain low-confidence output for review rather than silently
discarding content. OCR boxes use the same bottom-left PDF coordinate system as
native extraction, accounting for the MediaBox, CropBox and PDF rotation.

## Limits and production use

- The base API remains PDF-only; wrap image files in a PDF before parsing.
- OCR supplies text and bounding boxes to the existing structure analysis and
  Markdown/JSON/text exports. Font names are `OCR`; font sizes and character
  positions are estimates, not original typography.
- Skewed text crops and upside-down text lines benefit from RapidOCR's detector
  and classifier. PDF `/Rotate` is respected. This is not a general page deskew,
  dewarping, handwriting, or arbitrary photograph correction system.
- Existing table and heading heuristics are reused. Raster table grid lines
  are not reconstructed; complex scanned tables, merged cells and exact layout
  fidelity need separate table/layout models and representative evaluation.
- Rendering is limited to 40 million pixels per page (before allocation), and
  DPI must be an integer between 72 and 600. Reduce DPI for oversized pages.
- `ocr_timeout` kills a timed-out **Tesseract** subprocess. RapidOCR runs
  in-process and has no per-page hard deadline. For untrusted jobs, run the
  whole parser in an isolated worker with an external timeout and memory limit.
- PDFium operations in this module are serialized because PDFium is not
  thread-safe. Use separate processes for parallel OCR. Other application code
  using PDFium must obey the same thread-safety requirement.
- One engine is initialized per parse and reused for all selected pages. Native
  extraction remains document-wide; although rendered images are released per
  page, the parser is not constant-memory for arbitrarily large PDFs.
- `OCRDependencyError` identifies missing packages/executables. `OCRError`
  identifies initialization, rendering and recognition failures, including the
  page number for page-specific failures. Missing Tesseract language data is
  surfaced through that error. The original PDF is never modified.

## Licenses

The added backends and renderer are open-source with permissive upstream
licenses; no AGPL PDF/OCR engine is introduced. Attribution and redistribution
obligations still apply. Preserve upstream notices when shipping dependencies
or model artifacts, and re-check licenses if substituting other models.

| Component | Upstream license / reference |
| --- | --- |
| pdfstruct | [Apache-2.0](../LICENSE) |
| RapidOCR code and converted PaddleOCR model weights | [Apache-2.0, upstream code/model statement](https://github.com/RapidAI/RapidOCR#%EF%B8%8F-license) |
| PaddleOCR | [Apache-2.0](https://github.com/PaddlePaddle/PaddleOCR/blob/main/LICENSE) |
| ONNX Runtime | [MIT](https://github.com/microsoft/onnxruntime/blob/main/LICENSE) |
| pypdfium2 / PDFium | [Apache-2.0/BSD-3-Clause bindings; BSD-style PDFium and bundled third-party notices](https://github.com/pypdfium2-team/pypdfium2#licensing) |
| Tesseract | [Apache-2.0](https://github.com/tesseract-ocr/tesseract/blob/main/LICENSE) |
| pytesseract | [Apache-2.0](https://github.com/madmaze/pytesseract/blob/master/LICENSE) |

## Testing

```bash
pip install -e ".[dev,ocr,tesseract]"
pytest -m "not ocr_integration" --tb=short -q
pytest -m ocr_integration --tb=short -q
```

The suite generates synthetic image-only, native, blank, cropped and rotated
PDFs. Real RapidOCR and Tesseract smoke tests verify text recovery, provenance,
exports and non-duplication. Adapter and failure-path tests cover confidence,
coordinates, language/timeout forwarding, missing dependencies and render
limits. Integration tests skip when an engine is absent; the dedicated CI job
installs both engines. These are regression tests, not an accuracy benchmark on
customer documents. Validate representative production scans before release.
