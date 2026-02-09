"""Markdown output formatter.

Converts a parsed Document into clean Markdown with proper heading hierarchy,
lists, and tables.
"""

from __future__ import annotations

from pdfstruct.models.document import Document, Section, Paragraph, Table, ListItem, ImageInfo


def to_markdown(doc: Document) -> str:
    """Convert a Document to Markdown string."""
    parts: list[str] = []

    # Title
    if doc.title:
        parts.append(f"# {doc.title}")
        parts.append("")

    # Sections
    if doc.sections:
        for section in doc.sections:
            # Skip section if its heading duplicates the document title
            if (doc.title and section.heading
                    and section.heading.strip().lower() == doc.title.strip().lower()):
                # Output subsections and content directly, skip the duplicate heading
                for para in section.paragraphs:
                    parts.append(_paragraph_to_md(para))
                    parts.append("")
                for sub in section.subsections:
                    parts.append(_section_to_md(sub, base_level=2))
            else:
                parts.append(_section_to_md(section, base_level=1))
    else:
        # No sections — just output paragraphs from pages
        for page in doc.pages:
            for para in page.paragraphs:
                parts.append(_paragraph_to_md(para))
                parts.append("")

    # Standalone tables (not within sections)
    for table in doc.tables:
        parts.append(_table_to_md(table))
        parts.append("")

    # Image placeholders per page
    for page in doc.pages:
        if page.images:
            for img in page.images:
                parts.append(_image_to_md(img))
                parts.append("")

    return "\n".join(parts).strip() + "\n"


def _section_to_md(section: Section, base_level: int = 1) -> str:
    """Convert a Section to Markdown."""
    parts: list[str] = []
    level = base_level

    if section.heading and section.heading_level:
        level = section.heading_level.value
        prefix = "#" * min(level, 6)
        parts.append(f"{prefix} {section.heading}")
        parts.append("")

    # Content paragraphs
    for para in section.paragraphs:
        parts.append(_paragraph_to_md(para))
        parts.append("")

    # Tables within this section
    for table in section.tables:
        parts.append(_table_to_md(table))
        parts.append("")

    # Subsections
    for subsection in section.subsections:
        parts.append(_section_to_md(subsection, base_level=level + 1))

    return "\n".join(parts)


def _paragraph_to_md(para: Paragraph) -> str:
    """Convert a Paragraph to Markdown."""
    if para.is_heading and para.heading_level:
        prefix = "#" * min(para.heading_level.value, 6)
        return f"{prefix} {para.text}"

    if para.list_items:
        return _list_to_md(para.list_items, para.list_type)

    # Apply inline formatting
    text = para.text
    if para.font:
        if para.font.is_bold:
            text = f"**{text}**"
        elif para.font.is_italic:
            text = f"*{text}*"

    return text


def _list_to_md(items: list[ListItem], list_type=None) -> str:
    """Convert list items to Markdown list."""
    parts: list[str] = []
    for i, item in enumerate(items):
        indent = "  " * item.level
        if list_type and list_type.value in ("numbered", "lettered", "roman"):
            parts.append(f"{indent}{i + 1}. {item.text}")
        else:
            parts.append(f"{indent}- {item.text}")
    return "\n".join(parts)


def _image_to_md(image: ImageInfo) -> str:
    """Convert an ImageInfo to a Markdown image placeholder."""
    alt_text = image.caption or f"Image {image.image_index + 1}"
    details = f"{image.width_px}x{image.height_px}"
    if image.dpi:
        details += f", {round(image.dpi)}dpi"
    if image.format not in ("unknown", "raw"):
        details += f", {image.format}"
    return f"![{alt_text}](<!-- image: p{image.page_number}_img{image.image_index} {details} -->)"


def _table_to_md(table: Table) -> str:
    """Convert a Table to Markdown table format."""
    if not table.rows:
        return ""

    # Determine column widths
    num_cols = table.num_cols
    if num_cols == 0:
        return ""

    col_widths = [3] * num_cols  # minimum width
    for row in table.rows:
        for i, cell in enumerate(row.cells):
            if i < num_cols:
                col_widths[i] = max(col_widths[i], len(cell.text))

    parts: list[str] = []

    for row_idx, row in enumerate(table.rows):
        cells_text = []
        for i in range(num_cols):
            if i < len(row.cells):
                text = row.cells[i].text.replace("|", "\\|")
                cells_text.append(text.ljust(col_widths[i]))
            else:
                cells_text.append(" " * col_widths[i])

        parts.append("| " + " | ".join(cells_text) + " |")

        # Add separator after header row
        if row_idx == 0:
            separators = ["-" * w for w in col_widths]
            parts.append("| " + " | ".join(separators) + " |")

    return "\n".join(parts)
