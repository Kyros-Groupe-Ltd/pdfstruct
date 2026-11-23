"""Synthetic geometry regressions; no customer documents are test fixtures."""

from collections import Counter
from io import BytesIO
from itertools import pairwise

import pytest
from reportlab.pdfgen import canvas

from pdfstruct.extractors.text import extract_pages_raw
from pdfstruct.models.document import BBox, Character, FontInfo, TextLine
from pdfstruct.tables import detector
from pdfstruct.tables.detector import TableDetectionLimitError, TableDetectionLimits, detect_tables


def _pdf(draw):
    output = BytesIO()
    page = canvas.Canvas(output, pagesize=(612, 792))
    draw(page)
    page.save()
    return output.getvalue()


def _tables(pdf, *, char_margin=2.0):
    raw = next(extract_pages_raw(pdf, char_margin=char_margin))
    return detect_tables(raw.lines, raw.rects, raw.width, raw.height)


def _draw_grid(page, xs, top, data, *, row_height=22, header=True, fragments=False):
    bottom = top - len(data) * row_height
    if fragments:
        # Word-like nested header fills and padding: these are not cell edges.
        for left, right in pairwise(xs):
            page.setFillGray(0.9)
            page.rect(
                left + 0.3,
                top - row_height + 0.3,
                right - left - 0.6,
                row_height - 0.6,
                fill=1,
                stroke=0,
            )
            page.rect(
                left + 5, top - row_height + 3, right - left - 10, row_height - 6, fill=1, stroke=0
            )
            page.rect(left, top - row_height, right - left, 2.52, fill=1, stroke=0)
    page.setFillGray(0)
    page.setStrokeGray(0)
    for index in range(len(data) + 1):
        y = top - index * row_height
        if fragments:
            page.rect(xs[0], y - 0.24, xs[-1] - xs[0], 0.48, fill=1, stroke=0)
        else:
            page.line(xs[0], y, xs[-1], y)
    for x in xs:
        if fragments:
            for index in range(len(data)):
                y = top - index * row_height
                page.rect(x - 0.24, y - row_height, 0.48, row_height - 2.52, fill=1, stroke=0)
                page.rect(x - 0.24, y - 2.52, 0.48, 2.52, fill=1, stroke=0)
        else:
            page.line(x, bottom, x, top)
    for row_index, row in enumerate(data):
        page.setFont("Helvetica-Bold" if header and row_index == 0 else "Helvetica", 8)
        for col_index, text in enumerate(row):
            for line_index, part in enumerate(text.split("\n")):
                page.drawString(
                    xs[col_index] + 4, top - row_index * row_height - 12 - 9 * line_index, part
                )


def test_five_independent_tables_with_nested_fills_and_fragmented_rules():
    sizes = [(2, 3), (4, 3), (4, 3), (7, 2), (4, 3)]
    expected = []

    def draw(page):
        top = 746
        for table_index, (rows, cols) in enumerate(sizes):
            page.setFont("Helvetica-Bold", 11)
            page.drawString(74, top + 8, f"Section {table_index + 1}")
            data = [(["Code", "Description", "Level"] if cols == 3 else ["Parameter", "Value"])]
            for row in range(1, rows):
                values = [f"T{table_index}R{row}", f"Donnée {table_index}-{row}"]
                if cols == 3:
                    values.append("O")
                data.append(values)
            expected.append(data)
            xs = [74, 124, 474, 538] if cols == 3 else [74, 244, 538]
            _draw_grid(page, xs, top, data, fragments=True)
            top -= rows * 22 + 26

    tables = _tables(_pdf(draw))
    assert [(table.num_rows, table.num_cols) for table in tables] == sizes
    assert [table.to_list() for table in tables] == expected
    assert all(table.header_row is table.rows[0] for table in tables)
    assert all(cell.colspan == 1 for table in tables for row in table.rows for cell in row.cells)


def test_side_by_side_grids_do_not_become_a_single_global_grid():
    def draw(page):
        _draw_grid(page, [40, 110, 210], 700, [["Key", "Value"], ["Left", "10"]])
        _draw_grid(page, [350, 420, 550], 700, [["Key", "Value"], ["Right", "20"]])

    tables = _tables(_pdf(draw))
    assert len(tables) == 2
    assert [table.to_list()[1] for table in tables] == [["Left", "10"], ["Right", "20"]]


