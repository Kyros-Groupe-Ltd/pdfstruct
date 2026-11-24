"""Detect local ruled tables, with a conservative whitespace-table fallback.

Drawing coordinates alone are not a grid: unrelated tables, background fills,
and inset rectangles frequently coexist on a page. Find closed cells from actual
edge coverage first, then group adjacent cells into independent table regions.
"""

from __future__ import annotations

from bisect import bisect_left
from collections import defaultdict
from dataclasses import dataclass
from math import isfinite
from statistics import median

from pdfstruct.models.document import BBox, Character, Table, TableCell, TableRow, TextLine

LINE_ALIGN_TOLERANCE = 2.0
MIN_TABLE_ROWS = 2
MIN_TABLE_COLS = 2
_MAX_RULE_THICKNESS = 2.0
_MIN_RULE_LENGTH = 2.0


@dataclass(frozen=True)
class TableDetectionLimits:
    """Per-page table-work limits, independent of PDF decoding/worker limits.

    Raising a limit permits more work; it does not make hostile PDF decoding
    safe. Apply process memory, CPU, and wall-time limits around parsing too.
    """

    max_rectangles: int = 10_000
    max_lines: int = 20_000
    max_characters: int = 200_000
    max_text_length: int = 2_000_000
    max_grid_intersections: int = 100_000
    max_cells: int = 5_000
    max_work: int = 2_000_000

    def __post_init__(self) -> None:
        for name, value in vars(self).items():
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")


class TableDetectionLimitError(RuntimeError):
    """Table detection stopped before returning a partial, misleading result."""

    def __init__(self, limit_name: str, limit: int, observed: int):
        self.limit_name = limit_name
        self.limit = limit
        self.observed = observed
        super().__init__(f"Table detection limit exceeded: {limit_name} ({observed} > {limit})")


@dataclass
class _WorkBudget:
    limits: TableDetectionLimits
    used: int = 0
    output_cells: int = 0

    def check(self, name: str, observed: int) -> None:
        limit = getattr(self.limits, name)
        if observed > limit:
            raise TableDetectionLimitError(name, limit, observed)

    def consume(self, work: int = 1) -> None:
        self.check("max_work", self.used + work)
        self.used += work

    def add_cells(self, count: int) -> None:
        self.check("max_cells", self.output_cells + count)
        self.output_cells += count


@dataclass(frozen=True)
class _Edge:
    horizontal: bool
    position: float
    start: float
    end: float


def detect_tables(
    lines: list[TextLine],
    rects: list[BBox],
    page_width: float,
    page_height: float,
    *,
    limits: TableDetectionLimits | None = None,
) -> list[Table]:
    """Detect separate tables, or raise ``TableDetectionLimitError`` on excess work.

    No partial table result is returned on exhaustion. Callers can preserve the
    original text and report that table interpretation was skipped for the page.
    Non-finite or inverted input geometry raises ``ValueError``.
    """
    budget = _WorkBudget(limits if limits is not None else TableDetectionLimits())
    _validate_inputs(lines, rects, page_width, page_height, budget)
    if not lines:
        return []
    grid_tables = _detect_grid_tables(lines, rects, page_width, page_height, budget=budget)
    grid_bboxes = [table.bbox for table in grid_tables if table.bbox]
    budget.consume(len(lines) * len(grid_bboxes))
    remaining_lines = [
        line for line in lines if not any(_contains_center(bbox, line.bbox) for bbox in grid_bboxes)
    ]
    tables = grid_tables + _detect_whitespace_tables(remaining_lines, page_width, budget=budget)
    return sorted(tables, key=lambda table: (-table.bbox.y1, table.bbox.x0))


