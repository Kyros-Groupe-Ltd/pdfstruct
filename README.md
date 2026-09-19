<p align="center">
  <h1 align="center">pdfstruct</h1>
  <p align="center"><strong>The PDF parser built for AI pipelines.</strong><br>Structured sections, tables, images, and metadata — not just raw text.</p>
</p>

<p align="center">
  <a href="https://pypi.org/project/pdfstructx/"><img src="https://img.shields.io/pypi/v/pdfstructx.svg" alt="PyPI"></a>
  <a href="https://pypi.org/project/pdfstructx/"><img src="https://img.shields.io/pypi/dm/pdfstructx.svg" alt="Downloads"></a>
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/python-≥3.10-blue.svg" alt="Python"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache%202.0-green.svg" alt="License"></a>
</p>

---

## Why pdfstruct?

Most PDF libraries give you a wall of text. pdfstruct gives you a **document**.

```python
import pdfstruct

doc = pdfstruct.parse("contract.pdf")

# You get structure, not strings
for section in doc.sections:
    print(f"[{section.heading_level.name}] {section.heading}")
    for sub in section.subsections:
        print(f"  → {sub.heading}")

# Tables come as data, not text blobs
for table in doc.tables:
    print(table.to_dicts())

# Images with captions, DPI, deduplication
doc = pdfstruct.parse("report.pdf", extract_images=True)
for page in doc.pages:
    for img in page.images:
        print(f"{img.format} {img.width_px}×{img.height_px} @ {img.dpi:.0f} DPI")
        if img.caption:
            print(f"  Caption: {img.caption}")
```

---

## Why not PyMuPDF?

| | pdfstructx | PyMuPDF |
|---|---|---|
| **License** | Apache 2.0 — use it anywhere | AGPL — must open-source your code or buy a license |
| **Structured output** | Sections, headings, tables, lists as objects | Flat text dump |
| **Section hierarchy** | Full H1–H6 tree from font analysis | None |
| **Table extraction** | Grid + whitespace detection, `.to_dicts()` | None (use another library) |
| **Caption detection** | Auto-detects "Figure 1", "Table 2", etc. | None |
| **Image deduplication** | SHA-256 hash, cross-page dedup | None — same logo 50 times |
| **DPI estimation** | Computed from bbox vs pixel size | None |
| **Pure Python** | ✅ pip install and go | C extension + AGPL concerns |

PyMuPDF is fast. But it gives you raw text and an AGPL license. If you're building a product, that's a problem.

---

## Structured Output vs Raw Text

Here's what you get from a 130-page government RFP:

**PyMuPDF gives you this:**
```
CAHIER DES CHARGES TECHNIQUES GENERALES\n\n1. OBJET DU MARCHE\n\nLe présent
marché a pour objet...\n\n1.1 Contexte\n\nLa Direction des Systèmes
d'Information...\n\nTableau 1 : Planning prévisionnel\nPhase | Début | Fin |
Livrable\nConception | 01/03 | 30/04 | Dossier de conception\n...
```
A single string. You parse it yourself. Good luck.

**pdfstruct gives you this:**
```python
doc.sections[0].heading           # "CAHIER DES CHARGES TECHNIQUES GENERALES"
doc.sections[0].heading_level     # HeadingLevel.H1
doc.sections[0].subsections[0]    # Section("OBJET DU MARCHE", level=H2)

doc.tables[0].to_dicts()
# [{"Phase": "Conception", "Début": "01/03", "Fin": "30/04", "Livrable": "Dossier de conception"}, ...]

doc.metadata.word_count           # 41420
doc.metadata.language             # "fr"
doc.metadata.has_tables           # True
len(doc.tables)                   # 73
len(doc.sections)                 # 62
```

Same PDF. One gives you text. The other gives you data.

---

## Install

```bash
pip install pdfstructx
```

The base parser requires no OCR engine, Java, or Docker. Scanned PDFs use an
optional local OCR stack (with native runtimes); see below.

```bash
# Or from source
git clone https://github.com/Kyros-Groupe-Ltd/pdfstruct.git
cd pdfstruct
pip install -e .
```

## Scanned PDFs (local OCR)

```bash
pip install "pdfstructx[ocr]"
```

```python
import pdfstruct

# Automatically OCR likely scanned pages; keep native text on digital pages.
doc = pdfstruct.parse("scanned-contract.pdf", ocr=True)
print(doc.text)
print(doc.metadata.ocr_page_count)

for page in doc.pages:
    print(page.number, page.ocr_backend, page.ocr_confidence)

# Replace an existing/broken OCR layer without duplicating native text.
doc = pdfstruct.parse("bad-text-layer.pdf", ocr="always")
```

