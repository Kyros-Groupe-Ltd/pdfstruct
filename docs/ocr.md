# Scanned-document OCR

OCR is opt-in. Existing calls to `pdfstruct.parse(source)` keep using native PDF
text extraction and do not import or initialize either OCR engine. Inputs can
be paths (including `pathlib.Path`), PDF bytes, or binary streams. Seekable
caller-owned streams remain open and at their original position.

## Default backend: PaddleOCR models via RapidOCR

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

The supported RapidOCR wheels include the selected ONNX models. No model weights
are vendored into this repository. pdfstruct resolves the three local model
paths explicitly and fails if any is missing; it does not download missing model
weights. Pin resolved dependencies and model artifacts when building a worker.
The two engines have not been ranked for accuracy on customer documents; choose
using representative inputs, error rates and measured resource usage.

### Explicit local assets and offline preflight

```python
doc = pdfstruct.parse(
    "scan.pdf", ocr="auto", ocr_backend="rapidocr",
    ocr_model_dir="/opt/pdfstruct/models", ocr_offline=True,
)
```

`ocr_model_dir` is optional; its default is RapidOCR's installed `models`
directory. A custom directory must contain these exact files:

- `PP-OCRv6_det_small.onnx`
- `PP-OCRv6_rec_small.onnx`
- `ch_ppocr_mobile_v2.0_cls_mobile.onnx`

The recognition model normally embeds its character dictionary. When
`ocr_offline=True`, preflight checks this before initializing RapidOCR. If a
substitute recognition model has no character metadata, provide `rec_keys.txt`
in the same directory. A missing dictionary fails instead of permitting
RapidOCR's dictionary-download fallback. Preflight loads the recognition model
once to inspect its metadata, adding startup work to the normal engine load.

