"""Task 3: header normalization into safe SQL identifiers."""

from __future__ import annotations

import re

import pytest

from backend.services.column_normalizer import normalize_columns


def test_normalizes_messy_headers() -> None:
    headers = ["ID Klien", "Nama Klien", "nama klien", "", "Unnamed: 4", "2025 Revenue", "select"]

    result = normalize_columns(headers)

    assert [c.name for c in result] == [
        "id_klien",
        "nama_klien",
        "nama_klien_2",
        "column_4",
        "column_5",
        "col_2025_revenue",
        "select_col",
    ]
    assert [c.original for c in result] == headers


def test_strips_accents_and_symbols() -> None:
    result = normalize_columns(["Pendapatan (Rp)", "Café Name", "  Tanggal   Layanan  ", "%"])

    assert [c.name for c in result] == ["pendapatan_rp", "cafe_name", "tanggal_layanan", "column_4"]


def test_suffixed_names_never_collide() -> None:
    result = normalize_columns(["a", "a_2", "a", "A"])

    names = [c.name for c in result]
    assert len(set(names)) == len(names)
    assert names[:2] == ["a", "a_2"]


def test_names_are_valid_identifiers_and_bounded() -> None:
    result = normalize_columns(["x" * 200, "Jenis-Layanan/Produk", "Nilai.Total"])

    for column in result:
        assert re.fullmatch(r"[a-z_][a-z0-9_]*", column.name)
        assert len(column.name) <= 63


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("ClientID", "client_id"),
        ("CustomerID", "customer_id"),
        ("IDPelanggan", "id_pelanggan"),
        ("TanggalLayanan", "tanggal_layanan"),
        ("ID Klien", "id_klien"),
        ("NIK", "nik"),
        ("customerName", "customer_name"),
    ],
)
def test_camel_case_headers_are_split(header: str, expected: str) -> None:
    """Review L6: CamelCase headers produced tokens such as "clientid" that matched nothing."""
    assert normalize_columns([header])[0].name == expected
