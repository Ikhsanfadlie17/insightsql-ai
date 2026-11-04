"""Task 3: conservative type inference that never corrupts IDs or dates."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from backend.services.type_inference import infer_types


def _infer(values: list[str | None]):
    frame = pd.DataFrame({"c": pd.Series(values, dtype="string")})
    result = infer_types(frame)
    return result.dataframe["c"], result.types["c"], result.failed_counts.get("c", 0)


def test_leading_zero_ids_stay_text() -> None:
    series, logical, _ = _infer(["00123", "00456", "01000"])

    assert logical == "text"
    assert list(series) == ["00123", "00456", "01000"]


def test_integers() -> None:
    series, logical, _ = _infer(["1", "2", "30"])

    assert logical == "integer"
    assert list(series) == [1, 2, 30]


def test_excel_style_integral_floats_are_integers() -> None:
    _, logical, _ = _infer(["1001.0", "1002.0"])

    assert logical == "integer"


def test_decimals() -> None:
    series, logical, _ = _infer(["1.5", "2"])

    assert logical == "numeric"
    assert list(series) == [1.5, 2.0]


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        (["1.234,5", "10.000,25"], [1234.5, 10000.25]),  # Indonesian
        (["1,234.5", "10,000.25"], [1234.5, 10000.25]),  # English
        (["1.000.000", "250.000"], [1_000_000, 250_000]),  # dot thousands only
        (["Rp 1.500.000", "Rp 200.000"], [1_500_000, 200_000]),  # currency prefix
    ],
)
def test_localized_numbers(values: list[str], expected: list[float]) -> None:
    series, logical, _ = _infer(values)

    assert logical in {"integer", "numeric"}
    assert list(series) == expected


def test_day_first_dates_when_day_exceeds_12() -> None:
    series, logical, _ = _infer(["15/01/2026", "03/02/2026"])

    assert logical == "date"
    assert list(series.dt.date) == [date(2026, 1, 15), date(2026, 2, 3)]


def test_month_first_dates_when_second_part_exceeds_12() -> None:
    series, logical, _ = _infer(["01/15/2026", "02/03/2026"])

    assert logical == "date"
    assert list(series.dt.date) == [date(2026, 1, 15), date(2026, 2, 3)]


def test_ambiguous_slash_dates_default_to_day_first() -> None:
    series, logical, _ = _infer(["03/04/2026", "05/06/2026"])

    assert logical == "date"
    assert list(series.dt.date) == [date(2026, 4, 3), date(2026, 6, 5)]


def test_iso_dates_and_excel_midnight_timestamps() -> None:
    iso, iso_type, _ = _infer(["2026-01-15", "2025-12-31"])
    excel, excel_type, _ = _infer(["2023-01-03 00:00:00", "2023-02-04 00:00:00"])

    assert iso_type == "date" and excel_type == "date"
    assert list(iso.dt.date) == [date(2026, 1, 15), date(2025, 12, 31)]
    assert list(excel.dt.date) == [date(2023, 1, 3), date(2023, 2, 4)]


def test_datetimes_with_time_component() -> None:
    _, logical, _ = _infer(["2026-01-15 08:30:00", "2026-01-16 17:00:00"])

    assert logical == "datetime"


def test_mostly_unparseable_dates_stay_text() -> None:
    values = ["15/01/2026", "besok", "TBD", "03/02/2026"]
    series, logical, _ = _infer(values)

    assert logical == "text"
    assert list(series) == values


def test_few_bad_values_are_counted_not_hidden() -> None:
    values = [str(i) for i in range(1, 40)] + ["n/a?"]
    series, logical, failed = _infer(values)

    assert logical == "integer"
    assert failed == 1
    assert series.isna().sum() == 1


def test_text_is_trimmed_and_blank_becomes_missing() -> None:
    series, logical, _ = _infer([" A001 ", "A001", "   ", None])

    assert logical == "text"
    assert list(series[:2]) == ["A001", "A001"]
    assert series[2:].isna().all()


def test_all_missing_column_is_text() -> None:
    _, logical, _ = _infer([None, None])

    assert logical == "text"


def test_booleans() -> None:
    series, logical, _ = _infer(["true", "False", "TRUE"])

    assert logical == "boolean"
    assert list(series) == [True, False, True]


def test_consistent_three_digit_dot_groups_are_thousands() -> None:
    """Documented assumption (Indonesian format): 1.234 means one thousand two hundred."""
    series, logical, _ = _infer(["1.234", "5.678"])

    assert logical == "integer"
    assert list(series) == [1234, 5678]


def test_short_decimals_keep_dot_as_decimal_separator() -> None:
    series, _, _ = _infer(["1.234", "2.5"])

    assert list(series) == [1.234, 2.5]