The default backend runs **PaddleOCR PP-OCRv6 models through RapidOCR/ONNX Runtime**
locally, with multilingual recognition including English, French, and Chinese.
RapidOCR code and its converted PaddleOCR models are Apache-2.0 licensed;
ONNX Runtime is MIT licensed. No cloud account or document upload is needed.

For a lighter alternative, install `pdfstructx[tesseract]` and the system
Tesseract executable plus language packs, then select it explicitly:

```python
doc = pdfstruct.parse(
    "scanned-contract.pdf", ocr=True,
    ocr_backend="tesseract", ocr_language="eng+fra", ocr_timeout=60,
)
```

OCR text enters the existing paragraph, section, and export pipeline. It does
not guarantee exact original fonts, handwriting recognition, or reconstruction
of complex scanned tables. Confidence scores are engine estimates, not accuracy
guarantees. See [OCR setup, licenses, limits, and deployment](docs/ocr.md).

## Quick Start

```python
import pdfstruct

# Parse any PDF
doc = pdfstruct.parse("document.pdf")

# Metadata
print(doc.title)
print(f"{doc.page_count} pages, {doc.metadata.word_count} words")
print(f"Language: {doc.metadata.language}")

# Sections — full hierarchy
for section in doc.sections:
    print(f"{section.heading} ({len(section.content)} chars)")
    for sub in section.subsections:
        print(f"  {sub.heading}")

# Tables — as structured data
for table in doc.tables:
    print(table.to_dicts())  # [{"col1": "val1", ...}, ...]

# Export
print(pdfstruct.to_markdown(doc))   # Markdown
print(pdfstruct.to_json(doc))       # JSON
print(pdfstruct.to_text(doc))       # Plain text
data = pdfstruct.to_dict(doc)       # Dict for programmatic use
```

### Image Extraction

```python
doc = pdfstruct.parse("report.pdf", extract_images=True)

for page in doc.pages:
    for img in page.images:
        print(f"Page {img.page_number}: {img.format} {img.width_px}×{img.height_px}")
        print(f"  DPI: {img.dpi:.0f}, Size: {img.file_size_bytes} bytes")

        if img.caption:
            print(f"  Caption: {img.caption}")

        if img.is_duplicate:
            print(f"  ⚠ Duplicate of image {img.duplicate_of_index}")
        else:
            img.save(f"img_p{img.page_number}_{img.image_index}.png")

# Thumbnails
thumb = pdfstruct.generate_thumbnail(img.image_bytes, max_size=(150, 150))
```

---

## Feature Comparison

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

---

## Real-World Benchmarks

Tested on production documents — not toy examples.

| Document | Pages | Words | Sections | Tables | Images (unique) | Time |
|---|---|---|---|---|---|---|
| 3-page CV | 3 | 863 | 1 | 3 | 0 | 164 ms |
| Bank statement (French) | 5 | 1,880 | 23 | 2 | 2 (1) | 379 ms |
| 130-page gov't RFP | 130 | 41,420 | 62 | 73 | 269 (8 unique) | 10.2 s |
| 224-page procurement doc | 224 | 53,979 | 107 | 118 | 408 (58 unique) | 23.6 s |

### Head-to-Head: 130-Page Government RFP

| Library | Time | Words | Tables | Sections | Images | Dedup |
|---|---|---|---|---|---|---|
| **PyMuPDF** | 277 ms | 43,455 | ❌ N/A | ❌ N/A | 270 | ❌ No |
| **pdfplumber** | 16.5 s | 43,420 | 142 | ❌ N/A | ❌ N/A | ❌ No |
| **pdfstructx** | 13.1 s | 41,420 | 73 | 62 | 269 (8 unique) | ✅ 261 dupes filtered |