def _validate_inputs(
    lines: list[TextLine], rects: list[BBox], width: float, height: float, budget: _WorkBudget
) -> None:
    # Check inexpensive counts before walking glyphs or allocating geometry.
    budget.check("max_rectangles", len(rects))
    budget.check("max_lines", len(lines))
    budget.check("max_characters", sum(len(line.characters) for line in lines))
    budget.check("max_text_length", sum(len(line.text) for line in lines))
    budget.check(
        "max_text_length", sum(len(char.text) for line in lines for char in line.characters)
    )
    if not all(isfinite(value) and value > 0 for value in (width, height, width * height)):
        raise ValueError("Table detection requires finite, positive page dimensions")

    def check_box(box: BBox) -> None:
        if not (
            all(isfinite(value) for value in (box.x0, box.y0, box.x1, box.y1, box.area))
            and box.width >= 0
            and box.height >= 0
            and all(isfinite(value) for value in box.midpoint)
        ):
            raise ValueError("Table detection requires finite, non-inverted bounding boxes")

    for rect in rects:
        check_box(rect)
    for line in lines:
        check_box(line.bbox)
        for char in line.characters:
            check_box(char.bbox)


def _detect_grid_tables(
    lines: list[TextLine],
    rects: list[BBox],
    page_width: float,
    page_height: float,
    *,
    budget: _WorkBudget,
) -> list[Table]:
    """Use local closed faces, rather than the Cartesian product of all rules."""
    del page_width, page_height  # Geometric tests use the observed drawing bounds.
    edges: list[_Edge] = []
    rectangles: list[BBox] = []
    for rect in rects:
        if rect.height <= _MAX_RULE_THICKNESS and rect.width >= _MIN_RULE_LENGTH:
            edges.append(_Edge(True, (rect.y0 + rect.y1) / 2, rect.x0, rect.x1))
        elif rect.width <= _MAX_RULE_THICKNESS and rect.height >= _MIN_RULE_LENGTH:
            edges.append(_Edge(False, (rect.x0 + rect.x1) / 2, rect.y0, rect.y1))
        elif rect.width > _MIN_RULE_LENGTH and rect.height > _MIN_RULE_LENGTH:
            rectangles.append(rect)

    # Thin rules are stronger evidence than the bounding box of a filled shape.
    # Shading/padding inside a complete ruled region must not invent columns.
    faces = _find_closed_cells(edges, budget)
    ruled_regions = [
        _enclosing_bbox(group)
        for group in _group_adjacent_cells(faces, budget)
        if _is_table_grid(group)
    ]
    for rect in rectangles:
        budget.consume(len(ruled_regions))
        if not any(_contains_with_tolerance(region, rect) for region in ruled_regions):
            edges.extend(_rectangle_edges(rect))
    if rectangles:
        faces = _find_closed_cells(edges, budget)

    tables = []
    for group in _group_adjacent_cells(faces, budget):
        if not _is_table_grid(group):
            continue
        table = _build_grid_table(group, lines, budget)
        if sum(bool(cell.text) for row in table.rows for cell in row.cells) >= 2:
            tables.append(table)
    return tables


def _rectangle_edges(rect: BBox) -> list[_Edge]:
    return [
        _Edge(True, rect.y0, rect.x0, rect.x1),
        _Edge(True, rect.y1, rect.x0, rect.x1),
        _Edge(False, rect.x0, rect.y0, rect.y1),
        _Edge(False, rect.x1, rect.y0, rect.y1),
    ]


def _cluster_positions(values: list[float], tolerance: float) -> list[float]:
    """Snap a bounded cluster, without chaining arbitrarily distant positions."""
    if not values:
        return []
    clusters = [[min(values)]]
    for value in sorted(values)[1:]:
        if value - clusters[-1][0] <= tolerance:
            clusters[-1].append(value)
        else:
            clusters.append([value])
    return [median(cluster) for cluster in clusters]


def _merge_intervals(intervals: list[tuple[float, float]]) -> list[tuple[float, float]]:
    merged: list[tuple[float, float]] = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1] + LINE_ALIGN_TOLERANCE:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged


def _edge_maps(edges: list[_Edge]) -> tuple[dict, dict]:
    """Normalize collinear fragments but retain their actual covered intervals."""
    maps = []
    for horizontal in (True, False):
        selected = [edge for edge in edges if edge.horizontal == horizontal]
        positions = _cluster_positions([edge.position for edge in selected], LINE_ALIGN_TOLERANCE)
        intervals = defaultdict(list)
        for edge in selected:
            # A linear nearest-position search is quadratic for many rules.
            index = bisect_left(positions, edge.position)
            candidates = positions[max(0, index - 1) : index + 1]
            position = min(candidates, key=lambda value: abs(value - edge.position))
            intervals[position].append((edge.start, edge.end))
        maps.append({position: _merge_intervals(parts) for position, parts in intervals.items()})
    return maps[0], maps[1]


