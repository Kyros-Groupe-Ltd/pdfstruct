"""Header and footer detection across pages.

Identifies repeating content at the top/bottom of pages that should be
excluded from the main document content.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Optional

from pdfstruct.models.document import TextLine, BBox


# Patterns that commonly appear in headers/footers
PAGE_NUMBER_PATTERN = re.compile(
    r"^(?:page\s*)?\d{1,4}(?:\s*(?:of|/)\s*\d{1,4})?$", re.IGNORECASE
)
HEADER_FOOTER_ZONE_RATIO = 0.08  # top/bottom 8% of page height


def detect_headers_footers(
    pages_lines: list[list[TextLine]],
    page_heights: list[float],
    zone_ratio: float = HEADER_FOOTER_ZONE_RATIO,
    min_pages_for_detection: int = 3,
    similarity_threshold: float = 0.7,
) -> tuple[set[str], set[str]]:
    """Detect repeating header and footer text across multiple pages.

    Returns:
        (header_texts, footer_texts) — sets of text strings to exclude
    """
    if len(pages_lines) < min_pages_for_detection:
        return set(), set()

    header_candidates: Counter[str] = Counter()
    footer_candidates: Counter[str] = Counter()

    for page_idx, (lines, page_height) in enumerate(zip(pages_lines, page_heights)):
        if not lines:
            continue

        top_zone = page_height * (1 - zone_ratio)
        bottom_zone = page_height * zone_ratio

        for line in lines:
            text = line.text.strip()
            if not text:
                continue

            # Normalize page numbers for comparison
            normalized = _normalize_for_comparison(text)

            # Check if line is in header zone (top of page)
            if line.bbox.y1 >= top_zone:
                header_candidates[normalized] += 1
                line.is_header = True

            # Check if line is in footer zone (bottom of page)
            elif line.bbox.y0 <= bottom_zone:
                footer_candidates[normalized] += 1
                line.is_footer = True

    total_pages = len(pages_lines)
    min_occurrences = max(2, int(total_pages * similarity_threshold))

    # Headers: text that appears in the header zone on most pages
    header_texts = {
        text for text, count in header_candidates.items()
        if count >= min_occurrences
    }

    # Footers: text that appears in the footer zone on most pages
    footer_texts = {
        text for text, count in footer_candidates.items()
        if count >= min_occurrences
    }

    # Mark lines properly based on confirmed headers/footers
    for lines in pages_lines:
        for line in lines:
            normalized = _normalize_for_comparison(line.text.strip())
            line.is_header = normalized in header_texts
            line.is_footer = normalized in footer_texts

            # Also catch standalone page numbers
            if PAGE_NUMBER_PATTERN.match(line.text.strip()):
                line.is_footer = True

    return header_texts, footer_texts


def _normalize_for_comparison(text: str) -> str:
    """Normalize text for header/footer comparison.

    Replaces page numbers with a placeholder so "Page 1" and "Page 2"
    are treated as the same repeating element.
    """
    # Replace page numbers with placeholder
    normalized = re.sub(r"\b\d{1,4}\b", "#", text)
    # Normalize whitespace
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def filter_headers_footers(lines: list[TextLine]) -> list[TextLine]:
    """Remove lines marked as headers or footers."""
    return [line for line in lines if not line.is_header and not line.is_footer]
