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


# --- Phase 2 review regressions -------------------------------------------------


def test_long_integer_strings_are_ids_not_lossy_numbers() -> None:
    """H1: 16+ digit values (e.g. NIK) would lose precision as float64."""
    values = ["9171234567890123", "9171234567890125", "12345678901234567890"]
    series, logical, failed = _infer(values)

    assert logical == "text"
    assert list(series) == values
    assert failed == 0


def test_integers_are_converted_exactly() -> None:
    series, logical, _ = _infer(["900719925474099", "1"])

    assert logical == "integer"
    assert list(series) == [900719925474099, 1]


def test_leading_zero_decimals_are_not_thousands() -> None:
    """H2: "0.125" can never be Indonesian thousands."""
    series, logical, _ = _infer(["0.125", "0.250", "0.375"])

    assert logical == "numeric"
    assert list(series) == [0.125, 0.25, 0.375]


def test_excel_numbers_skip_locale_heuristics() -> None:
    """H2: native Excel numbers arrive as "4.125" and are never thousands-grouped."""
    frame = pd.DataFrame({"c": pd.Series(["4.125", "3.875", "5"], dtype="string")})

    result = infer_types(frame, number_format="plain")

    assert result.types["c"] == "numeric"
    assert list(result.dataframe["c"]) == [4.125, 3.875, 5.0]


def test_mixed_number_styles_are_not_silently_rewritten() -> None:
    """M5: "1.5" must not become 15 when the column is read as Indonesian format."""
    series, logical, _ = _infer(["1.5", "2,5", "3"])

    assert logical == "text"
    assert list(series) == ["1.5", "2,5", "3"]


def test_minority_style_values_are_counted_as_failures() -> None:
    values = [f"{i},5" for i in range(1, 40)] + ["1.5"]
    series, logical, failed = _infer(values)

    assert logical == "numeric"
    assert failed == 1
    assert series.iloc[-1] is pd.NA or pd.isna(series.iloc[-1])
    assert series.iloc[0] == 1.5


# --- Phase 3: explicit formats and user-facing notes -----------------------------


def _infer_full(values: list[str | None], **kwargs):
    frame = pd.DataFrame({"c": pd.Series(values, dtype="string")})
    result = infer_types(frame, **kwargs)
    return result.dataframe["c"], result.types["c"], result.notes.get("c", [])


def _codes(notes) -> set[str]:
    return {n.code for n in notes}


@pytest.mark.parametrize(
    ("number_format", "values", "expected"),
    [
        ("id", ["1.234", "5.678"], [1234, 5678]),
        ("en", ["1.234", "5.678"], [1.234, 5.678]),
        ("en", ["1,234", "5,678"], [1234, 5678]),
        ("id", ["1,5", "2,25"], [1.5, 2.25]),
    ],
)
def test_explicit_number_format(number_format, values, expected) -> None:
    series, _, notes = _infer_full(values, number_format=number_format)

    assert list(series) == expected
    assert "ambiguous_number_format" not in _codes(notes)


def test_explicit_format_rejects_and_reports_other_styles() -> None:
    values = [f"{i}.5" for i in range(1, 40)] + ["1,5"]
    series, logical, notes = _infer_full(values, number_format="en")

    assert logical == "numeric"
    assert pd.isna(series.iloc[-1])
    note = next(n for n in notes if n.code == "unconverted_values")
    assert note.level == "warning"
    assert "1,5" in note.message


def test_ambiguous_dot_groups_warn_in_auto_mode() -> None:
    _, _, notes = _infer_full(["1.234", "5.678"])

    note = next(n for n in notes if n.code == "ambiguous_number_format")
    assert note.level == "warning"
    assert "1.234" in note.message and "1234" in note.message


def test_ambiguous_comma_groups_warn_in_auto_mode() -> None:
    series, _, notes = _infer_full(["1,234", "5,678"])

    assert list(series) == [1234, 5678]
    assert "ambiguous_number_format" in _codes(notes)


@pytest.mark.parametrize(
    "values",
    [
        ["1.000.000", "250.000"],  # several groups: clearly thousands
        ["1.234,5", "10.000,25"],  # both separators
        ["2,5", "3,75"],  # comma decimals
        ["1.5", "2"],  # dot decimals
        ["10", "20"],  # no separators
    ],
)
def test_unambiguous_numbers_do_not_warn(values) -> None:
    _, _, notes = _infer_full(values)

    assert "ambiguous_number_format" not in _codes(notes)


