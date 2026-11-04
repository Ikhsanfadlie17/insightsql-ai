"""Task 3: upload type detection, sheet listing, and file loading."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from backend.core.errors import UploadError
from backend.services.file_loader import detect_file_type, list_sheets, load_dataframe
from tests.conftest import SAMPLE_DATA_DIR


def test_detects_xlsx_by_extension_and_magic_bytes() -> None:
    assert detect_file_type("data.xlsx", b"PK\x03\x04rest") == "xlsx"
    assert detect_file_type("DATA.XLSX", b"PK\x03\x04rest") == "xlsx"


def test_rejects_xlsx_without_zip_signature() -> None:
    with pytest.raises(UploadError):
        detect_file_type("data.xlsx", b"not a zip file")


def test_detects_csv_text() -> None:
    assert detect_file_type("data.csv", b"a,b\n1,2\n") == "csv"


def test_rejects_binary_csv() -> None:
    with pytest.raises(UploadError):
        detect_file_type("data.csv", b"a,b\x00\x01\x02")


@pytest.mark.parametrize("name", ["malware.exe", "report.pdf", "old.xls", "noextension", ""])
def test_rejects_unsupported_extensions(name: str) -> None:
    with pytest.raises(UploadError):
        detect_file_type(name, b"PK\x03\x04")


def test_lists_sheets_with_row_counts() -> None:
    sheets = list_sheets(SAMPLE_DATA_DIR / "client_services.xlsx")

    assert [s.name for s in sheets] == ["Transactions", "Departments"]
    assert sheets[0].row_count == 795
    assert sheets[1].row_count == 5


def test_loads_selected_sheet_as_text() -> None:
    df = load_dataframe(SAMPLE_DATA_DIR / "client_services.xlsx", "xlsx", "Departments")

    assert list(df.columns) == ["Departemen", "Kepala Departemen", "Lokasi"]
    assert len(df) == 5


def test_rejects_unknown_sheet() -> None:
    with pytest.raises(UploadError):
        load_dataframe(SAMPLE_DATA_DIR / "client_services.xlsx", "xlsx", "Nope")


def test_loads_sample_csv() -> None:
    df = load_dataframe(SAMPLE_DATA_DIR / "client_services.csv", "csv", None)

    assert len(df) == 795
    assert df["ID Klien"].iloc[0].startswith("KL-")


@pytest.mark.parametrize("sep", [";", "\t", "|"])
def test_sniffs_csv_delimiter(tmp_path: Path, sep: str) -> None:
    path = tmp_path / "x.csv"
    path.write_text(sep.join(["ID", "Nama", "Nilai"]) + "\n" + sep.join(["001", "A", "1,5"]) + "\n")

    df = load_dataframe(path, "csv", None)

    assert list(df.columns) == ["ID", "Nama", "Nilai"]
    assert df.iloc[0].tolist() == ["001", "A", "1,5"]


def test_reads_latin1_csv(tmp_path: Path) -> None:
    path = tmp_path / "x.csv"
    path.write_bytes("nama,kota\nJosé,Bogor\n".encode("latin-1"))

    df = load_dataframe(path, "csv", None)

    assert df.iloc[0, 0] == "José"


def test_drops_fully_empty_rows_and_unnamed_empty_columns(tmp_path: Path) -> None:
    path = tmp_path / "x.xlsx"
    frame = pd.DataFrame({"A": ["1", None, "3"], "Unnamed: 1": [None, None, None]})
    frame.to_excel(path, index=False)

    df = load_dataframe(path, "xlsx", "Sheet1")

    assert list(df.columns) == ["A"]
    assert len(df) == 2


def test_empty_file_is_an_upload_error(tmp_path: Path) -> None:
    path = tmp_path / "x.csv"
    path.write_text("")

    with pytest.raises(UploadError):
        load_dataframe(path, "csv", None)
