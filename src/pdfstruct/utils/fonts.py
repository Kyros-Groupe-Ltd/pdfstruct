"""Font analysis utilities for structure detection."""

from __future__ import annotations

from collections import Counter
from typing import Optional

from pdfstruct.models.document import FontInfo, TextLine


# Common bold indicators in font names
BOLD_INDICATORS = {
    "bold", "black", "heavy", "demi", "semibold", "extrabold", "ultrabold",
    "medium",  # some fonts use Medium as semi-bold
}

# Common italic indicators in font names
ITALIC_INDICATORS = {"italic", "oblique", "slanted", "inclined"}


def detect_font_weight(font_name: str) -> bool:
    """Detect if a font name indicates bold weight."""
    name_lower = font_name.lower()
    return any(indicator in name_lower for indicator in BOLD_INDICATORS)


def detect_font_style(font_name: str) -> bool:
    """Detect if a font name indicates italic style."""
    name_lower = font_name.lower()
    return any(indicator in name_lower for indicator in ITALIC_INDICATORS)


def parse_font_info(font_name: str, font_size: float) -> FontInfo:
    """Create a FontInfo from a raw font name and size."""
    return FontInfo(
        name=font_name,
        size=font_size,
        is_bold=detect_font_weight(font_name),
        is_italic=detect_font_style(font_name),
    )


def compute_font_statistics(lines: list[TextLine]) -> dict:
    """Analyze font usage across all lines to determine the document's font profile.

    Returns:
        {
            "primary_font": str,       # most common font name
            "primary_size": float,     # most common font size
            "font_sizes": Counter,     # font size distribution
            "font_names": Counter,     # font name distribution
            "size_tiers": list[float], # distinct font sizes sorted descending
        }
    """
    if not lines:
        return {
            "primary_font": "",
            "primary_size": 0.0,
            "font_sizes": Counter(),
            "font_names": Counter(),
            "size_tiers": [],
        }

    size_counter: Counter[float] = Counter()
    name_counter: Counter[str] = Counter()

    for line in lines:
        if not line.text.strip():
            continue
        # Weight by character count so body text dominates
        weight = len(line.text)
        rounded_size = round(line.font.size, 1)
        size_counter[rounded_size] += weight
        name_counter[line.font.normalized_name] += weight

    primary_size = size_counter.most_common(1)[0][0] if size_counter else 0.0
    primary_font = name_counter.most_common(1)[0][0] if name_counter else ""

    # Build sorted size tiers (unique sizes, descending)
    size_tiers = sorted(set(size_counter.keys()), reverse=True)

    return {
        "primary_font": primary_font,
        "primary_size": primary_size,
        "font_sizes": size_counter,
        "font_names": name_counter,
        "size_tiers": size_tiers,
    }


def classify_heading_level(
    font_size: float,
    is_bold: bool,
    primary_size: float,
    size_tiers: list[float],
    text: str = "",
) -> Optional[int]:
    """Determine heading level based on font size relative to body text.

    Returns 1-6 for headings, None for body text.

    Logic:
    - Font size must be larger than body text (primary_size) OR bold at same size
    - Borderline sizes (< 15% larger) need additional signals (bold, short, ALL CAPS)
    - Level determined by position in the font size tier list
    """
    if font_size < primary_size:
        return None

    if font_size == primary_size:
        # Same size as body — only a heading if bold
        if is_bold:
            return 4  # minor heading (bold body text)
        return None

    # Check if size difference is too small to be a reliable heading signal
    size_ratio = font_size / primary_size if primary_size > 0 else 999
    if size_ratio < 1.15:
        # Borderline: only classify as heading if also bold, short, or ALL CAPS
        if is_bold:
            return 5
        if text and len(text) < 80 and text.isupper():
            return 5
        # Small size difference + not bold + not caps = likely body text
        return None

    # Find this size's position in the tier list (excluding body size and small diffs)
    min_heading_size = primary_size * 1.15
    heading_tiers = [s for s in size_tiers if s >= min_heading_size]

    if not heading_tiers:
        return 5 if is_bold else None

    # Find the closest tier for this font size
    if font_size not in heading_tiers:
        # Font is larger than primary but not in significant tiers
        return 5 if is_bold else None

    # Position in heading tiers: 0 = largest = H1
    tier_index = heading_tiers.index(font_size)

    if tier_index == 0:
        return 1
    elif tier_index == 1:
        return 2
    elif tier_index == 2:
        return 3
    elif tier_index == 3:
        return 4
    elif tier_index == 4:
        return 5
    else:
        return 6
