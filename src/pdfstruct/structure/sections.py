"""Section builder — organizes paragraphs into a hierarchical section tree."""

from __future__ import annotations

from typing import Optional

from pdfstruct.models.document import (
    Section, Paragraph, Table, HeadingLevel,
)


def build_sections(
    paragraphs: list[Paragraph],
    tables: list[Table],
) -> list[Section]:
    """Build a hierarchical section tree from paragraphs and tables.

    Paragraphs marked as headings become section headers. Content between
    headings becomes the section body. Subsections nest based on heading level.
    """
    if not paragraphs:
        return []

    # If no headings at all, return a single section with all content
    has_headings = any(p.is_heading for p in paragraphs)
    if not has_headings:
        return [_build_single_section(paragraphs, tables)]

    # Build flat list of sections first
    flat_sections: list[Section] = []
    current_content: list[Paragraph] = []

    for para in paragraphs:
        if para.is_heading:
            # Save accumulated content to previous section
            if current_content and flat_sections:
                _add_content_to_section(flat_sections[-1], current_content, tables)
                current_content = []
            elif current_content:
                # Content before first heading — create an untitled section
                flat_sections.append(_build_content_section(current_content, tables))
                current_content = []

            # Start new section
            flat_sections.append(Section(
                heading=para.text,
                heading_level=para.heading_level,
                page_number=0,  # filled later if needed
            ))
        else:
            current_content.append(para)

    # Add remaining content to last section
    if current_content:
        if flat_sections:
            _add_content_to_section(flat_sections[-1], current_content, tables)
        else:
            flat_sections.append(_build_content_section(current_content, tables))

    # Build hierarchy from flat sections
    return _nest_sections(flat_sections)


def _build_single_section(paragraphs: list[Paragraph], tables: list[Table]) -> Section:
    """Create a single section from all paragraphs (no headings detected)."""
    content = "\n\n".join(p.text for p in paragraphs)
    return Section(
        heading=None,
        heading_level=None,
        content=content,
        paragraphs=paragraphs,
        tables=tables,
    )


def _build_content_section(paragraphs: list[Paragraph], tables: list[Table]) -> Section:
    """Create an untitled section from content before the first heading."""
    content = "\n\n".join(p.text for p in paragraphs)
    return Section(
        heading=None,
        heading_level=None,
        content=content,
        paragraphs=paragraphs,
    )


def _add_content_to_section(
    section: Section,
    paragraphs: list[Paragraph],
    tables: list[Table],
) -> None:
    """Add paragraphs to a section's content."""
    section.paragraphs.extend(paragraphs)
    section.content = "\n\n".join(p.text for p in section.paragraphs)


def _nest_sections(flat_sections: list[Section]) -> list[Section]:
    """Build a nested section hierarchy from a flat list.

    Uses heading levels to determine parent-child relationships:
    H1 > H2 > H3, etc. An H2 becomes a subsection of the preceding H1.
    """
    if not flat_sections:
        return []

    root_sections: list[Section] = []
    stack: list[Section] = []  # stack of parent sections

    for section in flat_sections:
        level = _get_level(section)

        # Pop stack until we find a parent with a lower level (higher heading)
        while stack and _get_level(stack[-1]) >= level:
            stack.pop()

        if stack:
            # This section is a subsection of the top of the stack
            stack[-1].subsections.append(section)
        else:
            # This is a root-level section
            root_sections.append(section)

        stack.append(section)

    return root_sections


def _get_level(section: Section) -> int:
    """Get the numeric heading level, defaulting to 0 for untitled sections."""
    if section.heading_level:
        return section.heading_level.value
    return 0
