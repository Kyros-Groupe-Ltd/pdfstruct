# pdfstructx

Local document extraction for Python applications. The PyPI distribution is
`pdfstructx`; the Python import is `pdfstruct`. Apache License 2.0.

The PDF parser extracts native text, font evidence, sections, tables and images.
Optional local OCR recognizes scanned regions. A separate ingestion API normalizes
PDF, DOCX and TXT into source-anchored blocks for search and retrieval pipelines.

## Install

```bash
pip install pdfstructx
pip install 'pdfstructx[documents]'            # optional DOCX ingestion
pip install 'pdfstructx[ocr]'                  # optional RapidOCR / ONNX OCR
pip install 'pdfstructx[tesseract]'            # optional Tesseract Python adapter
```

Python 3.10 or later is supported. The Tesseract option also needs the system
`tesseract` executable and the requested language files. OCR uses native runtimes;
the Python package does not bundle an operating-system sandbox or a cloud service.
To develop this checkout:

```bash
python -m venv .venv
.venv/bin/python -m pip install -e '.[dev,documents,ocr,tesseract]'
```

## PDF API

```python
import pdfstruct

pdf = pdfstruct.parse('report.pdf')
print(pdf.title, pdf.page_count, pdf.metadata.language)
for section in pdf.sections:
    print(section.heading, section.page_number, section.page_end)
for table in pdf.tables:
    print(table.page_number, table.to_list(), table.to_dicts())

json_text = pdfstruct.to_json(pdf)
markdown = pdfstruct.to_markdown(pdf)
plain_text = pdfstruct.to_text(pdf)
```

`parse()` accepts a local path, PDF bytes or a binary stream. A caller-owned
seekable stream is restored to its original position and is never closed.
It remains a PDF-specific API; use `parse_document()` for multiple formats.

## PDF, DOCX and TXT ingestion

```python
from pdfstruct import parse_document

source = parse_document('project-description.docx')
for block in source.blocks:
    print(block.id, block.kind, block.text, block.locator)

# Uploaded bytes require a filename for explicit format selection.
source = parse_document(
    'Le projet concerne le Québec.'.encode('utf-8'),
    filename='description.txt',
    max_bytes=25_000_000,
)
```

Block IDs combine the source content hash with a format-specific anchor. PDF
anchors include actual page numbers and boxes; DOCX uses paragraph/table/cell
anchors; TXT uses line ranges. DOCX and TXT do not receive invented PDF pages.
Treat document access permissions separately from its hash: identical bytes can
belong to different users, tenants or retention policies.

DOCX ingestion preserves body paragraphs/tables in order, heading-style evidence,
Unicode text and merged-cell anchors. It is not a Word rendering engine. Headers,
footers, text boxes, tracked changes and other unsupported constructs require
review; warnings describe known extraction limitations. TXT decoding is strict
UTF-8 by default, with UTF-16/32 BOM recognition and an explicit encoding override.
See [ingestion](docs/ingestion.md) for the complete contract and limits.

## Local OCR, including French

OCR is **off by default**. Automatic mode identifies raster regions that lack
usable native text, including a scanned body beneath a native header or an insert
beside native paragraphs. It preserves usable native content and reconciles
same-location duplicates.

```python
import pdfstruct

pdf = pdfstruct.parse('mixed-document.pdf', ocr='auto')
for page in pdf.pages:
    print(page.number, page.ocr_applied, page.ocr_backend, page.ocr_warnings)

# Explicit local French/English Tesseract configuration.
pdf = pdfstruct.parse(
    'scan.pdf',
    ocr='auto',
    ocr_backend='tesseract',
    ocr_language='fra+eng',
    ocr_tessdata_dir='/opt/ocr/tessdata',
    ocr_offline=True,
)

# Use the same options through the format adapter.
source = pdfstruct.parse_document(
    'scan.pdf',
    pdf_options={
        'ocr': 'auto',
        'ocr_backend': 'rapidocr',
        'ocr_model_dir': '/opt/ocr/rapidocr',
        'ocr_offline': True,
    },
)
```

The default backend runs PP-OCRv6 models through RapidOCR and ONNX Runtime.
Tesseract is an explicit alternative. `ocr_offline=True` checks local assets before
initialization; deployment-level egress controls must enforce network isolation.
Recognition runs locally and has no cloud OCR fallback. French model support does
not certify performance on every Canadian French vocabulary, handwriting or scan.

`ocr='always'` recognizes every nonblank page, preferring recovered OCR at overlap
locations while preserving unmatched native text. It does not blindly erase
native text when OCR returns nothing. Backend failures raise `OCRError`;
missing dependencies/assets raise `OCRDependencyError`; empty recognition and
unrecovered damaged text produce page warnings.

See [OCR configuration, dependencies, geometry and limits](docs/ocr.md).

## Structure, tables and evidence