def test_ambiguous_date_order_warns() -> None:
    _, _, notes = _infer_full(["03/04/2026", "05/06/2026"])

    note = next(n for n in notes if n.code == "ambiguous_date_order")
    assert "3 April 2026" in note.message


def test_explicit_month_first_date_order() -> None:
    series, _, notes = _infer_full(["03/04/2026", "05/06/2026"], date_order="month_first")

    assert list(series.dt.date) == [date(2026, 3, 4), date(2026, 5, 6)]
    assert "ambiguous_date_order" not in _codes(notes)


def test_explicit_order_contradicted_by_data_counts_failures() -> None:
    values = ["13/01/2026"] + ["01/02/2026"] * 39
    series, logical, notes = _infer_full(values, date_order="month_first")

    assert logical == "date"
    assert pd.isna(series.iloc[0])
    assert "unconverted_values" in _codes(notes)


def test_invalid_calendar_dates_are_reported() -> None:
    values = ["31/02/2026"] + [f"{d:02d}/01/2026" for d in range(1, 31)]
    series, logical, notes = _infer_full(values)

    assert logical == "date"
    assert pd.isna(series.iloc[0])
    note = next(n for n in notes if n.code == "unconverted_values")
    assert "31/02/2026" in note.message


def test_mostly_numeric_text_column_is_flagged() -> None:
    values = [str(i) for i in range(1, 9)] + ["n/a?", "unknown"]
    series, logical, notes = _infer_full(values)

    assert logical == "text"
    assert list(series) == values
    note = next(n for n in notes if n.code == "mixed_types")
    assert "80%" in note.message


def test_mostly_date_text_column_is_flagged() -> None:
    values = ["15/01/2026", "16/01/2026", "17/01/2026", "besok"]
    _, logical, notes = _infer_full(values)

    assert logical == "text"
    assert "mixed_types" in _codes(notes)


def test_long_csv_identifiers_are_kept_as_text_with_potential_loss_warning() -> None:
    series, logical, notes = _infer_full(["9171234567890123", "3201234567890127"])

    assert logical == "text"
    assert list(series) == ["9171234567890123", "3201234567890127"]
    note = next(n for n in notes if n.code == "long_identifier")
    assert note.level == "warning"
    assert "no digit is lost during import" in note.message
    assert "cannot be recovered" in note.message


def test_long_excel_numbers_warn_about_15_digit_limit() -> None:
    _, logical, notes = _infer_full(["9171234567890124", "3201234567890127"], number_format="plain")

    assert logical == "text"
    note = next(n for n in notes if n.code == "long_identifier")
    assert note.level == "warning"
    assert "15" in note.message
    assert "cannot be recovered" in note.message


def test_scientific_notation_identifiers_warn_about_lost_digits() -> None:
    values = ["9.17123E+15", "3.20123E+15", "1.11111E+15"]
    _, logical, notes = _infer_full(values)

    assert logical == "text"
    note = next(n for n in notes if n.code == "scientific_notation")
    assert note.level == "warning"
    assert "cannot be recovered" in note.message


def test_clean_columns_have_no_notes() -> None:
    _, _, notes = _infer_full(["10", "20", "30"])

    assert notes == []


# --- Phase 3 review regressions -------------------------------------------------


def _infer_named(name: str, values: list[str | None], **kwargs):
    frame = pd.DataFrame({name: pd.Series(values, dtype="string")})
    result = infer_types(frame, **kwargs)
    return result.dataframe[name], result.types[name], result.notes.get(name, [])


@pytest.mark.parametrize(
    "values",
    [
        ["0012 345", "0013 456"],  # leading zero hidden behind a space
        ["0812 3456 7890", "0813 1111 2222"],  # phone numbers
        ["3201 2345 6789 0123", "3201 2345 6789 0124"],  # spaced NIK
        ["1 2", "3 4"],  # spaces that are not thousands groups
    ],
)
def test_spaced_identifiers_are_never_converted(values) -> None:
    """Review H1: spaces were removed before the identifier checks."""
    series, logical, _ = _infer_full(values)

    assert logical == "text"
    assert list(series) == values


def test_space_thousands_separators_still_work() -> None:
    series, logical, _ = _infer_full(["1 000 000", "250 000"])

    assert logical == "integer"
    assert list(series) == [1_000_000, 250_000]


def test_spaced_nik_gets_long_identifier_warning() -> None:
    _, _, notes = _infer_full(["3201 2345 6789 0123", "3201 2345 6789 0124"])

    assert "long_identifier" in _codes(notes)


