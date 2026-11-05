"""Smart import: find the header row, the main table, and trailing template rows in sheets
laid out for people (title rows, side tables, pre-filled template rows), as exported from
Google Sheets. Dummy values throughout."""

from __future__ import annotations

import pandas as pd
import pytest

from backend.services.file_loader import detect_layout


def _raw(rows: list[list[str]]) -> pd.DataFrame:
    width = max(len(r) for r in rows)
    padded = [[(v if v != "" else None) for v in r] + [None] * (width - len(r)) for r in rows]
    return pd.DataFrame(padded, dtype="string")


HEADER = ["", "No", "Tahun", "Status", "ID Pembeli", "Nama Pembeli", "Tgl Order",
          "Lama Kirim", "Nilai Order", "", "", "", "Daftar hari libur"]  # fmt: skip


def _people_sheet(real_rows: int = 6, template_rows: int = 5) -> pd.DataFrame:
    rows = [
        ["", "", "Diisi admin", "Diisi gudang", "", "", "Rekap Contoh"],
        ["", "", "", "", "", "", "", "Pengiriman (2 hari)"],
        HEADER,
    ]
    for i in range(1, real_rows + 1):
        rows.append(["", str(i), "2025", "Closed", f"PLG-{i:03d}", f"Klien {i}",
                     f"{i}/Jan/2025", f"{i} hari", f"{i * 1000}", "", "", "7 hari",
                     f"{i}/Mar/2025"])  # fmt: skip
    for i in range(real_rows + 1, real_rows + template_rows + 1):
        # Some formula cells show "-" in template rows.
        status = "-" if i % 2 else ""
        # ...and formula errors or messages: "#REF!", "Data belum lengkap".
        nilai = "#REF!" if i % 3 == 0 else ""
        lama = "Data belum lengkap" if i % 3 == 1 else "-"
        rows.append(["", str(i), "", status, "", "", "", lama, nilai, "", "", "", ""])
    rows.append([""] * len(HEADER))  # an empty row at the very end
    return _raw(rows)


def test_plain_table_is_unchanged() -> None:
    raw = _raw([["ID", "Nama", "Nilai"], ["A1", "x", "1"], ["A2", "y", "2"], ["A3", "", "3"]])

    layout = detect_layout(raw)

    assert layout.header_index == 0
    assert layout.columns == [0, 1, 2]
    assert layout.ignored_columns == [] and layout.template_rows == 0


def test_title_rows_side_table_and_template_rows_are_found() -> None:
    layout = detect_layout(_people_sheet())

    assert layout.header_index == 2  # third row (header_row 3 for people)
    assert [HEADER[i] for i in layout.columns] == HEADER[1:9]
    assert layout.ignored_column_labels == ["column 12", "Daftar hari libur"]
    assert layout.template_rows == 5


def test_header_row_can_be_chosen() -> None:
    layout = detect_layout(_people_sheet(), header_index=0)

    assert layout.header_index == 0
    assert layout.chosen


def test_template_rows_can_be_kept() -> None:
    assert detect_layout(_people_sheet(), skip_template_rows=False).template_rows == 0


@pytest.mark.parametrize("trailing", [1, 2])
def test_a_few_sparse_last_rows_are_real_data(trailing: int) -> None:
    """Fewer than 3 trailing rows are never treated as a template block."""
    assert detect_layout(_people_sheet(template_rows=trailing)).template_rows == 0


def test_sparse_rows_in_the_middle_are_kept() -> None:
    raw = _people_sheet(template_rows=0)
    middle = ["", "99", "", "", "", "", "", "-", "", "", "", "", ""]
    rows = raw.values.tolist()
    rows.insert(5, middle)
    layout = detect_layout(pd.DataFrame(rows, dtype="string"))

    assert layout.template_rows == 0


def test_a_numeric_first_row_is_not_a_header() -> None:
    """Rows of numbers or dates are data, even when they are the widest."""
    raw = _raw([["Laporan 2025"], ["Tanggal", "Jumlah", "Nilai"], ["1/Jan/2025", "3", "10"],
                ["2/Jan/2025", "4", "20"]])  # fmt: skip

    assert detect_layout(raw).header_index == 1


def test_no_header_like_row_falls_back_to_the_first_row() -> None:
    raw = _raw([["1", "2", "3"], ["4", "5", "6"]])

    assert detect_layout(raw).header_index == 0
