"""Markdown output with ordered blocks and a single copy of each table."""

from __future__ import annotations

from pdfstruct.models.document import Document, ImageInfo, ListItem, Paragraph, Section, Table
from pdfstruct.structure.blocks import content_blocks


def to_markdown(doc: Document) -> str:
    """Convert a Document to Markdown; preserve merged cells in JSON for fidelity."""
    parts: list[str] = [f"# {doc.title}", ""] if doc.title else []
    seen: set[int] = set()
    if doc.sections:
        for section in doc.sections:
            skip = bool(
                doc.title
                and section.heading
                and section.heading.strip().casefold() == doc.title.strip().casefold()
            )
            parts.append(_section_to_md(section, seen=seen, skip_heading=skip))
    else:
        for page in doc.pages:
            for block in content_blocks(page.paragraphs, page.tables):
                parts.append(_block_to_md(block, seen))
                parts.append("")
    for table in doc.tables:
        if id(table) not in seen:
            parts.extend([_block_to_md(table, seen), ""])
    for page in doc.pages:
        for image in page.images:
            parts.extend([_image_to_md(image), ""])
    return "\n".join(parts).strip() + "\n"


def _block_to_md(block: Paragraph | Table, seen: set[int]) -> str:
    if isinstance(block, Table):
        if id(block) in seen:
            return ""
        seen.add(id(block))
        return _table_to_md(block)
    return _paragraph_to_md(block)


def _section_to_md(
    section: Section,
    base_level: int = 1,
    *,
    seen: set[int] | None = None,
    skip_heading: bool = False,
) -> str:
    seen = set() if seen is None else seen
    parts: list[str] = []
    level = section.heading_level.value if section.heading_level else base_level
    if section.heading and not skip_heading:
        parts.extend([f"{'#' * min(level, 6)} {section.heading}", ""])
    blocks = section.blocks or content_blocks(section.paragraphs, section.tables)
    if section.content and not any(isinstance(block, Paragraph) for block in blocks):
        # Tables must not suppress a manually constructed Section(content=...).
        parts.extend([section.content, ""])
    for block in blocks:
        parts.extend([_block_to_md(block, seen), ""])
    for subsection in section.subsections:
        parts.append(_section_to_md(subsection, base_level=level + 1, seen=seen))
    return "\n".join(parts)


def _paragraph_to_md(para: Paragraph) -> str:
    if para.is_heading and para.heading_level:
        return f"{'#' * min(para.heading_level.value, 6)} {para.text}"
    if para.list_items:
        return _list_to_md(para.list_items, para.list_type)
    text = para.text
    if para.font:
        if para.font.is_bold:
            text = f"**{text}**"
        elif para.font.is_italic:
            text = f"*{text}*"
    return text


def _list_to_md(items: list[ListItem], list_type=None) -> str:
    parts: list[str] = []
    for index, item in enumerate(items):
        indent = "  " * item.level
        if list_type and list_type.value in ("numbered", "lettered", "roman"):
            parts.append(f"{indent}{item.marker} {item.text}")
        else:
            parts.append(f"{indent}- {item.text}")
    return "\n".join(parts)


def _image_to_md(image: ImageInfo) -> str:
    alt_text = image.caption or f"Image {image.image_index + 1}"
    details = f"{image.width_px}x{image.height_px}"
    if image.dpi:
        details += f", {round(image.dpi)}dpi"
    if image.format not in ("unknown", "raw"):
        details += f", {image.format}"
    return f"![{alt_text}](<!-- image: p{image.page_number}_img{image.image_index} {details} -->)"


def _table_to_md(table: Table) -> str:
    """Use an anchor-only grid; Markdown cannot represent row/column spans."""
    if not table.rows or not table.num_cols:
        return ""
    grid = [
        [text.replace("|", "\\|").replace("\r\n", "\n").replace("\n", "<br>") for text in row]
        for row in table.to_list()
    ]
    if table.header_row is None:
        grid.insert(0, [""] * table.num_cols)
    widths = [max(3, max(len(row[col]) for row in grid)) for col in range(table.num_cols)]
    parts: list[str] = []
    for index, row in enumerate(grid):
        parts.append(
            "| " + " | ".join(text.ljust(width) for text, width in zip(row, widths)) + " |"
        )
        if index == 0:
            parts.append("| " + " | ".join("-" * width for width in widths) + " |")
    return "\n".join(parts)
