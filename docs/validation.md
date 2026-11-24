# Validation and deployment contract

This library is reusable across applications. Extraction tests establish specific
behaviours; they do not certify document accuracy, privacy-law compliance or an
entire application's acceptance criteria.

## Verification layers

| Layer | What is checked | What it does not establish |
| --- | --- | --- |
| Native extraction | Synthetic and representative PDFs retain expected text, table regions and source references | Fidelity for every PDF producer or malformed encoding |
| Tables | Independent/adjacent grids, fragmented Word-style rules, cell ownership, spans, duplicate headers, conservative whitespace fallback | General nested or broken tables, raster-table reconstruction |
| OCR routing | Native pages, scanned bodies with native headers, small inserts, broken character maps, blank regions, duplicates | Perfect image classification or detection of plausible but incorrect hidden OCR |
| Real recognition | Actual RapidOCR and Tesseract; French accents and Canadian-style amounts | Corpus-wide character/word accuracy or handwriting support |
| Geometry | Rotations, MediaBox origins, CropBox differences, measured word/line geometry | Original OCR font recovery or exact interpolated glyph coordinates |
| Input safety | Byte/block/table/ZIP limits; XML declaration checks; malformed-input errors | A substitute for OS isolation, antivirus or CPU/memory limits |
| Export | Actual source anchors, one table emission, no list-continuation loss, manual-model compatibility | Exact Word/PDF visual layout in Markdown |
| Packaging | Exact artifact metadata/version, installed-wheel smoke tests, required OCR release gate | A successful upload before a matching tag workflow has run |

The original OCR PR baseline passed 104 tests in the local Python 3.12 environment.
The release adds regression tests for the reviewed failure modes. The authoritative
current test count is the result of `pytest`, because more regression tests may be
added after this document is written. CI evaluates Python 3.10 through 3.13; a local
Python 3.12 result alone is not evidence that all those jobs have passed.

## Evaluation fixtures

Repository tests use generated, synthetic documents with explicit expectations
for text, layout, provenance and failure behaviour. They can be reproduced without
customer documents. Keep customer evaluation sources, extracted content and
source-derived statistics outside public commits and review descriptions.

## Run the checks

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m pytest -m 'not ocr_integration' -q
.venv/bin/python -m pytest -m ocr_integration -q
```

For locally provisioned French Tesseract assets:

```bash
PDFSTRUCT_TEST_TESSDATA=/opt/ocr/tessdata .venv/bin/python -m pytest -q
```

A developer run can skip integration tests when optional engines are absent.
The release workflow has a separate smoke gate that requires both engines and
French language data; missing assets cannot count as a successful OCR release.
See [releasing](releasing.md) for building, installing and testing the exact wheel.

## Production extraction policy

Use `parse_document()` at an upload boundary to obtain input limits, warnings and
format-specific evidence. Set limits based on measured legitimate documents, and
reject unsupported types. The parser does not execute document hyperlinks or
provide storage. Run document processing as an isolated worker rather than in a
web request process. Apply a full-job deadline that terminates the worker, memory
and CPU limits, input-byte/page quotas, bounded queue size and retry limits.
Tesseract's inference timeout is not a full-document deadline; RapidOCR runs in
process and needs the worker's hard deadline.

Pre-provision OCR files and verify their hashes during image/build preparation.
Run with a read-only model directory, bounded temporary storage and enforced
network policy. `ocr_offline` validates assets; it is not a network firewall.
Record parser/model versions and a source revision hash with each indexed block.
Keep extracted text and documents out of diagnostic logs. Content hashes identify
bytes, not authorization: include tenant/document identity in access decisions.

Route documents with warnings, empty text or ambiguous tables for review before
indexing authoritative facts. Rejecting an ambiguous table while preserving its
native text is deliberate. An OCR score is an engine score, not an assurance that
amounts, dates or names are correct. For a new project's document types, add a
representative annotated evaluation set with source/field expectations, critical
amount/identifier checks and agreed acceptance thresholds.

## Integration boundary

The library supplies extraction for text PDFs, DOCX and TXT, with optional OCR and
citation evidence. It can run in a container or worker in a selected region. The
application must supply uploads/deletion, user roles, tenant isolation, source
permissions, storage encryption, audit events, retention of derivatives/backups,
model prompting, retrieval authorization, and regional infrastructure controls.

A local OCR implementation does not by itself prove Québec residency or legal
compliance. Residency must cover the deployed worker, files, temporary storage,
queues, indexes, model calls, logs, backups, support access and subprocessors.
Similarly, a French OCR fixture does not validate a complete French-Canadian user
experience. Those are separate application and deployment acceptance checks.