Offline preflight is an asset check, not a network sandbox. Enforce the required
network policy on the worker/container and verify the exact built image with
networking denied. Do not enable remote document, logging or storage paths merely
because recognition itself runs locally. Model substitutions need compatible
architecture, dictionaries, licenses and separate validation.

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
    ocr_language="fra+eng", ocr_dpi=300, ocr_timeout=60,
    ocr_tessdata_dir="/opt/pdfstruct/tessdata",
    ocr_offline=True,
)
```

The optional `ocr_tessdata_dir` must contain `fra.traineddata` and
`eng.traineddata` for that example. Without it, Tesseract uses its installed
language-data location. Preflight checks the requested languages; the backend
uses the LSTM engine (`--oem 1`). French and English data can be obtained from the
official `tessdata_fast` or `tessdata_best` repositories and pinned during image
construction. Neither recognition mode downloads Tesseract language data.

Use `chi_sim` / `chi_tra` with their corresponding Tesseract language packs for
simplified / traditional Chinese. The backend is explicit: missing RapidOCR does
not silently switch to Tesseract, and failures do not return a partial document.

## Page selection and provenance

| Mode | Behavior |
| --- | --- |
| `ocr=False` (default) | Native extraction only; scan detection metadata is still populated |
| `ocr=True` or `ocr="auto"` | Assess each raster region for usable native text; recognize selected regions and preserve native content |
| `ocr="always"` | Re-recognize nonblank pages; prefer recovered OCR at overlapping locations and retain unmatched native content |

Automatic selection handles image-only pages, a digital header above a scanned
body, small scanned inserts beside native text, and documents mixing page types.
Native text elsewhere on the page cannot suppress a scanned insert. Regions are
clipped and overlapping/tiled images are merged before recognition. Small images
are ignored as likely logos: selected regions must occupy at least 2% of the page
and 1,800 square PDF units, with dimensions of at least 120 by 24 units in either
orientation. These are conservative heuristics, not a visual content classifier.

`ocr_min_text_chars` applies to usable text inside each image region. In regions
covering at least half the page height, the outer 12% at the top/bottom does not
count as a usable body text layer. Obvious broken character maps (replacement
characters, private-use glyphs, repeated `(cid:...)` placeholders) trigger OCR.
Unicode accents and non-Latin text remain valid. A layer containing plausible
but incorrect words cannot be detected reliably; use force mode to reassess it.

Blank/native-only pages and small logos do not initialize an OCR engine in auto
mode. A raster region rendered as uniform white/solid color is skipped before
engine initialization. Photos or large logos can still be selected, and scanned
text below the size thresholds can be missed. Neither engine promises arbitrary
photograph, handwriting or malformed-PDF recovery.

Native text is preserved when OCR produces no corresponding replacement. Auto
mode prefers usable native text at a duplicate location; force mode prefers
recognized text there. Reconciliation uses position and text similarity. Inspect
ambiguous layouts and low-quality recognition rather than treating any successful
parse as evidence that every field was recovered.

Page properties and JSON export include `is_scanned`, `ocr_applied`,
`ocr_backend`, `ocr_confidence`, `ocr_regions`, and `ocr_warnings`. An empty OCR
result adds a warning and does not erase native text. Backend/rendering errors
raise rather than returning a partially successful document.

Line objects expose `source`, `ocr_confidence`, and `ocr_spans`. Each OCR span
contains the engine's text, box/polygon, confidence and granularity: Tesseract
supplies word spans; RapidOCR supplies detected text-line spans. These measured
spans are the evidence to use for highlighting. Generated per-character positions
are explicitly marked `geometry_is_estimated`; the synthetic `OCR` font has
`is_estimated=True`. They are layout aids, not original typography or measured
character positions.
Document metadata includes `scanned_page_count`, `ocr_page_count`, and
`ocr_applied`. `is_scanned` means the document contains at least one page flagged
by the **pre-OCR heuristic**; it is not reset when recognition succeeds, and is
not a claim that every page was scanned. A forced-OCR digital page can have
`ocr_applied=True` and `is_scanned=False`.

Confidence is a character-weighted mean on a 0-100 scale. It is an engine score,
not a calibrated probability; scores from different engines are not directly
comparable. Retain low-confidence output for review rather than silently
discarding content. OCR boxes use the same bottom-left PDF coordinate system as
native extraction. Rendering is normalized to pdfminer's full MediaBox,
including a nonzero origin and inherited PDF rotation. The source CropBox does
not restrict this extraction frame. Region offsets are restored before mapping
pixels to page coordinates; the original PDF is never changed. This does not
perform arbitrary deskewing, so no undocumented deskew transform is applied.

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
- `ocr_timeout` is a shared **Tesseract recognition deadline per page**, starting
  before the first region is recognized. Each subsequent region receives only
  the remaining time, and a timed-out Tesseract subprocess is terminated. PDF
  rendering and engine initialization are outside this recognition deadline.
  RapidOCR runs in-process and has no per-page hard deadline. For untrusted jobs,
  run the whole parser in an isolated worker with an external timeout and memory
  limit.
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
| Tesseract | [Apache-2.0](https://github.com/tesseract-ocr/tesseract/blob/main/LICENSE); Leptonica and image-library dependencies retain their own notices |
| Official Tesseract language weights | [tessdata_fast](https://github.com/tesseract-ocr/tessdata_fast) / [tessdata_best](https://github.com/tesseract-ocr/tessdata_best), Apache-2.0 |
| pytesseract | [Apache-2.0](https://github.com/madmaze/pytesseract/blob/master/LICENSE) |

## Testing

```bash
pip install -e ".[dev,ocr,tesseract]"
pytest -m "not ocr_integration" --tb=short -q
pytest -m ocr_integration --tb=short -q
# When French/English Tesseract data are stored outside the system directory:
PDFSTRUCT_TEST_TESSDATA=/opt/pdfstruct/tessdata pytest -m ocr_integration -q
```

The suite generates image-only, native, mixed, small-insert, blank, cropped and
rotated PDFs. Geometry tests include nonzero MediaBox origins. Real RapidOCR and
Tesseract smoke tests cover recovery without losing native content. Adapter and
failure-path tests exercise measured spans, confidence, malformed backend output,
missing local assets, render limits and timeouts. The RapidOCR offline test blocks
Python socket connections while recognizing French text; deployment network
controls must still be verified separately. Integration tests skip when an engine
is absent. These are regression checks, not an accuracy benchmark on customer
documents. Validate representative production scans before release.