def test_glyphs_split_a_pdfminer_line_without_repeating_text_or_losing_spaces():
    data = [["Code", "Description", "Kind"], ["A1", "Green lake", "O"], ["B2", "Blue forest", "S"]]
    pdf = _pdf(lambda page: _draw_grid(page, [60, 110, 230, 280], 700, data))
    raw = next(extract_pages_raw(pdf, char_margin=100))
    assert any(line.bbox.width > 180 for line in raw.lines), "Fixture must span multiple cells"
    tables = detect_tables(raw.lines, raw.rects, raw.width, raw.height)
    assert len(tables) == 1
    assert tables[0].to_list() == data
    assert Counter(" ".join(cell.text for row in tables[0].rows for cell in row.cells).split()) == (
        Counter(" ".join(text for row in data for text in row).split())
    )


def test_multiline_cell_preserves_all_lines_once():
    data = [
        ["Code", "Description"],
        ["A1", "First sentence\ncontinues here"],
        ["B2", "Other description"],
    ]
    tables = _tables(_pdf(lambda page: _draw_grid(page, [60, 120, 400], 700, data, row_height=32)))
    assert len(tables) == 1
    assert tables[0].to_list()[1] == ["A1", "First sentence continues here"]


def test_merged_header_is_one_cell_with_colspan_and_two_header_rows():
    def draw(page):
        for y in (700, 680, 660, 640):
            page.line(60, y, 300, y)
        for x in (60, 300):
            page.line(x, 640, x, 700)
        page.line(180, 640, 180, 680)
        page.setFont("Helvetica-Bold", 9)
        page.drawString(65, 687, "Combined heading")
        page.drawString(65, 667, "Left label")
        page.drawString(185, 667, "Right label")
        page.setFont("Helvetica", 9)
        page.drawString(65, 647, "Alpha")
        page.drawString(185, 647, "Beta")

    (table,) = _tables(_pdf(draw))
    assert table.num_cols == 2
    assert len(table.rows[0].cells) == 1
    assert table.rows[0].cells[0].text == "Combined heading"
    assert table.rows[0].cells[0].colspan == 2
    assert [row.is_header for row in table.rows] == [True, True, False]
    assert [cell.text for cell in table.rows[2].cells] == ["Alpha", "Beta"]


def test_rowspan_records_text_only_at_its_anchor():
    def draw(page):
        for y in (700, 680, 640):
            page.line(60, y, 300, y)
        page.line(180, 660, 300, 660)
        for x in (60, 180, 300):
            page.line(x, 640, x, 700)
        page.setFont("Helvetica-Bold", 9)
        page.drawString(65, 687, "Group")
        page.drawString(185, 687, "Value")
        page.setFont("Helvetica", 9)
        page.drawString(65, 657, "Shared")
        page.drawString(185, 667, "Alpha")
        page.drawString(185, 647, "Beta")

    (table,) = _tables(_pdf(draw))
    assert table.num_rows == 3
    merged = table.rows[1].cells[0]
    assert (merged.text, merged.rowspan, merged.col_index) == ("Shared", 2, 0)
    assert [(cell.col_index, cell.text) for cell in table.rows[2].cells] == [(1, "Beta")]


def test_rectangular_cell_borders_without_separate_line_objects():
    def draw(page):
        page.setFont("Helvetica", 9)
        for row in range(2):
            for col in range(2):
                x, y = 60 + col * 100, 680 - row * 22
                page.rect(x, y, 100, 22, fill=0, stroke=1)
                page.drawString(x + 5, y + 7, f"R{row}C{col}")

    (table,) = _tables(_pdf(draw))
    assert table.to_list() == [["R0C0", "R0C1"], ["R1C0", "R1C1"]]
    assert table.header_row is None


def test_first_data_row_is_not_automatically_a_header():
    data = [["A1", "10"], ["B2", "20"]]
    (table,) = _tables(_pdf(lambda page: _draw_grid(page, [60, 160, 260], 700, data, header=False)))
    assert table.header_row is None
    assert table.to_list() == data