def _covers(
    intervals: list[tuple[float, float]], start: float, end: float, budget: _WorkBudget
) -> bool:
    for left, right in intervals:
        budget.consume()
        if left <= start + LINE_ALIGN_TOLERANCE and right >= end - LINE_ALIGN_TOLERANCE:
            return True
    return False


def _find_closed_cells(edges: list[_Edge], budget: _WorkBudget) -> list[BBox]:
    """Find each smallest rectangle with four connected, covered boundaries.

    Missing separators create merged cells naturally. A line outside a table
    cannot extend that table unless it actually closes an adjacent cell.
    """
    horizontal, vertical = _edge_maps(edges)
    if len(horizontal) < 2 or len(vertical) < 2:
        return []
    budget.check("max_grid_intersections", len(horizontal) * len(vertical))
    by_y: dict[float, list[float]] = defaultdict(list)
    by_x: dict[float, list[float]] = defaultdict(list)
    for y, h_parts in horizontal.items():
        for x, v_parts in vertical.items():
            if _covers(h_parts, x, x, budget) and _covers(v_parts, y, y, budget):
                by_y[y].append(x)
                by_x[x].append(y)
    for xs in by_y.values():
        xs.sort()
    for ys in by_x.values():
        ys.sort(reverse=True)
    points_at_y = {y: set(xs) for y, xs in by_y.items()}

    cells = []
    for top in sorted(by_y, reverse=True):
        for left in by_y[top]:
            budget.consume(len(by_y[top]) + len(by_x[left]))
            rights = [x for x in by_y[top] if x - left > LINE_ALIGN_TOLERANCE]
            bottoms = [y for y in by_x[left] if top - y > LINE_ALIGN_TOLERANCE]
            found = False
            for bottom in bottoms:
                if not _covers(vertical[left], bottom, top, budget):
                    break
                for right in rights:
                    if not _covers(horizontal[top], left, right, budget):
                        break
                    if (
                        right in points_at_y[bottom]
                        and _covers(horizontal[bottom], left, right, budget)
                        and _covers(vertical[right], bottom, top, budget)
                    ):
                        budget.check("max_cells", len(cells) + 1)
                        cells.append(BBox(left, bottom, right, top))
                        found = True
                        break
                if found:
                    break
    return cells