- Native text retains PDF layout boxes and font evidence. OCR spans retain measured
  word/line boxes, polygons where available and engine confidence scores. Estimated
  character positions and fonts are explicitly marked.
- Page/paragraph/line/table identifiers and section page ranges support citations.
  Coordinates use the normalized pdfminer page layout, bottom-left origin, in
  points. Match the page transform when overlaying on a rendered viewer.
- Ruled tables use local closed-cell geometry, so separate grids on one page do
  not become a page-wide Cartesian table. Glyphs are assigned once to cells.
- Merged cells occur once at an anchor with `rowspan`/`colspan`. `to_list()` leaves
  covered positions empty. JSON preserves spans and boxes; Markdown cannot
  represent merged-cell layout faithfully.
- `to_dicts()` disambiguates duplicate or empty column labels instead of silently
  overwriting a value. Header inference and borderless tables remain heuristics.
- Structured exports emit each table once in its owning section. Page paragraphs
  still expose table text for compatibility and mark it with `table_id`; avoid
  independently indexing both that text and the canonical table blocks.

Heading detection, reading order, lists, language identification and header/footer
removal are heuristics. Raster OCR does not reconstruct drawn table rules or
supply a scanned-table structure model. Inspect `ocr_warnings`, table quality
flags and ingestion warnings before treating extracted information as reliable.
When native text crosses a table border, the parser preserves the complete text
and reports `table_text_crosses_boundary` instead of emitting a truncated table.
`Page.table_warnings` also reports table-work limits. Applications should route
these cases for review rather than silently treating them as table-free documents.

## Image extraction

```python
pdf = pdfstruct.parse('report.pdf', extract_images=True)
for page in pdf.pages:
    for image in page.images:
        print(image.width_px, image.height_px, image.format, image.dpi)
        print(image.caption, image.content_hash, image.is_duplicate)
        if not image.is_duplicate:
            image.save(f'image-{page.number}-{image.image_index}.png', fmt='PNG')
```

Image data, format, dimensions, inferred captions, hashes and estimated DPI are
available. `extract_image_data=False` omits raw image data; image extraction is
opt-in. `generate_thumbnail(image_bytes, max_size=(150, 150))` creates a thumbnail.

## Important parser options

| Option | Default | Behaviour |
| --- | --- | --- |
| `detect_tables` | `True` | Detect ruled and conservative borderless tables |
| `detect_headers_footers` | `True` | Remove likely repeated page decorations |
| `detect_lists` | `True` | Detect list structures |
| `detect_columns` | `True` | Infer multicolumn reading order |
| `extract_images` | `False` | Extract image metadata and optionally bytes |
| `extract_image_data` | `True` | Include bytes when image extraction is enabled |
| `ocr` | `False` | `True`/`'auto'` selects scan regions; `'always'` recognizes pages |
| `ocr_backend` | `'rapidocr'` | `'rapidocr'` or `'tesseract'` |
| `ocr_language` | `'eng'` | Tesseract languages, e.g. `'fra+eng'`; RapidOCR is multilingual |
| `ocr_dpi` | `300` | Render resolution, 72–600 DPI; bounded raster size |
| `ocr_min_text_chars` | `50` | Native-text routing signal; not a page-wide OCR veto |
| `ocr_timeout` | `60` | Tesseract recognition timeout; no in-process RapidOCR deadline |
| `ocr_model_dir` | `None` | Explicit local RapidOCR model directory |
| `ocr_tessdata_dir` | `None` | Explicit local Tesseract language directory |
| `ocr_offline` | `False` | Validate local assets before engine initialization |
| `table_detection_limits` | `None` | Optional `TableDetectionLimits` override; safe per-page defaults apply |

`PDFParser` additionally exposes layout grouping parameters. Configure deployment
limits for untrusted inputs; an in-process function cannot enforce a hard CPU,
memory or full-document wall-time limit. Use isolated worker processes, limit
concurrency, and terminate workers that exceed their job deadline. PDFium renderer
calls are serialized inside a process; use processes for parallel OCR throughput.

## Development and release

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m pytest -m ocr_integration -q
.venv/bin/python -m build
.venv/bin/python -m twine check --strict dist/*
```

Integration tests need installed engines and local model assets. The release
workflow explicitly requires real English/French OCR rather than treating skipped
integration tests as a sufficient release check. It builds once, tests the same
wheel on Python 3.10–3.13, then publishes that artifact only on a matching version
tag. See [the release procedure](docs/releasing.md), [validation](docs/validation.md)
and [changes in 0.3.0](CHANGELOG.md).

## License

The project code is Apache-2.0; retain its license and notices when redistributing.
Optional engines, renderers, model files and bundled native dependencies have their
own licenses and notices. See [LICENSE](LICENSE) and [OCR dependencies](docs/ocr.md).
Contributions: [CONTRIBUTING.md](CONTRIBUTING.md).
