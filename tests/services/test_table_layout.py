"""Smart import: find the header row, the main table, and trailing template rows in sheets
laid out for people (title rows, side tables, pre-filled template rows), as exported from
Google Sheets. Dummy values throughout."""

from __future__ import annotations

import random
from pathlib import Path

import pandas as pd
import pytest

from backend.core.errors import UploadError
from backend.services.file_loader import apply_layout, detect_layout, load_table


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


# --- Phase 8 review A1: sparse rows with real values are data, never template rows ------
def _with_tail(total: int, sparse: int) -> list[list[str]]:
    rows = [["Nama", "Jumlah", "Catatan"]]
    for i in range(total):
        note = "" if i >= total - sparse else f"catatan {i}"
        rows.append([f"Klien {i}", str(100 + i * 7), note])
    return rows


@pytest.mark.parametrize(("total", "sparse"), [(100, 10), (1000, 25), (50, 5)])
def test_rows_missing_only_an_optional_column_are_data(total: int, sparse: int) -> None:
    raw = _raw(_with_tail(total, sparse))

    layout = detect_layout(raw)

    assert layout.template_rows == 0
    assert len(apply_layout(raw, layout)) == total


def test_new_clients_with_only_a_name_and_date_are_data() -> None:
    header = ["No", "Nama", "Masuk", "Selesai", "Nilai", "Auditor"]
    rows = [header]
    for i in range(40):
        rows.append([str(i + 1), f"Klien {i}", f"{i % 28 + 1}/Jan/2025", "2/Feb/2025", "100", "A"])
    for i in range(40, 46):
        rows.append([str(i + 1), f"Klien {i}", f"{i - 39}/Mar/2025", "", "", ""])

    assert detect_layout(_raw(rows)).template_rows == 0


def test_a_named_row_just_above_the_template_block_is_kept() -> None:
    rows = _people_sheet(real_rows=6, template_rows=8).astype(object).where(lambda f: f.notna(), "")
    grid = rows.values.tolist()
    first_template = next(i for i, r in enumerate(grid) if r[1] == "7")
    grid[first_template][5] = "Klien Baru"  # Nama Pembeli

    assert detect_layout(_raw(grid)).template_rows == 7


def _grid(raw: pd.DataFrame) -> list[list[str]]:
    return raw.astype(object).where(lambda f: f.notna(), "").values.tolist()


def test_lone_notes_below_a_template_block_are_skipped_with_it() -> None:
    """A sheet can end with template rows and then a few rows of one text cell."""
    grid = [r for r in _grid(_people_sheet(real_rows=100, template_rows=8)) if any(r)]
    grid.append(["", "", "", "catatan pertama"] + [""] * 9)
    grid.append(["", "", "", "catatan kedua"] + [""] * 9)
    raw = _raw(grid)

    layout = detect_layout(raw)

    assert layout.template_rows == 10
    assert len(apply_layout(raw, layout)) == 100


def test_a_large_dummy_sheet_keeps_every_real_row() -> None:
    """Dummy data: title rows, a side table, sparse real rows, then a long template block."""
    rng = random.Random(11)
    header = ["No", "Nama", "Kota", "Masuk", "Selesai", "Nilai", "Auditor", "Status", "Durasi",
              "Keterangan", "", "", "Hari libur"]  # fmt: skip
    rows = [["Rekap Contoh"], ["Diisi admin", "Diisi auditor"], header]
    for i in range(1, 181):
        filled = [str(i), f"Dummy {i}", rng.choice(["Kota A", "Kota B"]), f"{i % 28 + 1}/Jan/2025"]
        optional = [f"{i % 28 + 1}/Feb/2025", str(i * 1000), f"Auditor {i % 5}", "Selesai",
                    f"{i % 9} hari", f"catatan {i}"]  # fmt: skip
        rows.append(filled + [v if rng.random() > 0.4 else "" for v in optional])
    for i in range(181, 481):
        message = "Data belum lengkap" if i % 3 else "-"
        rows.append([str(i), "", "", "", "", "", "", "#REF!", "-", message])
    rows += [
        ["", "", "", "", "", "", "", "catatan bawah"],
        ["", "", "", "", "", "", "", "catatan lain"],
    ]
    raw = _raw(rows)

    layout = detect_layout(raw)
    table = apply_layout(raw, layout)

    assert layout.header_index == 2 and layout.rows_above_header == 2
    assert layout.template_rows == 302
    assert len(table) == 180
    assert table["Nama"].tolist() == [f"Dummy {i}" for i in range(1, 181)]