@pytest.mark.parametrize("header", ["id_klien", "kode_pelanggan", "no_telepon", "nik", "npwp"])
def test_identifier_headers_stay_text(header: str) -> None:
    """Review H2: numeric-looking IDs must not become integers (minority IDs would be lost)."""
    values = [str(1000 + i) for i in range(60)] + ["KL-77"]
    series, logical, notes = _infer_named(header, values)

    assert logical == "text"
    assert list(series) == values
    note = next(n for n in notes if n.code == "identifier_kept_as_text")
    assert note.level == "info"


def test_count_like_headers_are_still_numbers() -> None:
    _, logical, _ = _infer_named("jumlah_transaksi", ["1", "2", "3"])

    assert logical == "integer"


def test_user_can_force_columns_to_text() -> None:
    frame = pd.DataFrame({"kolom_a": pd.Series(["1", "2"], dtype="string")})

    result = infer_types(frame, text_columns={"kolom_a"})

    assert result.types["kolom_a"] == "text"
    assert result.notes["kolom_a"][0].code == "kept_as_text_by_user"


def test_mixed_thousand_separators_are_not_converted_in_auto_mode() -> None:
    """Review M1: "1.500" was read as 1.5 next to "1,500" read as 1500."""
    values = ["1,500", "1.500", "2,000", "750"]
    series, logical, notes = _infer_full(values)

    assert logical == "text"
    assert list(series) == values
    note = next(n for n in notes if n.code == "mixed_number_format")
    assert note.level == "warning"


def test_mixed_separators_follow_an_explicit_format() -> None:
    values = ["1,500", "1.500", "2,000", "750"]
    series, _, notes = _infer_full(values, number_format="id")

    # Indonesian: "1.500" = 1500, "750" = 750; "1,500"/"2,000" are decimals 1.5 / 2.0.
    assert list(series) == [1.5, 1500.0, 2.0, 750.0]
    assert "mixed_number_format" not in _codes(notes)


def test_mixed_currencies_are_not_summed() -> None:
    """Review M2: Rp and USD values must not end up in one numeric column."""
    values = ["Rp 1.000.000", "$ 100", "USD 50"]
    series, logical, notes = _infer_full(values)

    assert logical == "text"
    assert list(series) == values
    assert "mixed_currency" in _codes(notes)


def test_single_currency_prefix_is_reported() -> None:
    series, logical, notes = _infer_full(["Rp 1.500.000", "Rp 250.000"])

    assert logical == "integer"
    assert list(series) == [1_500_000, 250_000]
    note = next(n for n in notes if n.code == "currency_removed")
    assert "IDR" in note.message


def test_rupiah_dash_suffix_is_supported() -> None:
    """Review M3: "Rp 1.500.000,-" is a common Indonesian notation."""
    series, logical, _ = _infer_full(["Rp 1.500.000,-", "Rp 250.000,--", "Rp 75.000,-"])

    assert logical == "integer"
    assert list(series) == [1_500_000, 250_000, 75_000]


def test_mixed_date_order_is_flagged() -> None:
    """Review L3: a column proven to contain both orders must not read 03/04 silently."""
    values = ["13/04/2026"] * 40 + ["04/13/2026", "03/04/2026"]
    _, logical, notes = _infer_full(values)

    assert logical == "date"
    assert "mixed_date_order" in _codes(notes)


def test_excel_scientific_text_cells_are_identifiers() -> None:
    """Review L5: positive exponents in Excel strings are text cells (e.g. codes), not numbers."""
    series, logical, notes = _infer_full(["9.17E+15", "1E5"], number_format="plain")

    assert logical == "text"
    assert list(series) == ["9.17E+15", "1E5"]


def test_excel_small_floats_with_negative_exponent_are_numbers() -> None:
    series, logical, _ = _infer_full(["1e-05", "0.5"], number_format="plain")

    assert logical == "numeric"
    assert list(series) == [0.00001, 0.5]


def test_mixed_types_wording_does_not_tell_users_to_break_ids() -> None:
    """Review L9: for an identifier, keeping text is correct."""
    values = [str(i) for i in range(1, 9)] + ["n/a?", "unknown"]
    _, _, notes = _infer_full(values)

    message = next(n for n in notes if n.code == "mixed_types").message
    assert "identifier" in message


def test_mixed_format_message_shows_both_readings() -> None:
    _, _, notes = _infer_full(["1,500", "1.500", "2,000", "750"])

    message = next(n for n in notes if n.code == "mixed_number_format").message
    assert "could mean 1.5 or 1500" in message


