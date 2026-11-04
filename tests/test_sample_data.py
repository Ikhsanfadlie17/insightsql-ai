"""Task 2: deterministic sample dataset generator."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from sample_data.generate_sample_data import COLUMNS, generate, write_outputs

ID, NAME, DATE, DEPT, SERVICE, REVENUE, STATUS = COLUMNS


@pytest.fixture(scope="module")
def df() -> pd.DataFrame:
    return generate(seed=42)


def _active_ids(df: pd.DataFrame, year: int) -> set[str]:
    rows = df[(df[DATE].dt.year == year) & df[ID].notna()]
    return set(rows[ID])


def _first_year(df: pd.DataFrame) -> pd.Series:
    return df.dropna(subset=[ID]).groupby(ID)[DATE].min().dt.year


def test_columns_use_indonesian_headers(df: pd.DataFrame) -> None:
    assert list(df.columns) == [
        "ID Klien",
        "Nama Klien",
        "Tanggal Layanan",
        "Departemen",
        "Jenis Layanan",
        "Pendapatan (Rp)",
        "Status",
    ]


def test_at_least_100_clients(df: pd.DataFrame) -> None:
    assert df[ID].nunique() >= 100


def test_multiple_years_and_cutoff(df: pd.DataFrame) -> None:
    assert set(df[DATE].dt.year) >= {2023, 2024, 2025, 2026}
    assert df[DATE].max().date() <= date(2026, 10, 31)


def test_departments_and_services(df: pd.DataFrame) -> None:
    assert df[DEPT].nunique() >= 4
    assert df[SERVICE].nunique() >= 6


def test_multiple_transactions_per_client(df: pd.DataFrame) -> None:
    per_client = df.dropna(subset=[ID]).groupby(ID).size()
    assert (per_client >= 2).sum() >= 30


def test_missing_values_present(df: pd.DataFrame) -> None:
    assert df[ID].isna().sum() > 0
    assert df[REVENUE].isna().sum() > 0


def test_exact_duplicates_present(df: pd.DataFrame) -> None:
    assert df.duplicated().sum() > 0


def test_name_inconsistency_present(df: pd.DataFrame) -> None:
    names_per_id = df.dropna(subset=[ID]).groupby(ID)[NAME].nunique()
    assert (names_per_id >= 2).any()


def test_ids_keep_leading_zeros(df: pd.DataFrame) -> None:
    ids = df[ID].dropna()
    assert ids.str.fullmatch(r"KL-\d{4}").all()
    assert "KL-0001" in set(ids)


def test_client_lifecycle_categories_exist(df: pd.DataFrame) -> None:
    a2024, a2025, a2026 = (_active_ids(df, y) for y in (2024, 2025, 2026))
    first_year = _first_year(df)

    returning = a2025 & a2026
    new_2026 = {c for c in a2026 if first_year[c] == 2026}
    not_returned = a2025 - a2026
    reactivated = (a2024 - a2025) & a2026

    assert len(returning) >= 10
    assert len(new_2026) >= 10
    assert len(not_returned) >= 10
    assert len(reactivated) >= 5


def test_generation_is_deterministic() -> None:
    pd.testing.assert_frame_equal(generate(seed=42), generate(seed=42))


def test_different_seed_changes_data() -> None:
    assert not generate(seed=1).equals(generate(seed=2))


def test_write_outputs_creates_xlsx_and_csv(tmp_path: Path) -> None:
    paths = write_outputs(tmp_path, seed=42)

    xlsx = tmp_path / "client_services.xlsx"
    csv = tmp_path / "client_services.csv"
    assert set(paths) == {xlsx, csv}
    assert pd.ExcelFile(xlsx, engine="calamine").sheet_names == ["Transactions", "Departments"]

    raw_csv = pd.read_csv(csv, dtype=str)
    assert list(raw_csv.columns) == list(COLUMNS)
    # CSV dates use the Indonesian day-first format to exercise date parsing.
    assert raw_csv[DATE].str.fullmatch(r"\d{2}/\d{2}/\d{4}").all()
    assert len(raw_csv) == len(generate(seed=42))
