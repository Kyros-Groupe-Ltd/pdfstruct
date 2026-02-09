"""Font-aware heading detection.

This module classifies text lines as headings based on:
1. Font size relative to body text (primary signal)
2. Font weight (bold)
3. Regex patterns (numbered sections, ALL CAPS, etc.)
4. Line position and surrounding context
"""

from __future__ import annotations

import re
from typing import Optional

from pdfstruct.models.document import HeadingLevel, TextLine
from pdfstruct.utils.fonts import compute_font_statistics, classify_heading_level


# Regex patterns for structural heading detection
NUMBERED_HEADING = re.compile(
    r"^(\d{1,3}\.(?:\d{1,3}\.){0,4})\s+(.+)$"
)
ROMAN_HEADING = re.compile(
    r"^((?:X{0,3})(IX|IV|V?I{0,3}))\.\s+(.+)$", re.IGNORECASE
)
LETTERED_HEADING = re.compile(
    r"^([A-Z])\.\s+(.+)$"
)
ALL_CAPS_LINE = re.compile(
    r"^[A-Z][A-Z\s\-&/,.:;()]{2,97}$"
)
COLON_HEADING = re.compile(
    r"^(.{3,60}):$"
)


def detect_headings(
    lines: list[TextLine],
    font_stats: Optional[dict] = None,
) -> list[TextLine]:
    """Detect and mark heading lines using font analysis + regex patterns.

    This mutates the lines in-place (sets is_heading and heading_level).
    Also returns the list for chaining.

    Strategy:
    1. First pass: font-based detection (size > body text, bold)
    2. Second pass: regex-based detection (for same-font documents)
    3. Conflict resolution: font signals override regex signals
    """
    if not lines:
        return lines

    if font_stats is None:
        font_stats = compute_font_statistics(lines)

    primary_size = font_stats["primary_size"]
    size_tiers = font_stats["size_tiers"]

    # Pass 1: Font-based heading detection
    for line in lines:
        text = line.text.strip()
        if not text:
            continue

        # Skip very long lines (unlikely to be headings)
        if len(text) > 200:
            continue

        # Skip very short lines at body font size — likely table headers, not doc headings
        if line.font.size <= primary_size and len(text.split()) <= 2:
            continue

        # Skip lines that end with sentence-ending punctuation (likely body text)
        # unless they're significantly larger font (>= 20% larger)
        size_ratio = line.font.size / primary_size if primary_size > 0 else 999
        if text[-1] in ".!?" and size_ratio < 1.2:
            continue

        # Skip long lines that look like body text (> 100 chars) unless clearly heading-sized
        if len(text) > 100 and size_ratio < 1.2:
            continue

        level = classify_heading_level(
            font_size=round(line.font.size, 1),
            is_bold=line.font.is_bold,
            primary_size=primary_size,
            size_tiers=size_tiers,
            text=text,
        )

        if level is not None:
            line.is_heading = True
            line.heading_level = HeadingLevel(min(level, 6))

    # Pass 2: Regex-based detection for lines not yet classified
    # Only apply if the document has few font-based headings
    font_heading_count = sum(1 for l in lines if l.is_heading)
    has_font_headings = font_heading_count >= 2

    if not has_font_headings:
        for line in lines:
            if line.is_heading:
                continue

            text = line.text.strip()
            if not text or len(text) > 150:
                continue

            level = _regex_heading_level(text, line.font.is_bold)
            if level is not None:
                line.is_heading = True
                line.heading_level = HeadingLevel(min(level, 6))

    return lines


def _regex_heading_level(text: str, is_bold: bool) -> Optional[int]:
    """Detect heading level from text patterns alone.

    Returns 1-6 or None.
    """
    # Numbered: "1. Introduction" → H2, "1.1 Overview" → H3
    m = NUMBERED_HEADING.match(text)
    if m:
        num_part = m.group(1)
        depth = num_part.count(".")
        return min(depth + 1, 6)

    # Roman numerals: "I. Introduction" → H1
    m = ROMAN_HEADING.match(text)
    if m:
        return 1

    # Letter sections: "A. Section" → H2
    m = LETTERED_HEADING.match(text)
    if m:
        return 2

    # ALL CAPS: "INTRODUCTION" → H2 (if short enough and not a sentence)
    if ALL_CAPS_LINE.match(text):
        words = text.split()
        if len(words) <= 6:
            return 2 if is_bold else 3

    # Colon-ended: "Introduction:" → H3 (weakest signal)
    m = COLON_HEADING.match(text)
    if m and is_bold:
        return 3

    return None
