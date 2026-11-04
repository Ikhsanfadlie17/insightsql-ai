"""Task 16: rule-based chart selection from real query results; LLM hints are advisory."""

from __future__ import annotations

from backend.schemas.charts import ChartSpec
from backend.schemas.llm_outputs import ChartHint
from backend.schemas.query import QueryResult
from backend.services.visualization_service import (
    infer_result_types,
    recommend_chart,
    validate_chart_spec,
)


def _result(columns: list[str], rows: list[list]) -> QueryResult:
    return QueryResult(columns=columns, rows=rows, row_count=len(rows))


def _cats(n: int, prefix: str = "Dept") -> QueryResult:
    return _result(["departemen", "jumlah"], [[f"{prefix} {i}", i + 1] for i in range(n)])


def test_types_are_inferred_from_values() -> None:
    result = _result(
        ["bulan", "hari", "kategori", "n", "rata", "kosong"],
        [
            ["2026-01", "2026-01-05", "A", 3, 1.5, None],
            ["2026-02", "2026-02-05", "B", 4, 2.0, None],
        ],
    )

    assert infer_result_types(result) == {
        "bulan": "period", "hari": "date", "kategori": "text", "n": "number",
        "rata": "number", "kosong": "empty",
    }  # fmt: skip


def test_single_row_numbers_are_kpis() -> None:
    spec = recommend_chart(_result(["total", "rata_rata"], [[120, 4.5]]))

    assert spec is not None and spec.type == "kpi" and spec.y == ["total", "rata_rata"]


def test_time_series_is_a_line() -> None:
    result = _result(["bulan", "klien_aktif"], [["2026-01", 4], ["2026-02", 6], ["2026-03", 5]])

    spec = recommend_chart(result)

    assert spec is not None and (spec.type, spec.x, spec.y) == ("line", "bulan", ["klien_aktif"])


def test_year_numbers_are_a_time_axis() -> None:
    result = _result(["tahun", "klien"], [[2024, 50], [2025, 60], [2026, 70]])

    spec = recommend_chart(result)

    assert spec is not None and spec.type == "line" and spec.x == "tahun"


def test_few_categories_with_a_donut_hint() -> None:
    hint = ChartHint(type="donut", x="departemen", y=["jumlah"])

    spec = recommend_chart(_cats(5), hint=hint)

    assert spec is not None and spec.type == "donut"


def test_donut_hint_is_ignored_for_too_many_or_negative_slices() -> None:
    hint = ChartHint(type="donut", x="departemen", y=["jumlah"])
    negative = _result(["departemen", "jumlah"], [["A", 5], ["B", -2]])

    assert recommend_chart(_cats(8), hint=hint).type == "bar"
    assert recommend_chart(negative, hint=hint).type == "bar"


def test_up_to_twelve_categories_are_bars() -> None:
    assert recommend_chart(_cats(12)).type == "bar"


def test_many_or_long_categories_are_horizontal_top_n() -> None:
    many = recommend_chart(_cats(30))
    long = recommend_chart(_cats(3, prefix="Departemen Konsultasi Bisnis dan Pajak"))

    assert many.type == "hbar" and many.top_n == 20
    assert long.type == "hbar"


def test_one_category_and_several_numbers_is_grouped() -> None:
    result = _result(["dept", "periode_lalu", "periode_ini"], [["A", 1, 2], ["B", 3, 4]])

    spec = recommend_chart(result)

    assert spec.type == "grouped_bar" and spec.y == ["periode_lalu", "periode_ini"]


def test_hint_with_a_missing_column_is_ignored() -> None:
    hint = ChartHint(type="line", x="tidak_ada", y=["jumlah"])

    spec = recommend_chart(_cats(4), hint=hint)

    assert spec.type == "bar" and spec.x == "departemen"


def test_line_hint_needs_a_time_axis() -> None:
    hint = ChartHint(type="line", x="departemen", y=["jumlah"])

    assert recommend_chart(_cats(4), hint=hint).type == "bar"


def test_non_numeric_values_get_no_chart() -> None:
    result = _result(["klien", "nama"], [["K1", "PT A"], ["K2", "PT B"]])

    assert recommend_chart(result) is None


def test_empty_or_single_value_results_get_no_chart() -> None:
    assert recommend_chart(_result(["a", "b"], [])) is None
    assert recommend_chart(_result(["dept", "n"], [["A", 3]])) is None  # one bar says nothing


def test_table_hint_means_no_chart() -> None:
    assert recommend_chart(_cats(4), hint=ChartHint(type="table", x=None, y=[])) is None


def test_validate_rejects_specs_that_do_not_match_the_result() -> None:
    result = _cats(4)

    assert validate_chart_spec(ChartSpec(type="bar", x="departemen", y=["jumlah"]), result)
    assert not validate_chart_spec(ChartSpec(type="bar", x="departemen", y=["x"]), result)
    assert not validate_chart_spec(ChartSpec(type="bar", x="jumlah", y=["departemen"]), result)
    assert not validate_chart_spec(ChartSpec(type="line", x="departemen", y=["jumlah"]), result)
    assert not validate_chart_spec(ChartSpec(type="kpi", x=None, y=["jumlah"]), result)
    assert not validate_chart_spec(ChartSpec(type="bar", x="departemen", y=[]), result)


def test_too_many_series_are_capped() -> None:
    columns = ["bulan", *[f"m{i}" for i in range(6)]]
    rows = [[f"2026-0{m}", *range(6)] for m in range(1, 4)]

    spec = recommend_chart(_result(columns, rows))

    assert spec.type == "line" and len(spec.y) == 4


def test_long_format_time_series_is_not_a_line() -> None:
    """Review M5: one row per month and department would zig-zag in one line."""
    result = _result(
        ["bulan", "departemen", "klien"],
        [["2026-01", "A", 5], ["2026-01", "B", 9], ["2026-02", "A", 6], ["2026-02", "B", 3]],
    )

    spec = recommend_chart(result)

    assert spec is None or spec.type != "line"
    assert not validate_chart_spec(ChartSpec(type="line", x="bulan", y=["klien"]), result)


def test_unsorted_time_series_is_still_a_line() -> None:
    result = _result(["bulan", "n"], [["2026-03", 1], ["2026-01", 2], ["2026-02", 3]])

    assert recommend_chart(result).type == "line"