def _group_adjacent_cells(cells: list[BBox], budget: _WorkBudget) -> list[list[BBox]]:
    """Connected cells share an edge of positive length, not merely a corner."""
    parents = list(range(len(cells)))
    # Charge before entering the pairwise pass, including disconnected cells.
    budget.consume(len(cells) * (len(cells) - 1) // 2)

    def root(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    for index, cell in enumerate(cells):
        for other_index in range(index):
            other = cells[other_index]
            horizontal_overlap = min(cell.x1, other.x1) - max(cell.x0, other.x0)
            vertical_overlap = min(cell.y1, other.y1) - max(cell.y0, other.y0)
            shares_horizontal = (
                abs(cell.y0 - other.y1) < 0.01 or abs(cell.y1 - other.y0) < 0.01
            ) and horizontal_overlap > LINE_ALIGN_TOLERANCE
            shares_vertical = (
                abs(cell.x0 - other.x1) < 0.01 or abs(cell.x1 - other.x0) < 0.01
            ) and vertical_overlap > LINE_ALIGN_TOLERANCE
            if shares_horizontal or shares_vertical:
                parents[root(index)] = root(other_index)
    groups: dict[int, list[BBox]] = defaultdict(list)
    for index, cell in enumerate(cells):
        groups[root(index)].append(cell)
    return list(groups.values())


def _enclosing_bbox(boxes: list[BBox]) -> BBox:
    return BBox(
        min(box.x0 for box in boxes),
        min(box.y0 for box in boxes),
        max(box.x1 for box in boxes),
        max(box.y1 for box in boxes),
    )


def _is_table_grid(cells: list[BBox]) -> bool:
    xs = {coordinate for cell in cells for coordinate in (cell.x0, cell.x1)}
    ys = {coordinate for cell in cells for coordinate in (cell.y0, cell.y1)}
    return len(xs) >= MIN_TABLE_COLS + 1 and len(ys) >= MIN_TABLE_ROWS + 1


def _contains_with_tolerance(outer: BBox, inner: BBox) -> bool:
    tolerance = LINE_ALIGN_TOLERANCE
    return (
        outer.x0 - tolerance <= inner.x0
        and outer.y0 - tolerance <= inner.y0
        and outer.x1 + tolerance >= inner.x1
        and outer.y1 + tolerance >= inner.y1
    )


def _contains_center(outer: BBox, inner: BBox) -> bool:
    x, y = inner.midpoint
    return outer.x0 <= x <= outer.x1 and outer.y0 <= y <= outer.y1


def _owner(box: BBox, cells: list[TableCell]) -> int | None:
    """Assign each glyph/word/unsplittable line to at most one physical cell."""
    best_index = None
    best_area = 0.0
    best_contains_center = False
    for index, cell in enumerate(cells):
        target = cell.bbox
        area = max(0, min(box.x1, target.x1) - max(box.x0, target.x0)) * max(
            0, min(box.y1, target.y1) - max(box.y0, target.y0)
        )
        contains_center = _contains_center(target, box)
        if area > best_area or (
            area > 0 and area == best_area and contains_center and not best_contains_center
        ):
            best_index, best_area = index, area
            best_contains_center = contains_center
    if best_index is not None:
        target = cells[best_index].bbox
        if best_area >= box.area / 2 or _contains_center(target, box):
            return best_index
    return None


def _character_offsets(line: TextLine) -> list[tuple[int, int]] | None:
    """Map non-space glyphs back to line text, retaining inferred PDF spaces."""
    offsets = []
    cursor = 0
    for char in line.characters:
        while cursor < len(line.text) and line.text[cursor].isspace():
            cursor += 1
        if not line.text.startswith(char.text, cursor):
            return None
        offsets.append((cursor, cursor + len(char.text)))
        cursor += len(char.text)
    return offsets


def _fragment_text(
    line: TextLine, indices: list[int], offsets: list[tuple[int, int]] | None
) -> str:
    parts = []
    previous = None
    for index in indices:
        char = line.characters[index]
        if previous is not None:
            prior = line.characters[previous]
            if offsets is not None and index == previous + 1:
                parts.append(line.text[offsets[previous][1] : offsets[index][0]])
            elif char.bbox.x0 - prior.bbox.x1 > max(1.0, char.font.size * 0.15):
                parts.append(" ")
        parts.append(char.text)
        previous = index
    return "".join(parts).strip()


def _assign_text(
    cells: list[TableCell], lines: list[TextLine], budget: _WorkBudget
) -> dict[int, tuple[int, int]]:
    fragments = defaultdict(list)
    style_counts: dict[int, list[int]] = defaultdict(lambda: [0, 0])
    for line in lines:
        budget.consume(len(cells))
        candidates = [index for index, cell in enumerate(cells) if cell.bbox.overlaps(line.bbox)]
        candidate_cells = [cells[index] for index in candidates]
        if line.characters:
            offsets = _character_offsets(line)
            assigned: dict[int, list[int]] = defaultdict(list)
            for index, char in enumerate(line.characters):
                budget.consume(len(candidates))
                owner = _owner(char.bbox, candidate_cells)
                if owner is not None:
                    cell_index = candidates[owner]
                    assigned[cell_index].append(index)
                    style_counts[cell_index][0] += len(char.text) * bool(char.font.is_bold)
                    style_counts[cell_index][1] += len(char.text)
            for cell_index, indices in assigned.items():
                text = _fragment_text(line, indices, offsets)
                if text:
                    fragments[cell_index].append((-line.bbox.y1, line.bbox.x0, text))
        else:
            budget.consume(len(candidates))
            owner = _owner(line.bbox, candidate_cells)
            if owner is not None and line.text.strip():
                cell_index = candidates[owner]
                fragments[cell_index].append((-line.bbox.y1, line.bbox.x0, line.text.strip()))
                style_counts[cell_index][0] += len(line.text) * bool(line.font.is_bold)
                style_counts[cell_index][1] += len(line.text)
    for index, cell in enumerate(cells):
        cell.text = " ".join(fragment[2] for fragment in sorted(fragments[index]))
    return {index: tuple(counts) for index, counts in style_counts.items()}


def _mark_headers(rows: list[TableRow], bold_fractions: list[float]) -> None:
    """Prefer font contrast; do not silently drop an ordinary first data row."""
    header_count = 0
    last_regular_row = max(
        (index for index, fraction in enumerate(bold_fractions) if fraction < 0.5), default=-1
    )
    for index, fraction in enumerate(bold_fractions[:-1]):
        if fraction >= 0.6 and index < last_regular_row:
            header_count += 1
        else:
            break
    if not header_count and rows:
        first = [cell.text for cell in rows[0].cells if cell.text]
        body = [cell.text for row in rows[1:] for cell in row.cells if cell.text]
        if (
            len(first) >= 2
            and all(not any(char.isdigit() for char in text) for text in first)
            and any(
                text.strip().lstrip("$€£+-").replace(",", "").replace(".", "").isdigit()
                for text in body
            )
        ):
            header_count = 1
    for index, row in enumerate(rows):
        row.is_header = index < header_count
        for cell in row.cells:
            cell.is_header = row.is_header


def _build_grid_table(boxes: list[BBox], lines: list[TextLine], budget: _WorkBudget) -> Table:
    budget.add_cells(len(boxes))
    xs = sorted({coordinate for box in boxes for coordinate in (box.x0, box.x1)})
    ys = sorted({coordinate for box in boxes for coordinate in (box.y0, box.y1)}, reverse=True)
    x_index = {x: index for index, x in enumerate(xs)}
    y_index = {y: index for index, y in enumerate(ys)}
    cells = [
        TableCell(
            text="",
            row_index=y_index[box.y1],
            col_index=x_index[box.x0],
            rowspan=y_index[box.y0] - y_index[box.y1],
            colspan=x_index[box.x1] - x_index[box.x0],
            bbox=box,
        )
        for box in boxes
    ]
    cells.sort(key=lambda cell: (cell.row_index, cell.col_index))
    bbox = _enclosing_bbox(boxes)
    budget.consume(len(lines))
    counts = _assign_text(cells, [line for line in lines if bbox.overlaps(line.bbox)], budget)
    cells_at_row: dict[int, list[int]] = defaultdict(list)
    for index, cell in enumerate(cells):
        cells_at_row[cell.row_index].append(index)
    rows = []
    bold_fractions = []
    for row_index in range(len(ys) - 1):
        indices = cells_at_row[row_index]
        rows.append(
            TableRow(
                cells=[cells[index] for index in indices],
                bbox=BBox(xs[0], ys[row_index + 1], xs[-1], ys[row_index]),
            )
        )
        bold = sum(counts.get(index, (0, 0))[0] for index in indices)
        total = sum(counts.get(index, (0, 0))[1] for index in indices)
        bold_fractions.append(bold / total if total else 0)
    _mark_headers(rows, bold_fractions)
    return Table(rows=rows, bbox=bbox)


@dataclass
class _ColumnSegment:
    text: str
    x_start: float
    x_end: float
    y0: float
    y1: float
    is_bold: bool = False


def _split_line_into_segments(line: TextLine) -> list[_ColumnSegment]:
    if not line.characters:
        return [
            _ColumnSegment(
                line.text,
                line.bbox.x0,
                line.bbox.x1,
                line.bbox.y0,
                line.bbox.y1,
                line.font.is_bold,
            )
        ]
    widths = [char.bbox.width for char in line.characters if char.text.strip()]
    if not widths:
        return []
    gap_threshold = max(4.0, median(widths) * 2.5)
    groups: list[list[int]] = [[]]
    for index in sorted(range(len(line.characters)), key=lambda i: line.characters[i].bbox.x0):
        if groups[-1]:
            previous = line.characters[groups[-1][-1]]
            if line.characters[index].bbox.x0 - previous.bbox.x1 > gap_threshold:
                groups.append([])
        groups[-1].append(index)
    segments = []
    offsets = _character_offsets(line)
    for indices in groups:
        chars: list[Character] = [line.characters[index] for index in indices]
        text = _fragment_text(line, indices, offsets)
        if text:
            segments.append(
                _ColumnSegment(
                    text,
                    min(char.bbox.x0 for char in chars),
                    max(char.bbox.x1 for char in chars),
                    min(char.bbox.y0 for char in chars),
                    max(char.bbox.y1 for char in chars),
                    sum(char.font.is_bold for char in chars) > len(chars) / 2,
                )
            )
    return segments


def _detect_whitespace_tables(
    lines: list[TextLine], page_width: float, *, budget: _WorkBudget
) -> list[Table]:
    """Retain aligned-column fallback, rejecting wide, parallel prose blocks."""
    bands: list[list[_ColumnSegment]] = []
    for line in sorted(lines, key=lambda item: (-item.bbox.y1, item.bbox.x0)):
        budget.consume(max(1, len(line.characters)))
        segments = _split_line_into_segments(line)
        if not segments:
            continue
        midpoint = (line.bbox.y0 + line.bbox.y1) / 2
        if bands and abs(midpoint - (bands[-1][0].y0 + bands[-1][0].y1) / 2) <= 2.0:
            bands[-1].extend(segments)
        else:
            bands.append(segments)
    for band in bands:
        band.sort(key=lambda segment: segment.x_start)

    tables = []
    pending: list[list[_ColumnSegment]] = []

    def flush() -> None:
        if len(pending) >= MIN_TABLE_ROWS:
            budget.check("max_cells", sum(len(row) for row in pending))
            table = _build_whitespace_table(pending, budget)
            if table is not None:
                tables.append(table)

    for segments in bands:
        positions = [segment.x_start for segment in segments]
        aligned = pending and _columns_align(
            [segment.x_start for segment in pending[-1]], positions, page_width * 0.02
        )
        nearby = pending and (
            pending[-1][0].y0 - segments[0].y1 <= max(24.0, 2 * (segments[0].y1 - segments[0].y0))
        )
        if len(segments) < MIN_TABLE_COLS or not (aligned and nearby):
            flush()
            pending = []
        if len(segments) >= MIN_TABLE_COLS:
            pending.append(segments)
    flush()
    return tables


def _columns_align(cols_a: list[float], cols_b: list[float], tolerance: float) -> bool:
    return len(cols_a) == len(cols_b) and all(
        abs(left - right) <= tolerance for left, right in zip(cols_a, cols_b)
    )


def _build_whitespace_table(
    all_segments: list[list[_ColumnSegment]], budget: _WorkBudget
) -> Table | None:
    if len(all_segments) < MIN_TABLE_ROWS:
        return None
    # Compact codes/labels/values plus descriptions are common in tables;
    # two columns of long aligned prose alone are not sufficient evidence.
    body = all_segments[1:] if all(segment.is_bold for segment in all_segments[0]) else all_segments
    if not any(
        sum(len(row[index].text.split()) <= 4 and len(row[index].text) <= 32 for row in body)
        >= len(body) * 0.75
        for index in range(len(all_segments[0]))
    ):
        return None
    budget.add_cells(sum(len(row) for row in all_segments))
    rows = []
    for row_index, segments in enumerate(all_segments):
        cells = [
            TableCell(
                text=segment.text,
                row_index=row_index,
                col_index=col_index,
                bbox=BBox(segment.x_start, segment.y0, segment.x_end, segment.y1),
            )
            for col_index, segment in enumerate(segments)
        ]
        rows.append(TableRow(cells=cells, bbox=_enclosing_bbox([cell.bbox for cell in cells])))
    _mark_headers(
        rows,
        [sum(segment.is_bold for segment in row) / len(row) for row in all_segments],
    )
    return Table(rows=rows, bbox=_enclosing_bbox([row.bbox for row in rows]))
