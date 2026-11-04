"""Task 16: frontend chart rendering re-validates specs and formats numbers in Indonesian."""

from __future__ import annotations

import pytest

from frontend.components.charts import SERIES_COLORS, build_figure, chart_problem
from frontend.components.formatting import (
    fmt_int,
    fmt_number,
    fmt_pct,
    fmt_signed_pct,
    fmt_value,
)

COLUMNS = ["departemen", "jumlah"]
ROWS = [["Audit", 1200], ["Pajak", 800], ["TI", 300]]


@pytest.mark.parametrize(
    ("value", "expected"),
    [(0, "0"), (1234567, "1.234.567"), (None, "N/A"), (-4500, "-4.500"), (12.6, "13")],
)
def test_fmt_int(value, expected) -> None:
    assert fmt_int(value) == expected


def test_fmt_number_and_percentages() -> None:
    assert fmt_number(1234.5, 1) == "1.234,5"
    assert fmt_pct(0.632183) == "63,2%"
    assert fmt_pct(1.0, 0) == "100%"
    assert fmt_pct(None) == "N/A"
    assert fmt_signed_pct(0.184) == "+18,4%"
    assert fmt_signed_pct(-0.05) == "−5,0%"
    assert fmt_signed_pct(0.0) == "0,0%"
    assert fmt_value(2500) == "2.500" and fmt_value("KL-01") == "KL-01"


@pytest.mark.parametrize(
    "spec",
    [
        None,
        {"type": "radar", "x": "departemen", "y": ["jumlah"]},
        {"type": "bar", "x": "missing", "y": ["jumlah"]},
        {"type": "bar", "x": "departemen", "y": ["departemen"]},
        {"type": "bar", "x": "departemen", "y": []},
    ],
)
def test_invalid_specs_are_reported(spec) -> None:
    assert chart_problem(spec, COLUMNS, ROWS) is not None


def test_share_chart_needs_positive_values() -> None:
    spec = {"type": "donut", "x": "departemen", "y": ["jumlah"]}
    assert chart_problem(spec, COLUMNS, [["A", 0], ["B", 0]]) is not None
    assert chart_problem(spec, COLUMNS, ROWS) is None


def test_bar_figure_uses_indonesian_separators_and_real_values() -> None:
    spec = {"type": "bar", "x": "departemen", "y": ["jumlah"], "title": "Klien"}

    fig = build_figure(spec, COLUMNS, ROWS, labels={"jumlah": "Clients"})

    assert fig.layout.separators == ",."
    assert list(fig.data[0].y) == [1200, 800, 300]
    assert "Clients" in fig.data[0].hovertemplate
    assert fig.layout.showlegend is False  # one series: the title names it


def test_hbar_keeps_top_n_largest() -> None:
    rows = [[f"D{i}", i] for i in range(30)]
    spec = {"type": "hbar", "x": "departemen", "y": ["jumlah"], "top_n": 20}

    fig = build_figure(spec, COLUMNS, rows)

    assert len(fig.data[0].y) == 20 and fig.data[0].y[-1] == "D29"


def test_series_colors_follow_fixed_order_and_category_colors_stick() -> None:
    grouped = {"type": "grouped_bar", "x": "d", "y": ["a", "b"]}
    fig = build_figure(grouped, ["d", "a", "b"], [["x", 1, 2], ["y", 3, 4]])
    assert [t.marker.color for t in fig.data] == list(SERIES_COLORS[:2])
    assert fig.layout.showlegend is True

    donut = {"type": "donut", "x": "departemen", "y": ["jumlah"]}
    pie = build_figure(donut, COLUMNS, ROWS, colors={"Pajak": "#123456"})
    assert pie.data[0].marker.colors[1] == "#123456"


def test_line_figure() -> None:
    spec = {"type": "line", "x": "bulan", "y": ["aktif"]}

    fig = build_figure(spec, ["bulan", "aktif"], [["2026-01", 3], ["2026-02", None]])

    assert list(fig.data[0].x) == ["2026-01", "2026-02"]
    assert fig.layout.xaxis.type == "category"
