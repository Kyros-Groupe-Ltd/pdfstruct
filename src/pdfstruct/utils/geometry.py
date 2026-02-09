"""Geometric utilities for spatial analysis of PDF elements."""

from __future__ import annotations

from pdfstruct.models.document import BBox


def horizontal_overlap(a: BBox, b: BBox) -> float:
    """Return the horizontal overlap ratio between two bounding boxes.

    Returns a value between 0.0 (no overlap) and 1.0 (full overlap).
    """
    overlap = max(0, min(a.x1, b.x1) - max(a.x0, b.x0))
    min_width = min(a.width, b.width)
    if min_width == 0:
        return 0.0
    return overlap / min_width


def vertical_distance(a: BBox, b: BBox) -> float:
    """Return the vertical gap between two bounding boxes.

    Positive = b is below a (PDF coords: a.y0 > b.y1).
    Negative = they overlap vertically.
    """
    # In PDF coords, y increases upward, so "below" means lower y
    return a.y0 - b.y1


def are_on_same_line(a: BBox, b: BBox, tolerance: float = 2.0) -> bool:
    """Check if two boxes are on the same horizontal line."""
    # Compare vertical midpoints
    mid_a = (a.y0 + a.y1) / 2
    mid_b = (b.y0 + b.y1) / 2
    return abs(mid_a - mid_b) <= tolerance


def cluster_by_y(bboxes: list[BBox], tolerance: float = 2.0) -> list[list[int]]:
    """Cluster bounding box indices by their y-position (same line).

    Returns list of lists of indices, sorted top-to-bottom.
    """
    if not bboxes:
        return []

    # Sort by y-midpoint descending (top of page first in PDF coords)
    indexed = [(i, (b.y0 + b.y1) / 2) for i, b in enumerate(bboxes)]
    indexed.sort(key=lambda x: -x[1])

    clusters: list[list[int]] = []
    current_cluster = [indexed[0][0]]
    current_y = indexed[0][1]

    for i in range(1, len(indexed)):
        idx, y_mid = indexed[i]
        if abs(y_mid - current_y) <= tolerance:
            current_cluster.append(idx)
        else:
            clusters.append(current_cluster)
            current_cluster = [idx]
            current_y = y_mid

    clusters.append(current_cluster)
    return clusters


def find_columns(bboxes: list[BBox], page_width: float,
                 min_gap_ratio: float = 0.03) -> list[tuple[float, float]]:
    """Detect column boundaries from a set of text line bounding boxes.

    Returns list of (x_start, x_end) tuples for each detected column.
    A gap is considered a column separator if it's at least min_gap_ratio
    of the page width.
    """
    if not bboxes:
        return []

    min_gap = page_width * min_gap_ratio

    # Project all bboxes onto the x-axis as intervals
    intervals = sorted([(b.x0, b.x1) for b in bboxes], key=lambda x: x[0])

    # Merge overlapping intervals
    merged: list[tuple[float, float]] = [intervals[0]]
    for start, end in intervals[1:]:
        prev_start, prev_end = merged[-1]
        if start <= prev_end + min_gap:
            merged[-1] = (prev_start, max(prev_end, end))
        else:
            merged.append((start, end))

    # Only report as columns if we found more than one
    if len(merged) < 2:
        return [(merged[0][0], merged[0][1])] if merged else []

    return merged


def group_into_lines(chars: list[dict], y_tolerance: float = 2.0) -> list[list[dict]]:
    """Group character dicts by their y-position into lines.

    Each char dict must have 'bbox' with y0, y1 values.
    Returns lines sorted top-to-bottom, chars within each line sorted left-to-right.
    """
    if not chars:
        return []

    # Sort by y-midpoint descending (top first)
    sorted_chars = sorted(chars, key=lambda c: -(c["bbox"].y0 + c["bbox"].y1) / 2)

    lines: list[list[dict]] = []
    current_line = [sorted_chars[0]]
    current_y = (sorted_chars[0]["bbox"].y0 + sorted_chars[0]["bbox"].y1) / 2

    for char in sorted_chars[1:]:
        char_y = (char["bbox"].y0 + char["bbox"].y1) / 2
        if abs(char_y - current_y) <= y_tolerance:
            current_line.append(char)
        else:
            # Sort current line left-to-right and save
            current_line.sort(key=lambda c: c["bbox"].x0)
            lines.append(current_line)
            current_line = [char]
            current_y = char_y

    current_line.sort(key=lambda c: c["bbox"].x0)
    lines.append(current_line)
    return lines


def compute_line_spacing(bboxes: list[BBox]) -> float:
    """Compute median vertical spacing between consecutive lines.

    Assumes bboxes are sorted top-to-bottom.
    """
    if len(bboxes) < 2:
        return 0.0

    spacings = []
    for i in range(1, len(bboxes)):
        # Gap between bottom of previous line and top of current line
        gap = bboxes[i - 1].y0 - bboxes[i].y1
        spacings.append(gap)

    spacings.sort()
    return spacings[len(spacings) // 2]  # median