def test_identifier_header_detection_accepts_raw_headers() -> None:
    values = [str(1000 + i) for i in range(30)]

    _, logical, _ = _infer_named("Client ID", values)

    assert logical == "text"


# ------------------------------------------------- Task 21: "Keep as text" follow-up
def test_dates_kept_as_text_are_flagged() -> None:
    """User test 2026-11-04: dates chosen as "Keep as text" silently lost period analysis."""
    frame = pd.DataFrame({"tanggal": pd.Series(["03/01/2026", "04/01/2026"], dtype="string")})

    result = infer_types(frame, text_columns={"tanggal"})

    assert result.types["tanggal"] == "text"
    codes = [n.code for n in result.notes["tanggal"]]
    assert codes == ["kept_as_text_by_user", "kept_as_text_looks_like_date"]
    note = result.notes["tanggal"][1]
    assert note.level == "warning"
    assert "date" in note.message and "Keep as text" in note.message


def test_numbers_kept_as_text_are_flagged() -> None:
    frame = pd.DataFrame({"pendapatan": pd.Series(["8200000", "104500000"], dtype="string")})

    result = infer_types(frame, text_columns={"pendapatan"})

    codes = [n.code for n in result.notes["pendapatan"]]
    assert codes == ["kept_as_text_by_user", "kept_as_text_looks_like_number"]
    assert result.notes["pendapatan"][1].level == "info"  # IDs are often kept as text on purpose


def test_identifiers_kept_as_text_are_not_flagged() -> None:
    """Text is already the right type for numeric-looking IDs."""
    frame = pd.DataFrame({"id_klien": pd.Series(["1001", "1002"], dtype="string")})

    result = infer_types(frame, text_columns={"id_klien"})

    assert [n.code for n in result.notes["id_klien"]] == ["kept_as_text_by_user"]


def test_detect_types_reports_the_type_without_converting() -> None:
    from backend.services.type_inference import detect_types

    frame = pd.DataFrame(
        {
            "tanggal": pd.Series(["03/01/2026", "04/01/2026"], dtype="string"),
            "nilai": pd.Series(["1.5", "2"], dtype="string"),
            "nama": pd.Series(["a", "b"], dtype="string"),
        }
    )

    assert detect_types(frame) == {"tanggal": "date", "nilai": "numeric", "nama": "text"}


# ----------------------------------- real-data test 2026-11-05: dates with month names
@pytest.mark.parametrize(
    ("values", "expected"),
    [
        (["22/Jan/2025", "4/Feb/2025", "28/May/2025"], ["2025-01-22", "2025-02-04", "2025-05-28"]),
        (["22 Dec 2025", "2 Jan 2026"], ["2025-12-22", "2026-01-02"]),
        (["10-Feb-2025", "26-Mar-2025"], ["2025-02-10", "2025-03-26"]),
        (["17 Agustus 2025", "1 Mei 2026", "5 Okt 2025", "9 Des 2025"],
         ["2025-08-17", "2026-05-01", "2025-10-05", "2025-12-09"]),
        (["Jan 22, 2025", "Feb 4, 2025"], ["2025-01-22", "2025-02-04"]),
        (["3 Agt 2025", "7 Nop 2025", "1 Pebruari 2025"],
         ["2025-08-03", "2025-11-07", "2025-02-01"]),
    ],
)  # fmt: skip
def test_dates_with_month_names(values: list[str], expected: list[str]) -> None:
    series, logical, notes = _infer_full(values)

    assert logical == "date"
    assert [d.date().isoformat() for d in series] == expected
    assert not any(n.code in {"ambiguous_date_order", "mixed_date_order"} for n in notes)


def test_unknown_month_names_are_reported_not_guessed() -> None:
    values = [f"{d}/Jan/2025" for d in range(1, 25)] + ["5/Foo/2025"]  # 96% parse

    series, logical, notes = _infer_full(values)

    assert logical == "date"
    assert pd.isna(series.iloc[-1])
    note = next(n for n in notes if n.code == "unconverted_values")
    assert "5/Foo/2025" in note.message


def test_month_name_dates_are_detected_at_inspection() -> None:
    from backend.services.type_inference import detect_types

    frame = pd.DataFrame({"tgl": pd.Series(["22/Jan/2025", "14/Jan/2025"], dtype="string")})

    assert detect_types(frame) == {"tgl": "date"}