def test_plain_label_header_above_numbers_is_recognized():
    data = [["Name", "Amount"], ["Alpha", "10"], ["Beta", "20"]]
    (table,) = _tables(_pdf(lambda page: _draw_grid(page, [60, 160, 260], 700, data, header=False)))
    assert table.header_row is table.rows[0]


def test_unsplittable_line_is_owned_once_instead_of_copied_to_every_cell():
    pdf = _pdf(
        lambda page: _draw_grid(page, [60, 160, 260, 360], 700, [["A", "B", "C"], ["D", "E", "F"]])
    )
    raw = next(extract_pages_raw(pdf))
    line = TextLine("Whole row", BBox(65, 683, 355, 693), FontInfo("Helvetica", 10))
    body = [text for text in raw.lines if text.bbox.y1 < 678]
    tables = detect_tables([line] + body, raw.rects, raw.width, raw.height)
    assert len(tables) == 1
    assert sum(cell.text.count("Whole row") for row in tables[0].rows for cell in row.cells) == 1


def test_borderless_table_fallback_keeps_spaces_and_separate_text_lines():
    data = [["Name", "Amount"], ["Green lake", "10"], ["Blue forest", "20"]]

    def draw(page):
        for row, values in enumerate(data):
            page.setFont("Helvetica-Bold" if row == 0 else "Helvetica", 9)
            page.drawString(60, 700 - row * 18, values[0])
            page.drawString(300, 700 - row * 18, values[1])

    (table,) = _tables(_pdf(draw))
    assert table.to_list() == data
    assert table.header_row is table.rows[0]


@pytest.mark.parametrize("char_margin", [2.0, 100.0])
def test_parallel_prose_columns_are_not_a_whitespace_table(char_margin):
    def draw(page):
        page.setFont("Helvetica", 8)
        for row in range(5):
            page.drawString(
                50, 700 - row * 12, "This paragraph describes a journey through the forest."
            )
            page.drawString(
                330, 700 - row * 12, "Another paragraph explains the history of this area."
            )

    assert _tables(_pdf(draw), char_margin=char_margin) == []


def test_unconnected_rules_do_not_form_phantom_grid():
    def draw(page):
        for y in (700, 680, 660):
            page.line(60, y, 200, y)
        for x in (300, 400, 500):
            page.line(x, 600, x, 750)
        page.setFont("Helvetica", 9)
        page.drawString(65, 687, "A standalone line of text")

    assert _tables(_pdf(draw)) == []


def _plain_line(text="Content", x=60, y=680):
    return TextLine(text, BBox(x, y, x + len(text) * 5, y + 10), FontInfo("Helvetica", 10))


def test_excessive_drawing_count_is_rejected_before_geometry_allocation(monkeypatch):
    def should_not_reach_geometry(*args, **kwargs):
        pytest.fail("Oversized input reached the geometry allocator")

    monkeypatch.setattr(detector, "_edge_maps", should_not_reach_geometry)
    rects = [BBox(20, 20, 100, 20)] * 10_001
    with pytest.raises(TableDetectionLimitError) as error:
        detect_tables([_plain_line()], rects, 612, 792)
    assert error.value.limit_name == "max_rectangles"
    assert error.value.observed == 10_001


def test_axis_explosion_is_rejected_before_intersection_work(monkeypatch):
    def should_not_reach_intersections(*args, **kwargs):
        pytest.fail("Oversized axis product entered intersection scanning")

    monkeypatch.setattr(detector, "_covers", should_not_reach_intersections)
    rules = [BBox(0, index * 4, 1400, index * 4) for index in range(350)]
    rules += [BBox(index * 4, 0, index * 4, 1400) for index in range(350)]
    with pytest.raises(TableDetectionLimitError) as error:
        detect_tables([_plain_line()], rules, 1500, 1500)
    assert error.value.limit_name == "max_grid_intersections"
    assert error.value.observed == 350 * 350


