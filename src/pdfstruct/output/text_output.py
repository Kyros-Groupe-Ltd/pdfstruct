"""Plain text output formatter."""

from __future__ import annotations

from pdfstruct.models.document import Document, Section


def to_text(doc: Document, include_structure: bool = True) -> str:
    """Convert a Document to plain text.

    Args:
        include_structure: if True, add blank lines between sections
            and prefix headings with markers
    """
    if not include_structure:
        return doc.text

    parts: list[str] = []

    if doc.title:
        parts.append(doc.title)
        parts.append("=" * len(doc.title))
        parts.append("")

    for section in doc.sections:
        parts.append(_section_to_text(section))
        parts.append("")

    # Tables
    for table in doc.tables:
        for row in table.rows:
            cells = [cell.text for cell in row.cells]
            parts.append(" | ".join(cells))
        parts.append("")

    # Image placeholders
    for page in doc.pages:
        if page.images:
            for img in page.images:
                caption = img.caption or f"Image {img.image_index + 1}"
                parts.append(f"[{caption} ({img.width_px}x{img.height_px})]")
            parts.append("")

    return "\n".join(parts).strip() + "\n"


def _section_to_text(section: Section, depth: int = 0) -> str:
    """Convert a section to plain text with indentation."""
    parts: list[str] = []

    if section.heading:
        parts.append(section.heading)
        underline = {1: "=", 2: "-", 3: "~"}.get(
            section.heading_level.value if section.heading_level else 3, "~"
        )
        parts.append(underline * len(section.heading))

    if section.content:
        parts.append(section.content)

    for sub in section.subsections:
        parts.append("")
        parts.append(_section_to_text(sub, depth + 1))

    return "\n".join(parts)
