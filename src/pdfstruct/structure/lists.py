"""List detection — bulleted, numbered, lettered, and roman numeral lists."""

from __future__ import annotations

import re

from pdfstruct.models.document import (
    ListItem,
    ListType,
    Paragraph,
    TextLine,
)

# Bullet markers
BULLET_CHARS = {"•", "●", "○", "■", "□", "▪", "▸", "▹", "►", "◆", "◇", "–", "—", "·", "-", "*"}

# Ordered list patterns
NUMBERED_LIST = re.compile(r"^(\d{1,3})[.)]\s+(.+)$")
LETTERED_LIST = re.compile(r"^([a-zA-Z])[.)]\s+(.+)$")
ROMAN_LIST = re.compile(
    r"^((?:x{0,3})(?:ix|iv|v?i{0,3}))[.)]\s+(.+)$", re.IGNORECASE
)


def detect_lists_in_paragraphs(paragraphs: list[Paragraph]) -> list[Paragraph]:
    """Scan paragraphs and convert sequences of list-like lines into list items.

    Mutates paragraphs in-place and returns them for chaining.
    """
    for para in paragraphs:
        if para.is_heading or not para.lines:
            continue

        items = _extract_list_items(para.lines)
        if items:
            para.list_items = [item for item, _ in items]
            para.list_type = items[0][1]
            # Rebuild text from list items
            para.text = "\n".join(
                f"{item.marker} {item.text}" for item in para.list_items
            )

    return paragraphs


def _extract_list_items(
    lines: list[TextLine],
) -> list[tuple[ListItem, ListType]] | None:
    """Try to parse lines as a list.

    Returns list of (ListItem, ListType) tuples if lines form a list,
    or None if they don't.
    """
    if len(lines) < 2:
        return None

    items: list[tuple[ListItem, ListType]] = []
    anchor: TextLine | None = None
    previous: TextLine | None = None

    for line in lines:
        if not line.text.strip():
            continue

        result = _parse_list_line(line.text)
        if result:
            marker, content, list_type, level = result
            items.append((
                ListItem(
                    text=content,
                    marker=marker,
                    level=level,
                    bbox=line.bbox,
                ),
                list_type,
            ))
            anchor = line
        else:
            # A paragraph can contain a preamble, an ordinary sentence between
            # bullets, or an unindented continuation. Do not drop that content
            # merely because two other lines happen to look like list items.
            # Only fold an unambiguously indented, adjacent continuation into
            # its item. Otherwise retain the complete original paragraph.
            if anchor is None or previous is None:
                return None
            if (
                line.is_heading
                or line.page_number != anchor.page_number
                or line.bbox.x0 - anchor.bbox.x0 < max(2.0, anchor.font.size * 0.4)
                or line.bbox.y1 >= previous.bbox.y1
                or abs(line.font.size - anchor.font.size) > anchor.font.size * 0.2
            ):
                return None
            item = items[-1][0]
            item.text += " " + line.text.strip()
            item.bbox = item.bbox.merge(line.bbox) if item.bbox else line.bbox
        previous = line

    # Require at least 2 items with the same type to be a list
    if len(items) < 2:
        return None

    # The output model has one list type per paragraph. Mixed marker families
    # are ambiguous; preserve their original text rather than relabeling them.
    if len({list_type for _, list_type in items}) != 1:
        return None

    return items


def _parse_list_line(text: str) -> tuple[str, str, ListType, int] | None:
    """Parse a single line as a potential list item.

    Returns (marker, content, list_type, indent_level) or None.
    """
    # Check leading whitespace for nesting level
    stripped = text.lstrip()
    indent = len(text) - len(stripped)
    level = indent // 2  # rough nesting level

    text = stripped

    # Bullet markers
    if text and text[0] in BULLET_CHARS:
        content = text[1:].strip()
        if content:
            return (text[0], content, ListType.BULLETED, level)

    # Numbered: "1. Item" or "1) Item"
    m = NUMBERED_LIST.match(text)
    if m:
        return (text[:m.start(2)].strip(), m.group(2), ListType.NUMBERED, level)

    # Lettered: "a. Item" or "a) Item"
    m = LETTERED_LIST.match(text)
    if m:
        return (text[:m.start(2)].strip(), m.group(2), ListType.LETTERED, level)

    # Roman: "i. Item" or "ii) Item"
    m = ROMAN_LIST.match(text)
    if m:
        return (text[:m.start(2)].strip(), m.group(2), ListType.ROMAN, level)

    return None
