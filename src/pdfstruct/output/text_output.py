"""Plain text output with canonical table ownership and source reading order."""

from __future__ import annotations

from pdfstruct.models.document import Document, Paragraph, Section, Table
from pdfstruct.structure.blocks import content_blocks


def to_text(doc: Document, include_structure: bool = True) -> str:
    """Convert a Document to text; raw mode preserves the page line stream."""
    if not include_structure:
        return doc.text
    parts: list[str] = [doc.title, "=" * len(doc.title), ""] if doc.title else []
    seen: set[int] = set()
    for section in doc.sections:
        skip = bool(
            doc.title
            and section.heading
            and section.heading.strip().casefold() == doc.title.strip().casefold()
        )
        parts.extend([_section_to_text(section, seen=seen, skip_heading=skip), ""])
    if not doc.sections:
        for page in doc.pages:
            for block in content_blocks(page.paragraphs, page.tables):
                parts.extend([_block_to_text(block, seen), ""])
    for table in doc.tables:
        if id(table) not in seen:
            parts.extend([_block_to_text(table, seen), ""])
    for page in doc.pages:
        for image in page.images:
            caption = image.caption or f"Image {image.image_index + 1}"
            parts.extend([f"[{caption} ({image.width_px}x{image.height_px})]", ""])
    return "\n".join(parts).strip() + "\n"


def _block_to_text(block: Paragraph | Table, seen: set[int]) -> str:
    if isinstance(block, Table):
        if id(block) in seen:
            return ""
        seen.add(id(block))
        return "\n".join(" | ".join(row) for row in block.to_list())
    return block.text


def _section_to_text(
    section: Section,
    depth: int = 0,
    *,
    seen: set[int] | None = None,
    skip_heading: bool = False,
) -> str:
    seen = set() if seen is None else seen
    parts: list[str] = []
    if section.heading and not skip_heading:
        level = section.heading_level.value if section.heading_level else 3
        parts.extend(
            [section.heading, {1: "=", 2: "-", 3: "~"}.get(level, "~") * len(section.heading)]
        )
    blocks = section.blocks or content_blocks(section.paragraphs, section.tables)
    parts.extend(_block_to_text(block, seen) for block in blocks)
    if section.content and not any(isinstance(block, Paragraph) for block in blocks):
        parts.insert(2 if section.heading and not skip_heading else 0, section.content)
    for child in section.subsections:
        parts.extend(["", _section_to_text(child, depth + 1, seen=seen)])
    return "\n".join(parts)
