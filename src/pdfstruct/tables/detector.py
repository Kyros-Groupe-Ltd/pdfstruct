"""Table detection using two strategies:

1. Grid-based: Detect explicit horizontal/vertical lines forming a grid
2. Whitespace-based: Detect aligned columns of text without grid lines
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Optional

from pdfstruct.models.document import BBox, TextLine, Table, TableRow, TableCell

logger = logging.getLogger(__name__)

# Minimum lines to form a grid edge
MIN_GRID_LINES = 3
# Tolerance for line alignment (points)
LINE_ALIGN_TOLERANCE = 2.0
# Minimum rows to consider a whitespace table
MIN_TABLE_ROWS = 2
# Minimum columns to consider a whitespace table
MIN_TABLE_COLS = 2


def detect_tables(
    lines: list[TextLine],
    rects: list[BBox],
    page_width: float,
    page_height: float,
) -> list[Table]:
    """Detect tables on a page using both grid and whitespace strategies.

    Args:
        lines: text lines on the page
        rects: rectangles/lines drawn on the page (for grid detection)
        page_width: width of the page
        page_height: height of the page

    Returns:
        List of detected Table objects
    """
    tables: list[Table] = []

    # Strategy 1: Grid-based detection
    grid_tables = _detect_grid_tables(lines, rects, page_width, page_height)
    tables.extend(grid_tables)

    # Strategy 2: Whitespace-based detection (for tables without grid lines)
    # Only for lines not already captured by grid tables
    grid_bboxes = [t.bbox for t in grid_tables if t.bbox]
    remaining_lines = [
        l for l in lines
        if not any(bbox.contains(l.bbox) for bbox in grid_bboxes)
    ]
    ws_tables = _detect_whitespace_tables(remaining_lines, page_width)
    tables.extend(ws_tables)

    return tables


def _detect_grid_tables(
    lines: list[TextLine],
    rects: list[BBox],
    page_width: float,
    page_height: float,
) -> list[Table]:
    """Detect tables formed by explicit grid lines (rectangles).

    Approach:
    1. Separate rects into horizontal and vertical lines
    2. Find intersections to form a grid
    3. Extract text within each cell
    """
    if len(rects) < 4:  # Need at least 4 lines for a cell
        return []

    # Classify rects as horizontal or vertical lines
    h_lines: list[BBox] = []  # horizontal
    v_lines: list[BBox] = []  # vertical

    for rect in rects:
        width = rect.width
        height = rect.height

        # Thin horizontal line
        if height < 3 and width > 10:
            h_lines.append(rect)
        # Thin vertical line
        elif width < 3 and height > 10:
            v_lines.append(rect)
        # Small rectangle — could be a border cell, include both dimensions
        elif width > 10 and height > 10:
            # This might be a filled cell or a border rectangle
            # Add its edges as lines
            h_lines.append(BBox(rect.x0, rect.y0, rect.x1, rect.y0))  # bottom
            h_lines.append(BBox(rect.x0, rect.y1, rect.x1, rect.y1))  # top
            v_lines.append(BBox(rect.x0, rect.y0, rect.x0, rect.y1))  # left
            v_lines.append(BBox(rect.x1, rect.y0, rect.x1, rect.y1))  # right

    if len(h_lines) < 2 or len(v_lines) < 2:
        return []

    # Cluster horizontal lines by y-position
    h_positions = _cluster_positions([l.y0 for l in h_lines], LINE_ALIGN_TOLERANCE)
    v_positions = _cluster_positions([l.x0 for l in v_lines], LINE_ALIGN_TOLERANCE)

    if len(h_positions) < MIN_GRID_LINES or len(v_positions) < MIN_TABLE_COLS + 1:
        return []

    # Sort positions
    h_positions.sort(reverse=True)  # top to bottom (PDF coords)
    v_positions.sort()  # left to right

    # Build grid cells and extract text
    rows: list[TableRow] = []
    for row_idx in range(len(h_positions) - 1):
        top_y = h_positions[row_idx]
        bottom_y = h_positions[row_idx + 1]

        cells: list[TableCell] = []
        for col_idx in range(len(v_positions) - 1):
            left_x = v_positions[col_idx]
            right_x = v_positions[col_idx + 1]

            cell_bbox = BBox(left_x, bottom_y, right_x, top_y)

            # Find text lines within this cell
            cell_text_parts = []
            for line in lines:
                if cell_bbox.overlaps(line.bbox, threshold=0.5):
                    cell_text_parts.append(line.text.strip())

            cell_text = " ".join(cell_text_parts)

            cells.append(TableCell(
                text=cell_text,
                row_index=row_idx,
                col_index=col_idx,
                bbox=cell_bbox,
                is_header=(row_idx == 0),  # first row assumed header
            ))

        if cells:
            rows.append(TableRow(
                cells=cells,
                is_header=(row_idx == 0),
                bbox=BBox(v_positions[0], bottom_y, v_positions[-1], top_y),
            ))

    if len(rows) < MIN_TABLE_ROWS:
        return []

    table_bbox = BBox(
        v_positions[0], h_positions[-1],
        v_positions[-1], h_positions[0],
    )

    return [Table(rows=rows, bbox=table_bbox)]


def _detect_whitespace_tables(
    lines: list[TextLine],
    page_width: float,
) -> list[Table]:
    """Detect tables formed by aligned whitespace columns.

    Approach:
    1. Find lines where text is split into multiple segments with consistent gaps
    2. Check if multiple consecutive lines share the same column structure
    3. Extract cell content based on column boundaries
    """
    if len(lines) < MIN_TABLE_ROWS:
        return []

    # Find potential table regions: consecutive lines with similar column structure
    tables: list[Table] = []
    table_lines: list[list[_ColumnSegment]] = []
    prev_cols: Optional[list[float]] = None

    for line in lines:
        segments = _split_line_into_segments(line)

        if len(segments) < MIN_TABLE_COLS:
            # Not enough columns — check if we have a pending table
            if len(table_lines) >= MIN_TABLE_ROWS:
                table = _build_whitespace_table(table_lines)
                if table:
                    tables.append(table)
            table_lines = []
            prev_cols = None
            continue

        # Check if column positions align with previous line
        col_positions = [seg.x_start for seg in segments]

        if prev_cols is None:
            table_lines = [segments]
            prev_cols = col_positions
        elif _columns_align(prev_cols, col_positions, tolerance=page_width * 0.02):
            table_lines.append(segments)
        else:
            if len(table_lines) >= MIN_TABLE_ROWS:
                table = _build_whitespace_table(table_lines)
                if table:
                    tables.append(table)
            table_lines = [segments]
            prev_cols = col_positions

    # Check remaining lines
    if len(table_lines) >= MIN_TABLE_ROWS:
        table = _build_whitespace_table(table_lines)
        if table:
            tables.append(table)

    return tables


class _ColumnSegment:
    """A text segment within a line, representing a potential table cell."""
    def __init__(self, text: str, x_start: float, x_end: float, y0: float, y1: float):
        self.text = text
        self.x_start = x_start
        self.x_end = x_end
        self.y0 = y0
        self.y1 = y1


def _split_line_into_segments(line: TextLine) -> list[_ColumnSegment]:
    """Split a text line into segments based on large whitespace gaps.

    Uses character positions to find gaps larger than 2x the average character width.
    """
    if not line.characters:
        return [_ColumnSegment(
            line.text, line.bbox.x0, line.bbox.x1, line.bbox.y0, line.bbox.y1
        )]

    chars = sorted(line.characters, key=lambda c: c.bbox.x0)

    # Compute average character width
    widths = [c.bbox.width for c in chars if c.text.strip()]
    if not widths:
        return []
    avg_width = sum(widths) / len(widths)
    gap_threshold = avg_width * 2.5

    segments: list[_ColumnSegment] = []
    current_chars: list = [chars[0]]

    for i in range(1, len(chars)):
        gap = chars[i].bbox.x0 - chars[i - 1].bbox.x1
        if gap > gap_threshold:
            # Start new segment
            text = "".join(c.text for c in current_chars).strip()
            if text:
                segments.append(_ColumnSegment(
                    text=text,
                    x_start=current_chars[0].bbox.x0,
                    x_end=current_chars[-1].bbox.x1,
                    y0=min(c.bbox.y0 for c in current_chars),
                    y1=max(c.bbox.y1 for c in current_chars),
                ))
            current_chars = [chars[i]]
        else:
            current_chars.append(chars[i])

    # Last segment
    text = "".join(c.text for c in current_chars).strip()
    if text:
        segments.append(_ColumnSegment(
            text=text,
            x_start=current_chars[0].bbox.x0,
            x_end=current_chars[-1].bbox.x1,
            y0=min(c.bbox.y0 for c in current_chars),
            y1=max(c.bbox.y1 for c in current_chars),
        ))

    return segments


def _columns_align(
    cols_a: list[float],
    cols_b: list[float],
    tolerance: float,
) -> bool:
    """Check if two sets of column positions are aligned."""
    if len(cols_a) != len(cols_b):
        return False
    return all(abs(a - b) <= tolerance for a, b in zip(cols_a, cols_b))


def _build_whitespace_table(
    all_segments: list[list[_ColumnSegment]],
) -> Optional[Table]:
    """Build a Table from aligned line segments."""
    if not all_segments:
        return None

    rows: list[TableRow] = []
    for row_idx, segments in enumerate(all_segments):
        cells = [
            TableCell(
                text=seg.text,
                row_index=row_idx,
                col_index=col_idx,
                bbox=BBox(seg.x_start, seg.y0, seg.x_end, seg.y1),
                is_header=(row_idx == 0),
            )
            for col_idx, seg in enumerate(segments)
        ]
        rows.append(TableRow(
            cells=cells,
            is_header=(row_idx == 0),
        ))

    # Compute table bounding box
    all_segs = [seg for row in all_segments for seg in row]
    if not all_segs:
        return None

    bbox = BBox(
        x0=min(s.x_start for s in all_segs),
        y0=min(s.y0 for s in all_segs),
        x1=max(s.x_end for s in all_segs),
        y1=max(s.y1 for s in all_segs),
    )

    return Table(rows=rows, bbox=bbox)


def _cluster_positions(values: list[float], tolerance: float) -> list[float]:
    """Cluster nearby float values and return the average of each cluster."""
    if not values:
        return []

    sorted_vals = sorted(values)
    clusters: list[list[float]] = [[sorted_vals[0]]]

    for val in sorted_vals[1:]:
        if val - clusters[-1][-1] <= tolerance:
            clusters[-1].append(val)
        else:
            clusters.append([val])

    return [sum(c) / len(c) for c in clusters]
