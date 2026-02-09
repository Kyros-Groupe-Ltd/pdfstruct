# pdfstruct

**The PDF parser built for AI pipelines.** Structured sections, tables, images, and metadata — not just raw text.

[![PyPI](https://img.shields.io/pypi/v/pdfstructx.svg)](https://pypi.org/project/pdfstructx/)
[![Python](https://img.shields.io/badge/python-≥3.10-blue.svg)](https://www.python.org/)

## Overview

**pdfstruct** is a Python library that extracts structured content from PDF documents. Unlike basic text extraction tools, pdfstruct understands document layout — detecting headings, sections, tables, lists, headers/footers, and multi-column layouts using font analysis and geometric reasoning.

## Features

- **Font-aware heading detection**: Uses font size, weight, and frequency analysis to classify headings (H1–H6)
- **Table extraction**: Detects tables from grid lines and whitespace-aligned columns
- **Image extraction**: Extracts embedded images with metadata, DPI estimation, caption detection, and cross-page deduplication
- **Section hierarchy**: Builds a document tree from headings and content
- **Multi-column support**: Handles two-column and multi-column layouts
- **Header/footer removal**: Identifies and filters repeating page content
- **List detection**: Recognizes bulleted, numbered, lettered, and Roman numeral lists
- **Thumbnail generation**: Create thumbnails from extracted images
- **Multiple output formats**: JSON, Markdown, and plain text
- **Rich metadata**: Word count, language detection, reading time, font statistics, image stats

## Installation

```bash
pip install pdfstructx
```

Or install from source:

```bash
git clone https://github.com/Kyros-Groupe-Ltd/pdfstruct.git
cd pdfstruct
pip install -e .
```

## Quickstart

```python
import pdfstruct

# Parse a PDF
doc = pdfstruct.parse("contract.pdf")

# Access structured content
print(doc.title)
print(f"{doc.page_count} pages, {doc.metadata.word_count} words")

# Browse sections
for section in doc.sections:
    print(f"{section.heading} ({len(section.content)} chars)")
    for sub in section.subsections:
        print(f"  {sub.heading}")

# Get tables
for table in doc.tables:
    print(table.to_dicts())  # List of row dicts

# Extract images (opt-in)
doc = pdfstruct.parse("report.pdf", extract_images=True)
for page in doc.pages:
    for img in page.images:
        print(f"Page {img.page_number}: {img.format} {img.width_px}x{img.height_px} @ {img.dpi:.0f} DPI")
        if img.caption:
            print(f"  Caption: {img.caption}")
        if img.image_bytes:
            img.save(f"img_{img.page_number}_{img.image_index}.png")

# Generate thumbnails
thumbnail = pdfstruct.generate_thumbnail(img.image_bytes, max_size=(150, 150))

# Export to different formats
print(pdfstruct.to_markdown(doc))
print(pdfstruct.to_text(doc))
print(pdfstruct.to_json(doc))

# Full dict for programmatic use
data = pdfstruct.to_dict(doc)
```

## API Reference

### `pdfstruct.parse(source, **options) -> Document`

Parse a PDF file, bytes, or file-like object.

**Options:**
- `detect_tables` (bool, default True) — Enable table detection
- `detect_headers_footers` (bool, default True) — Remove repeating headers/footers
- `detect_lists` (bool, default True) — Detect list structures
- `detect_columns` (bool, default True) — Handle multi-column layouts
- `extract_images` (bool, default False) — Enable full image extraction (opt-in)
- `extract_image_data` (bool, default True) — Include raw image bytes (only when `extract_images=True`)

### Document

- `doc.title` — Detected document title
- `doc.pages` — List of Page objects
- `doc.sections` — Hierarchical section tree
- `doc.tables` — All detected tables
- `doc.metadata` — DocumentMetadata with statistics
- `doc.text` — Full document text (concatenated from pages)
- `doc.to_dict()` — JSON-serializable dictionary

### Section

- `section.heading` — Section heading text
- `section.heading_level` — HeadingLevel enum (H1–H6)
- `section.content` — Section body text
- `section.paragraphs` — List of Paragraph objects
- `section.subsections` — Nested subsections

### Table

- `table.rows` — List of TableRow objects
- `table.to_list()` — 2D list of cell text
- `table.to_dicts()` — List of dicts (header row as keys)
- `table.num_rows`, `table.num_cols` — Dimensions

### ImageInfo

- `img.bbox` — BBox position on page
- `img.width_px`, `img.height_px` — Pixel dimensions
- `img.format` — Image format (jpeg, png, jbig2, ccitt, jpeg2000, raw)
- `img.colorspace` — Color space (rgb, cmyk, grayscale, indexed)
- `img.dpi_x`, `img.dpi_y`, `img.dpi` — DPI (estimated from bbox vs pixel size)
- `img.image_bytes` — Raw image data (when `extract_image_data=True`)
- `img.file_size_bytes` — Size of extracted image data
- `img.content_hash` — SHA-256 hash for deduplication
- `img.caption` — Auto-detected caption text (Figure 1, Fig. 2, etc.)
- `img.page_number`, `img.image_index` — Location identifiers
- `img.is_duplicate`, `img.duplicate_of_index` — Cross-page deduplication
- `img.save(path)` — Save image to file

### `pdfstruct.generate_thumbnail(image_bytes, max_size=(150, 150), output_format="PNG")`

Generate a thumbnail from extracted image bytes. Returns thumbnail bytes or None.

### Metadata

- `metadata.word_count`, `metadata.char_count` — Text statistics
- `metadata.language` — Detected language code
- `metadata.page_count` — Number of pages
- `metadata.is_scanned` — Whether PDF appears to be scanned
- `metadata.has_tables`, `metadata.has_images` — Content flags
- `metadata.primary_font`, `metadata.primary_font_size` — Font info

## Comparison

| Feature | pdfstructx | PyMuPDF | pdfplumber | Unstructured |
|---|---|---|---|---|
| **Text extraction** | ✅ | ✅ | ✅ | ✅ |
| **Section hierarchy** (H1–H6 tree) | ✅ | ❌ | ❌ | Partial |
| **Font-aware heading detection** | ✅ | ❌ | ❌ | ❌ |
| **Table extraction** | ✅ | ❌ | ✅ | ✅ |
| **Image extraction + metadata** | ✅ | ✅ | ❌ | ✅ |
| **Caption detection** | ✅ | ❌ | ❌ | ❌ |
| **Image deduplication** | ✅ | ❌ | ❌ | ❌ |
| **DPI estimation** | ✅ | ❌ | ❌ | ❌ |
| **Thumbnail generation** | ✅ | ❌ | ❌ | ❌ |
| **Multi-column layout** | ✅ | ❌ | ❌ | ✅ |
| **Header/footer removal** | ✅ | ❌ | ❌ | ✅ |
| **List detection** | ✅ | ❌ | ❌ | ✅ |
| **Language detection** | ✅ | ❌ | ❌ | ✅ |
| **Reading time / word count** | ✅ | ❌ | ❌ | ❌ |
| **Markdown export** | ✅ | ❌ | ❌ | ✅ |
| **JSON structured output** | ✅ | ❌ | ❌ | ✅ |
| **Pure Python (no Java/Docker)** | ✅ | ✅ | ✅ | ❌ |
| **License** | Apache 2.0 | AGPL | MIT | Apache 2.0 |

## Real-World Benchmarks

Tested on actual documents — not toy examples:

| Document | Pages | Words | Sections | Tables | Images (unique) | Time |
|---|---|---|---|---|---|---|
| 3-page CV | 3 | 863 | 1 | 3 | 0 | 164 ms |
| Bank statement (French) | 5 | 1,880 | 23 | 2 | 2 (1) | 379 ms |
| 130-page gov't RFP | 130 | 41,420 | 62 | 73 | 269 (8 unique) | 10.2 s |
| 224-page procurement doc | 224 | 53,979 | 107 | 118 | 408 (58 unique) | 23.6 s |

**Head-to-head on the 130-page RFP:**

| Library | Time | Words | Tables | Sections | Images | Dedup |
|---|---|---|---|---|---|---|
| **PyMuPDF** | 277 ms | 43,455 | ❌ N/A | ❌ N/A | 270 | ❌ No |
| **pdfplumber** | 16.5 s | 43,420 | 142 | ❌ N/A | ❌ N/A | ❌ No |
| **pdfstructx** | 13.1 s | 41,420 | 73 | 62 | 269 (8 unique) | ✅ 261 dupes filtered |

PyMuPDF is faster (C-based) but gives you flat text — no sections, no structure, no deduplication. pdfplumber finds tables but no hierarchy. pdfstructx gives you the complete picture.

## Architecture

```
pdfstruct/
├── parser.py           # Main PDFParser class and parse() entry point
├── models/
│   ├── document.py     # Core models: Document, Page, Section, TextLine, Table, ImageInfo, etc.
│   └── metadata.py     # DocumentMetadata with computed statistics
├── extractors/
│   ├── text.py         # PDF text extraction via pdfminer.six
│   └── images.py       # Image extraction, caption detection, dedup, thumbnails
├── layout/
│   └── analyzer.py     # Paragraph grouping, reading order, margins
├── structure/
│   ├── headings.py     # Font-aware heading detection
│   ├── headers_footers.py  # Repeating content detection
│   ├── lists.py        # List structure detection
│   └── sections.py     # Section hierarchy builder
├── tables/
│   └── detector.py     # Grid and whitespace table detection
├── output/
│   ├── json_output.py  # JSON/dict export
│   ├── markdown.py     # Markdown export
│   └── text_output.py  # Plain text export
└── utils/
    ├── fonts.py        # Font analysis and heading classification
    ├── geometry.py     # Bounding box utilities, column detection
    └── language.py     # Language detection heuristics
```

## Requirements

- Python >= 3.10
- pdfminer.six >= 20231228
- Pillow >= 10.0.0

## License

Apache License 2.0. See [LICENSE](LICENSE) for details.