def test_large_grid_stops_before_unbounded_cell_adjacency_work():
    # This stays below the axis limit, but 2,500 cells would require millions
    # of adjacency comparisons. Exhaustion is explicit, not partial success.
    rules = [BBox(0, index * 10, 500, index * 10) for index in range(51)]
    rules += [BBox(index * 10, 0, index * 10, 500) for index in range(51)]
    with pytest.raises(TableDetectionLimitError) as error:
        detect_tables([_plain_line(y=100)], rules, 612, 792)
    assert error.value.limit_name == "max_work"


def test_work_and_cell_limits_are_configurable_and_do_not_mutate_input():
    pdf = _pdf(
        lambda page: _draw_grid(page, [60, 160, 260], 700, [["Name", "Value"], ["Alpha", "10"]])
    )
    raw = next(extract_pages_raw(pdf))
    original = [line.text for line in raw.lines]
    with pytest.raises(TableDetectionLimitError, match="max_work"):
        detect_tables(
            raw.lines, raw.rects, raw.width, raw.height, limits=TableDetectionLimits(max_work=1)
        )
    with pytest.raises(TableDetectionLimitError, match="max_cells"):
        detect_tables(
            raw.lines, raw.rects, raw.width, raw.height, limits=TableDetectionLimits(max_cells=3)
        )
    assert [line.text for line in raw.lines] == original
    assert len(detect_tables(raw.lines, raw.rects, raw.width, raw.height)) == 1


def test_borderless_cell_limit_applies_across_multiple_tables():
    def draw(page):
        for top in (700, 500):
            page.setFont("Helvetica", 9)
            for row in range(2):
                page.drawString(60, top - row * 18, f"Name {row}")
                page.drawString(300, top - row * 18, f"Value {row}")

    raw = next(extract_pages_raw(_pdf(draw)))
    with pytest.raises(TableDetectionLimitError, match="max_cells"):
        detect_tables(
            raw.lines, raw.rects, raw.width, raw.height, limits=TableDetectionLimits(max_cells=5)
        )
    assert len(detect_tables(raw.lines, raw.rects, raw.width, raw.height)) == 2


@pytest.mark.parametrize("value", [0, -1, True, 1.5, float("inf")])
def test_limit_values_require_positive_integers(value):
    with pytest.raises(ValueError, match="positive integer"):
        TableDetectionLimits(max_work=value)


@pytest.mark.parametrize(
    "bbox", [BBox(0, 0, float("nan"), 1), BBox(0, 0, float("inf"), 1), BBox(10, 0, 5, 20)]
)
def test_invalid_rule_geometry_fails_explicitly(bbox):
    with pytest.raises(ValueError, match="bounding boxes"):
        detect_tables([_plain_line()], [bbox], 612, 792)


@pytest.mark.parametrize("width,height", [(float("nan"), 100), (100, float("inf")), (0, 100)])
def test_invalid_page_dimensions_fail_explicitly(width, height):
    with pytest.raises(ValueError, match="page dimensions"):
        detect_tables([], [], width, height)


def test_character_and_text_caps_apply_without_any_grid():
    font = FontInfo("Helvetica", 10)
    chars = [Character("A", BBox(index * 10, 10, index * 10 + 5, 20), font) for index in range(10)]
    line = TextLine("A" * 10, BBox(0, 10, 100, 20), font, chars)
    with pytest.raises(TableDetectionLimitError, match="max_characters"):
        detect_tables([line], [], 612, 792, limits=TableDetectionLimits(max_characters=5))
    with pytest.raises(TableDetectionLimitError, match="max_text_length"):
        detect_tables([line], [], 612, 792, limits=TableDetectionLimits(max_text_length=5))


def test_many_cell_fragments_map_original_line_offsets_only_once(monkeypatch):
    original = detector._character_offsets
    calls = []

    def counted_offsets(line):
        calls.append(line)
        return original(line)

    monkeypatch.setattr(detector, "_character_offsets", counted_offsets)
    font = FontInfo("Helvetica", 10)
    lines = []
    for y in (100, 80):
        chars = [
            Character("A", BBox(10 + index * 20, y, 15 + index * 20, y + 10), font)
            for index in range(500)
        ]
        lines.append(TextLine("A" * 500, BBox(10, y, 9995, y + 10), font, chars))
    (table,) = detect_tables(lines, [], 12000, 200)
    assert (table.num_rows, table.num_cols) == (2, 500)
    assert len(calls) == 2