PyMuPDF is faster (it's a C library). But it gives you flat text — no sections, no tables, no deduplication. pdfplumber finds tables but no hierarchy. **pdfstructx gives you the complete picture.**

---

## API Reference

### `pdfstruct.parse(source, **options) → Document`

Parse a PDF file path, bytes, or file-like object.

| Option | Type | Default | Description |
|---|---|---|---|
| `detect_tables` | bool | `True` | Enable table detection |
| `detect_headers_footers` | bool | `True` | Remove repeating headers/footers |
| `detect_lists` | bool | `True` | Detect list structures |
| `detect_columns` | bool | `True` | Handle multi-column layouts |
| `extract_images` | bool | `False` | Extract embedded images (opt-in) |
| `extract_image_data` | bool | `True` | Include raw bytes (when images enabled) |
| `ocr` | bool or str | `False` | `True`/`"auto"`: OCR likely scans; `"always"`: OCR every page |
| `ocr_backend` | str | `"rapidocr"` | Local `"rapidocr"` (PP-OCRv6) or `"tesseract"` |
| `ocr_language` | str | `"eng"` | Tesseract language packs, e.g. `"eng+fra"`; RapidOCR is multilingual automatically |
| `ocr_dpi` | int | `300` | Render resolution (72-600); max 40 million pixels/page |
| `ocr_min_text_chars` | int | `50` | Auto OCR needs fewer native non-whitespace characters plus >=50% raster coverage |
| `ocr_timeout` | float | `60` | Tesseract timeout in seconds/page; not a RapidOCR deadline |

### Document

| Property | Description |
|---|---|
| `doc.title` | Detected document title |
| `doc.pages` | List of Page objects |
| `doc.sections` | Hierarchical section tree |
| `doc.tables` | All detected tables |
| `doc.metadata` | DocumentMetadata with statistics |
| `doc.text` | Full document text |

### Section

| Property | Description |
|---|---|
| `section.heading` | Section heading text |
| `section.heading_level` | HeadingLevel enum (H1–H6) |
| `section.content` | Section body text |
| `section.paragraphs` | List of Paragraph objects |
| `section.subsections` | Nested subsections |

### Table

| Method / Property | Description |
|---|---|
| `table.rows` | List of TableRow objects |
| `table.to_list()` | 2D list of cell text |
| `table.to_dicts()` | List of dicts (header row as keys) |
| `table.num_rows` / `table.num_cols` | Dimensions |

### ImageInfo

| Property | Description |
|---|---|
| `img.width_px` / `img.height_px` | Pixel dimensions |
| `img.format` | jpeg, png, jbig2, ccitt, jpeg2000, raw |
| `img.colorspace` | rgb, cmyk, grayscale, indexed |
| `img.dpi` | Estimated DPI |
| `img.image_bytes` | Raw image data |
| `img.caption` | Auto-detected caption text |
| `img.content_hash` | SHA-256 for deduplication |
| `img.is_duplicate` | Whether image is a duplicate |
| `img.save(path)` | Save image to file |

### Output Functions

```python
pdfstruct.to_json(doc)       # JSON string
pdfstruct.to_dict(doc)       # Python dict
pdfstruct.to_markdown(doc)   # Markdown string
pdfstruct.to_text(doc)       # Plain text string

pdfstruct.generate_thumbnail(image_bytes, max_size=(150, 150))  # Thumbnail bytes
```

---

## Architecture

```
pdfstruct/
├── parser.py              # Main entry point: parse()
├── models/
│   ├── document.py        # Document, Page, Section, Table, ImageInfo, etc.
│   └── metadata.py        # Word count, language, reading time, font stats
├── extractors/
│   ├── text.py            # Text extraction via pdfminer.six
│   └── images.py          # Image extraction, captions, dedup, thumbnails
├── layout/
│   └── analyzer.py        # Paragraph grouping, reading order, columns
├── structure/
│   ├── headings.py        # Font-aware heading detection (H1–H6)
│   ├── headers_footers.py # Repeating content detection
│   ├── lists.py           # Bullet, numbered, lettered, Roman lists
│   └── sections.py        # Section hierarchy builder
├── tables/
│   └── detector.py        # Grid-line and whitespace table detection
├── output/
│   ├── json_output.py     # JSON / dict export
│   ├── markdown.py        # Markdown export
│   └── text_output.py     # Plain text export
└── utils/
    ├── fonts.py           # Font analysis and classification
    ├── geometry.py        # Bounding boxes, column detection
    └── language.py        # Language detection heuristics
```

## Requirements

- Python ≥ 3.10
- [pdfminer.six](https://github.com/pdfminer/pdfminer.six) ≥ 20231228
- [Pillow](https://python-pillow.org/) ≥ 10.0.0
- Optional OCR: [RapidOCR](https://github.com/RapidAI/RapidOCR), ONNX Runtime and
  pypdfium2, or pytesseract with a separately installed Tesseract executable.

## Contributing

Issues and pull requests welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## License

Apache License 2.0. See [LICENSE](LICENSE) for the full text.

---

<p align="center">
  Built by <a href="https://github.com/Kyros-Groupe-Ltd"><strong>Kyros Groupe</strong></a><br>
  <a href="https://github.com/Kyros-Groupe-Ltd">Follow us on GitHub</a> · <a href="https://github.com/Kyros-Groupe-Ltd/pdfstruct/stargazers">⭐ Star pdfstruct</a>
</p>
