# Changelog

## 0.3.0 — release preparation

### Added

- Optional local RapidOCR/ONNX and Tesseract OCR with native/scan-region routing,
  French language configuration, explicit local model directories and asset preflight.
- Engine-measured OCR spans, polygons and confidence, with estimated character
  positions and fonts identified as estimates.
- `parse_document()` for PDF, DOCX and TXT, with source hashes, stable block IDs,
  real format-specific anchors, extraction warnings and configurable input limits.
- DOCX ZIP/XML preflight, merged-cell metadata and conservative nested-table handling.
- Configurable per-page table complexity limits with a typed exhaustion exception
  and serialized page warnings when the parser retains raw text instead.
- Regression fixtures covering French, mixed documents, geometry, hostile inputs,
  table regions, spans, source citations, list preservation and package contents.

### Fixed

- Independent ruled tables no longer become a single page-wide grid.
- Whole text lines no longer repeat across multiple table cells; native glyphs
  are assigned once. Merged cells remain anchor-only and preserve their spans.
- Duplicate/empty table headers no longer overwrite values in `to_dicts()`.
- Section, paragraph, line and table exports retain actual source pages/boxes.
- Each table belongs to one section and appears once in structured exports.
- Table text is excluded from document heading detection. A heading no longer
  absorbs the following same-size body paragraph.
- French uppercase and dotted numbered heading patterns are recognized; obvious
  table-of-contents leader lines are excluded from heading detection.
- Text crossing a table boundary is retained with a review warning, instead of
  disappearing from structured output. Ambiguous list continuations are retained.
- Structured exports retain manually supplied `Section.content` alongside tables
  and preserve original ordered-list markers.
- OCR preserves unmatched native content and reports empty or low-quality outcomes
  instead of silently erasing text; renderer/backend failures remain explicit.
- PDF metadata warnings avoid including the underlying exception's source content.

### Release workflow

- Build and validate a single wheel/source-distribution pair; test the same wheel
  on Python 3.10–3.13 and in a required real French OCR job.
- Require native PDF, DOCX and French TXT smoke checks against the installed wheel.
- Publish the tested artifacts only on a matching version tag, using the existing
  GitHub/PyPI Trusted Publisher identity. The publish job alone receives OIDC rights.

### Compatibility and limits

- `parse()` remains PDF-only, with OCR disabled by default. Optional DOCX dependencies
  are lazy; the base install remains usable without OCR/DOCX extras.
- JSON adds evidence and warning fields. Consumers with strict schemas must update.
- `Table.to_list()` is rectangular and does not repeat merged-cell content. Header
  inference is more conservative; `to_dicts()` may use disambiguated keys.
- Section body text excludes canonical table blocks; use `section.tables`,
  `section.blocks` or `parse_document().blocks` for complete structured content.
- `ocr='always'` prefers OCR at overlapping locations but retains unmatched native
  text. OCR confidence is not calibrated accuracy and OCR typography is estimated.
- Reading order, headings and table interpretation remain heuristic. Raster-table
  structure recognition, handwriting, general deskew/dewarping and application
  access control/retention are outside this release.
