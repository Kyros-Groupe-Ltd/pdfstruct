"""List detection — bulleted, numbered, lettered, and roman numeral lists."""

from __future__ import annotations

import re
from typing import Optional

from pdfstruct.models.document import (
    Paragraph, ListItem, ListType, TextLine, BBox,
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
) -> Optional[list[tuple[ListItem, ListType]]]:
    """Try to parse lines as a list.

    Returns list of (ListItem, ListType) tuples if lines form a list,
    or None if they don't.
    """
    if len(lines) < 2:
        return None

    items: list[tuple[ListItem, ListType]] = []

    for line in lines:
        text = line.text.strip()
        if not text:
            continue

        result = _parse_list_line(text)
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

    # Require at least 2 items with the same type to be a list
    if len(items) < 2:
        return None

    # Check that most items have the same list type
    types = [lt for _, lt in items]
    from collections import Counter
    type_counts = Counter(types)
    dominant_type, dominant_count = type_counts.most_common(1)[0]

    if dominant_count < len(items) * 0.6:
        return None

    return items


def _parse_list_line(text: str) -> Optional[tuple[str, str, ListType, int]]:
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
        return (m.group(1) + ".", m.group(2), ListType.NUMBERED, level)

    # Lettered: "a. Item" or "a) Item"
    m = LETTERED_LIST.match(text)
    if m:
        return (m.group(1) + ".", m.group(2), ListType.LETTERED, level)

    # Roman: "i. Item" or "ii) Item"
    m = ROMAN_LIST.match(text)
    if m:
        return (m.group(1) + ".", m.group(2), ListType.ROMAN, level)

    return None
