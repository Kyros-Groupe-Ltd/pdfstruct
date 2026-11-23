"""Layout analysis engine.

Takes raw extracted lines and groups them into paragraphs, detects columns,
determines reading order, and identifies headers/footers.
"""

from __future__ import annotations

import logging
from collections import Counter

from pdfstruct.models.document import (
    BBox,
    Paragraph,
    TextLine,
)
from pdfstruct.utils.geometry import (
    compute_line_spacing,
    find_columns,
)

logger = logging.getLogger(__name__)


def detect_page_margins(lines: list[TextLine], page_width: float, page_height: float) -> dict:
    """Detect the effective content margins of a page.

    Returns dict with left, right, top, bottom margin values.
    """
    if not lines:
        return {"left": 0, "right": page_width, "top": page_height, "bottom": 0}

    left = min(line.bbox.x0 for line in lines)
    right = max(line.bbox.x1 for line in lines)
    top = max(line.bbox.y1 for line in lines)
    bottom = min(line.bbox.y0 for line in lines)

    return {
        "left": left,
        "right": right,
        "top": top,
        "bottom": bottom,
        "left_margin": left,
        "right_margin": page_width - right,
        "top_margin": page_height - top,
        "bottom_margin": bottom,
    }


def group_lines_into_paragraphs(
    lines: list[TextLine],
    page_width: float,
    line_spacing_factor: float = 1.8,
) -> list[Paragraph]:
    """Group text lines into paragraphs based on spacing and indentation.

    A new paragraph starts when:
    1. The vertical gap between lines exceeds line_spacing_factor * median spacing
    2. The line is indented relative to the previous line
    3. The font changes significantly (size or family)
    4. The line is a detected heading

    Args:
        lines: sorted top-to-bottom
        page_width: page width for margin calculations
        line_spacing_factor: multiplier for paragraph break detection
    """
    if not lines:
        return []

    if len(lines) == 1:
        return [_line_to_paragraph(lines[0])]

    # Compute median line spacing
    bboxes = [line.bbox for line in lines]
    median_spacing = compute_line_spacing(bboxes)
    if median_spacing <= 0:
        median_spacing = lines[0].font.size * 0.3  # fallback

    paragraph_break_threshold = median_spacing * line_spacing_factor

    # Detect typical left margin (most common x0 position)
    x0_values = [round(line.bbox.x0, 0) for line in lines]
    x0_counter = Counter(x0_values)
    typical_x0 = x0_counter.most_common(1)[0][0] if x0_counter else 0

    paragraphs: list[Paragraph] = []
    current_lines: list[TextLine] = [lines[0]]

    for i in range(1, len(lines)):
        prev_line = lines[i - 1]
        curr_line = lines[i]

        should_break = False

        # 1. Check vertical gap
        gap = prev_line.bbox.y0 - curr_line.bbox.y1
        if gap > paragraph_break_threshold:
            should_break = True

        # 2. Check if current line is a heading
        if curr_line.is_heading or prev_line.is_heading:
            should_break = True

        # 3. Check font size change (>20% different)
        if prev_line.font.size > 0 and curr_line.font.size > 0:
            size_ratio = curr_line.font.size / prev_line.font.size
            if size_ratio > 1.2 or size_ratio < 0.8:
                should_break = True

        # 4. Check indentation change (first-line indent)
        indent_diff = curr_line.bbox.x0 - typical_x0
        if indent_diff > curr_line.font.size * 1.5 and gap > median_spacing * 0.5:
            should_break = True

        if should_break:
            paragraphs.append(_build_paragraph(current_lines))
            current_lines = [curr_line]
        else:
            current_lines.append(curr_line)

    # Don't forget the last paragraph
    if current_lines:
        paragraphs.append(_build_paragraph(current_lines))

    return paragraphs


def _line_to_paragraph(line: TextLine) -> Paragraph:
    """Convert a single line to a paragraph."""
    return Paragraph(
        text=line.text,
        lines=[line],
        bbox=line.bbox,
        font=line.font,
        is_heading=line.is_heading,
        heading_level=line.heading_level,
        page_number=line.page_number,
        source=line.source,
    )


def _build_paragraph(lines: list[TextLine]) -> Paragraph:
    """Build a paragraph from a group of lines."""
    text = " ".join(line.text for line in lines)

    # Compute bounding box encompassing all lines
    bbox = BBox(
        x0=min(l.bbox.x0 for l in lines),
        y0=min(l.bbox.y0 for l in lines),
        x1=max(l.bbox.x1 for l in lines),
        y1=max(l.bbox.y1 for l in lines),
    )

    # Use the dominant font from the first line
    font = lines[0].font

    # If first line is a heading and it's the only line, mark paragraph as heading
    is_heading = len(lines) == 1 and lines[0].is_heading
    heading_level = lines[0].heading_level if is_heading else None

    return Paragraph(
        text=text,
        lines=lines,
        bbox=bbox,
        font=font,
        is_heading=is_heading,
        heading_level=heading_level,
        page_number=lines[0].page_number,
        source=lines[0].source if len({line.source for line in lines}) == 1 else "mixed",
    )


def sort_lines_reading_order(
    lines: list[TextLine],
    page_width: float,
    columns: list[tuple[float, float]] | None = None,
) -> list[TextLine]:
    """Sort lines in natural reading order.

    For single-column layouts: top-to-bottom.
    For multi-column layouts: left column top-to-bottom, then right column top-to-bottom.
    """
    if not lines:
        return []

    if columns is None:
        columns = find_columns([l.bbox for l in lines], page_width)

    if len(columns) <= 1:
        # Single column: sort top-to-bottom
        return sorted(lines, key=lambda l: -l.bbox.y1)

    # Multi-column: assign each line to a column, then sort within each column
    column_lines: list[list[TextLine]] = [[] for _ in columns]

    for line in lines:
        line_mid_x = (line.bbox.x0 + line.bbox.x1) / 2
        best_col = 0
        best_dist = float("inf")
        for i, (col_start, col_end) in enumerate(columns):
            col_mid = (col_start + col_end) / 2
            dist = abs(line_mid_x - col_mid)
            if dist < best_dist:
                best_dist = dist
                best_col = i
        column_lines[best_col].append(line)

    # Sort each column top-to-bottom, then concatenate left-to-right
    result: list[TextLine] = []
    for col_lines in column_lines:
        col_lines.sort(key=lambda l: -l.bbox.y1)
        result.extend(col_lines)

    return result
