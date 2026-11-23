"""Canonical paragraph/table order without duplicating the table's text."""

from __future__ import annotations

from pdfstruct.models.document import Paragraph, Table


def source_position(block: Paragraph | Table) -> tuple[int, float, float]:
    """Physical fallback for unanchored blocks, in PDF points."""
    box = block.bbox
    return (block.page_number, -box.y1 if box else 0.0, box.x0 if box else 0.0)


def content_blocks(paragraphs: list[Paragraph], tables: list[Table]) -> list[Paragraph | Table]:
    """Preserve paragraph reading order, replacing table text at its first anchor.

    Tables without a text anchor (for example, an empty grid) use physical page
    position. This fallback is a layout heuristic, not a promise about arbitrary
    multicolumn reading order.
    """
    by_id = {table.element_id: table for table in tables if table.element_id}
    seen: set[int] = set()
    blocks: list[Paragraph | Table] = []
    for paragraph in paragraphs:
        table = by_id.get(paragraph.table_id) if paragraph.table_id else None
        if table is None:
            blocks.append(paragraph)
        elif id(table) not in seen:
            blocks.append(table)
            seen.add(id(table))

    for table in tables:
        if id(table) in seen:
            continue
        key = source_position(table)
        index = next(
            (i for i, block in enumerate(blocks) if source_position(block) > key),
            len(blocks),
        )
        blocks.insert(index, table)
        seen.add(id(table))
    return blocks