def test_a_lone_cell_without_a_template_block_is_data() -> None:
    rows = _with_tail(20, 0) + [["Klien A", "", ""], ["Klien B", "", ""], ["Klien C", "", ""]]

    assert detect_layout(_raw(rows)).template_rows == 0


def test_repeated_full_rows_are_duplicates_not_template_rows() -> None:
    rows = _with_tail(20, 0) + [["Klien X", "5", "sama"]] * 4

    assert detect_layout(_raw(rows)).template_rows == 0


def test_whitespace_only_rows_are_not_data_and_do_not_shift_the_cut() -> None:
    rows = [["No", "Nama", "Nilai"], ["1", "a", "10"], ["2", "b", "20"], ["3", "c", "30"]]
    rows += [["4", "", ""], ["5", "", ""], ["6", "", ""], ["  ", "  ", "  "], [" ", "", ""]]
    raw = _raw(rows)

    layout = detect_layout(raw)
    table = apply_layout(raw, layout)

    assert layout.template_rows == 3
    assert table["Nama"].tolist() == ["a", "b", "c"]


# --- Phase 8 review A2: header labels that are years, dates, or running numbers ---------
@pytest.mark.parametrize(
    "header",
    [
        ["Kota", "2023", "2024", "2025"],
        ["Kota", "2023.0", "2024.0", "2025.0"],
        ["Kota", "2025-01-01 00:00:00", "2025-02-01 00:00:00", "2025-03-01 00:00:00"],
        ["Kota", "1/Jan/2025", "1/Feb/2025", "1/Mar/2025"],
        ["Pegawai", "1", "2", "3", "4", "5"],
    ],
)
def test_a_header_with_period_labels_is_found(header: list[str]) -> None:
    width = len(header)
    rows = [header, ["Jakarta"] + ["10"] * (width - 1), ["Medan"] + ["20"] * (width - 1)]
    rows.append(["Budi Santoso"] + ["cuti"] * (width - 1))
    rows.append(["Bandung"] + ["40"] * (width - 1))

    raw = _raw(rows)
    layout = detect_layout(raw)

    assert layout.header_index == 0 and layout.rows_above_header == 0
    assert len(apply_layout(raw, layout)) == 4


def test_a_summary_row_above_the_header_stays_data_and_is_counted() -> None:
    raw = _raw([["Laporan 2025"], ["Total", "100", "200", "300"], ["Nama", "A", "B", "C"],
                ["x", "1", "2", "3"]])  # fmt: skip

    layout = detect_layout(raw)

    assert layout.header_index == 2
    assert layout.rows_above_header == 2  # both are skipped, and reported
    assert layout.table_like_rows_above == 1  # "Total, 100, 200, 300" looks like the table


def test_blank_rows_above_the_header_are_not_counted() -> None:
    raw = _raw([["", "", ""], ["Judul"], ["", "", ""], ["ID", "Nama", "Kota"], ["1", "a", "b"]])

    layout = detect_layout(raw)

    assert layout.header_index == 3
    assert layout.rows_above_header == 1 and layout.table_like_rows_above == 0


# --- Phase 8 review A3: an unmatched quote must not swallow the rest of the file --------
def test_an_unmatched_quote_is_an_upload_error(tmp_path: Path) -> None:
    path = tmp_path / "broken.csv"
    path.write_bytes(b'a,b\n1,"x\n2,y\n3,z\n4,w\n')

    with pytest.raises(UploadError, match="quote"):
        load_table(path, "csv", None)


def test_a_quoted_line_break_is_still_one_cell(tmp_path: Path) -> None:
    path = tmp_path / "multiline.csv"
    path.write_bytes(b'a,b\n1,"Jl. Sudirman, No 1\nBandung"\n2,y\n')

    table, _ = load_table(path, "csv", None)

    assert len(table) == 2
    assert table.iloc[0, 1] == "Jl. Sudirman, No 1\nBandung"
