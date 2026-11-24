"""Build a section hierarchy with real source pages and one table owner."""

from __future__ import annotations

from pdfstruct.models.document import Paragraph, Section, Table
from pdfstruct.structure.blocks import content_blocks


def build_sections(paragraphs: list[Paragraph], tables: list[Table]) -> list[Section]:
    """Use heading paragraphs as boundaries while preserving source anchors."""
    flat: list[Section] = []
    current: Section | None = None
    for block in content_blocks(paragraphs, tables):
        if isinstance(block, Paragraph) and block.is_heading and not block.table_id:
            current = Section(
                heading=block.text,
                heading_level=block.heading_level,
                heading_paragraph=block,
                page_number=block.page_number,
                page_end=block.page_number,
            )
            flat.append(current)
            continue
        if current is None:
            current = Section(page_number=block.page_number, page_end=block.page_number)
            flat.append(current)
        current.blocks.append(block)
        if isinstance(block, Table):
            current.tables.append(block)
        else:
            current.paragraphs.append(block)

    for index, section in enumerate(flat, 1):
        section.element_id = f"section{index}"
        section.content = "\n\n".join(paragraph.text for paragraph in section.paragraphs)

    roots: list[Section] = []
    stack: list[Section] = []
    for section in flat:
        level = section.heading_level.value if section.heading_level else 0
        while stack and (stack[-1].heading_level.value if stack[-1].heading_level else 0) >= level:
            stack.pop()
        if stack:
            stack[-1].subsections.append(section)
        else:
            roots.append(section)
        stack.append(section)
    for section in roots:
        _set_page_range(section)
    return roots


def _set_page_range(section: Section) -> None:
    pages = [block.page_number for block in section.blocks if block.page_number > 0]
    if section.heading_paragraph and section.heading_paragraph.page_number > 0:
        pages.append(section.heading_paragraph.page_number)
    for child in section.subsections:
        _set_page_range(child)
        pages.extend(page for page in (child.page_number, child.page_end) if page > 0)
    if pages:
        section.page_number = min(pages)
        section.page_end = max(pages)
