# Local document ingestion: PDF, DOCX and TXT

`parse_document()` provides one block-oriented ingestion API for text PDFs,
Word `.docx` documents and plain `.txt` files. Existing `pdfstruct.parse()` remains
the richer PDF-specific API. Both run in the local Python process. The adapter
does not send document content to a service or follow document hyperlinks.

Install the Word dependency only when DOCX is needed:

```bash
python -m pip install 'pdfstructx[documents]==0.3.0'
```

The `documents` extra uses python-docx 1.2.x; its body iterator supplies paragraphs
and tables in document order ([python-docx document API](https://python-docx.readthedocs.io/en/latest/api/document.html)).

## A single ingestion interface

```python
from pdfstruct import parse_document

document = parse_document("programme.docx")
print(document.format, document.sha256)

for block in document.blocks:
    print(block.kind, block.text, block.locator)

record = document.to_dict()  # JSON-compatible dictionaries, lists and scalars
warnings = document.warnings
```

For upload bytes or a binary stream, supply the original filename explicitly:

```python
document = parse_document(upload_bytes, filename="programme.pdf")
```

Only the extension selects the adapter, case-insensitively. The PDF header and
DOCX package structure are checked before parsing. `.doc`, `.docm`, `.rtf`, image
files and arbitrary archives are not supported. A string input denotes a local
path, never a URL. Seekable streams are read from the start and their caller-owned
position is restored; the adapter does not close them. A nonseekable stream is
read from its current position.

Use the PDF options explicitly when scans also need OCR:

```python
document = parse_document(
    "scan.pdf",
    pdf_options={
        "ocr": "auto",
        "ocr_backend": "tesseract",
        "ocr_language": "fra+eng",
    },
)
```

The Tesseract executable and French/English trained-data files must already be
available. See [OCR configuration](ocr.md) for both local OCR backends, resource
limits, offline model provisioning, and OCR quality/provenance. OCR remains off
when no PDF option enables it. `encoding` applies only to TXT; `pdf_options`
applies only to PDF. Passing either option to the wrong adapter raises an error.

## What the output means

An `IngestedDocument` contains the source filename, format, complete SHA-256 of
the input bytes, canonical blocks, extraction warnings, and metadata. Its `text`
property joins nonempty block text. Each `DocumentBlock` contains:

| Field | Meaning |
| --- | --- |
| `id` | Content-digest prefix plus a source anchor; stable for the same bytes and extraction path. |
| `kind` | `heading`, `paragraph`, or `table`. |
| `text` | Extracted text; a table uses newlines between rows and tabs between columns. |
| `locator` | Format-specific evidence about where the block came from. |
| `heading_level` | A detected PDF heading level or a declared DOCX heading/outline level, when available. |
| `rows` | A rectangular table grid, or `None`. |
| `metadata` | Format-specific structure, provenance, style evidence or table cells. |

| Format | Source anchor | Extraction behavior |
| --- | --- | --- |
| PDF | Actual page, canonical block index and measured bounding box when available; bottom-left PDF layout points. | Reuses the PDF parser's reading order. Associated table paragraphs are replaced by one table block, preventing duplicate indexed text. Unanchored tables use a physical-layout heuristic and produce a warning. |
| DOCX | Actual body-child position plus paragraph/table index; table cells have row/column anchors. | Preserves ordinary body paragraph/table order. Heading styles and direct outline levels supply hierarchy. Explicit bold runs are recorded as style evidence rather than automatically promoted to headings. |
| TXT | One-based start/end line numbers. | Groups consecutive nonblank lines into blocks; blank lines separate them. CRLF, CR and LF are normalized as line endings. |

DOCX and TXT do not have fixed rendered pages. Their locators intentionally contain
no page numbers or PDF coordinates. Word pagination changes with fonts, margins,
printer settings and rendering software; a future rendered PDF should be treated
as a separate artifact with its own source mapping.

Merged table text appears once at its anchor; covered grid positions are empty
strings. DOCX cell metadata records one-based `row`, `col`, `row_span` and
`col_span`. PDF table metadata preserves the existing PDF model's zero-based cell
indices and measured cell geometry. An empty string alone cannot distinguish a
covered cell from an originally blank cell; use the cell metadata for that purpose.
Nested DOCX tables are flattened inside their parent cell and reported in warnings.

IDs identify extracted source material, not access permissions. Store the full
SHA-256, parser version and options with a source revision. Keep a separate
application-owned document ID, tenant ID and ingestion version; changing OCR or
layout options can change the block sequence even for identical bytes.

## French text and encodings

French Unicode text and accents are preserved. TXT defaults to strict UTF-8,
accepts an optional UTF-8 BOM, and detects UTF-16 or UTF-32 only when a BOM is
present. It does not silently guess a legacy encoding or replace undecodable
characters. For a file with a known Windows encoding:

```python
document = parse_document("programme.txt", encoding="cp1252")
```

This extraction support does not establish Canadian-French terminology quality
for a chatbot or language model. Evaluate those separately with representative
Québec documents and actual user questions. OCR smoke tests likewise validate an
execution path, not a guaranteed recognition rate for every scan.

## Limits, malformed documents and untrusted uploads

The default limits are explicit and can be lowered by an application:

```python
document = parse_document(
    upload_bytes,
    filename="programme.docx",
    max_bytes=25_000_000,
    max_docx_uncompressed_bytes=100_000_000,
    max_docx_entries=2_000,
    max_blocks=10_000,
    max_table_cells=100_000,
)
```

DOCX validation checks ZIP entry count and declared/actually read expansion,
rejects duplicate or unsafe member names, encrypted members, obvious VBA project
members, and compression ratios above 1,000:1. It reads every ZIP member under the
expansion budget, including XML parts without an `.xml` extension. DTD/entity
declarations are rejected before python-docx parses the package, including ordinary
UTF-8/16/32 representations. No archive member is extracted to a filesystem path.
Nested DOCX tables are limited to 12 nested levels. Table grid limits apply to each
table, including a merged cell's expanded grid footprint.

Unsupported, malformed, oversized or undecodable adapter input raises
`DocumentInputError`. A missing Word dependency raises `DocumentDependencyError`
with the install command. PDF configuration, password and OCR backend errors
retain the PDF parser's exceptions. These errors should produce a failed ingestion
status, not a successful empty index. An empty or image-only document can instead
produce no nonempty text and an explicit warning; review warnings before indexing.

These are practical bounds, not a hostile-file sandbox or an antivirus. PDF
parsing and DOCX XML parsing occur before output-block limits can take effect.
Run untrusted uploads in an isolated worker with process memory, CPU and wall-time
limits, restricted filesystem access, and controlled network access. Add malware
inspection, queue backpressure and application upload policies at that boundary.

## Known extraction limits and application responsibilities

DOCX headers, footers, footnotes, endnotes, embedded files, tracked revisions,
content controls, text boxes, drawings and imported content are not fully
represented by the body adapter. Detected features produce warnings. It does not
resolve Word fields, reproduce layout, OCR embedded Word images, or extract
comments as part of the body. If these features contain required evidence, convert
the source through an approved local renderer and evaluate that separate path.

PDF reading order, inferred headings, tables and OCR remain heuristic. Preserve
source links and confidence/quality metadata and require review where errors could
change a business decision. Do not treat source instructions embedded in a document
as privileged instructions to an AI model.

This package covers document extraction only. Authentication, RBAC, upload/delete
workflows, tenant isolation, retention, audit logging, Snowflake integration,
retrieval, document-level authorization and answer citations belong in the host
application. Deleting a document must also remove or invalidate its derived
blocks, embeddings and caches there; this stateless parser cannot perform that
operation. Running locally enables a chosen deployment boundary, but does not by
itself certify Québec residency, Law 25 compliance, disaster recovery locations or
the behavior of an application's other dependencies.
