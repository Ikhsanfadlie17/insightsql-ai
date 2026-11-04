"""Upload validation and reading CSV/XLSX files into string DataFrames."""

from __future__ import annotations

import csv
import logging
from pathlib import Path

import pandas as pd
import python_calamine

from backend.core.errors import UploadError
from backend.schemas.datasets import FileType, SheetInfo

logger = logging.getLogger(__name__)

_ZIP_MAGIC = b"PK\x03\x04"
_CSV_DELIMITERS = ",;\t|"
_ENCODINGS = ("utf-8-sig", "cp1252", "latin-1")
_NA_VALUES = ["", "NA", "N/A", "n/a", "null", "NULL", "None", "-"]
_SNIFF_BYTES = 64 * 1024


def detect_file_type(filename: str, head: bytes) -> FileType:
    """Validate an upload by extension and content signature.

    Args:
        filename: Client-supplied filename (only its extension is used).
        head: The first bytes of the file.

    Raises:
        UploadError: If the type is unsupported or the content does not match it.
    """
    suffix = Path(filename or "").suffix.lower()
    if suffix == ".xlsx":
        if not head.startswith(_ZIP_MAGIC):
            raise UploadError("The file has an .xlsx extension but is not a valid Excel file.")
        return "xlsx"
    if suffix == ".csv":
        if b"\x00" in head:
            raise UploadError("The CSV file contains binary data.")
        return "csv"
    raise UploadError("Unsupported file type. Upload an .xlsx or .csv file.")


def list_sheets(path: Path) -> list[SheetInfo]:
    """List worksheets of an Excel file with their data-row counts (header excluded)."""
    try:
        workbook = python_calamine.CalamineWorkbook.from_path(str(path))
        return [
            SheetInfo(name=name, row_count=max(workbook.get_sheet_by_name(name).height - 1, 0))
            for name in workbook.sheet_names
        ]
    except Exception as exc:  # calamine raises several error types for corrupt files
        logger.warning("Could not read workbook: %s", type(exc).__name__)
        raise UploadError("The Excel file could not be read. It may be corrupt.") from exc


def _read_text(path: Path) -> tuple[str, str]:
    raw = path.read_bytes()[:_SNIFF_BYTES]
    for encoding in _ENCODINGS:
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise UploadError("The CSV file encoding is not supported.")


def _sniff_delimiter(sample: str) -> str:
    try:
        return csv.Sniffer().sniff(sample, delimiters=_CSV_DELIMITERS).delimiter
    except csv.Error:
        return ","


def _tidy(df: pd.DataFrame) -> pd.DataFrame:
    """Drop fully empty rows and empty columns that have no header."""
    df = df.dropna(how="all")
    empty_unnamed = [c for c in df.columns if str(c).startswith("Unnamed:") and df[c].isna().all()]
    return df.drop(columns=empty_unnamed).reset_index(drop=True)


def _load_csv(path: Path) -> pd.DataFrame:
    sample, encoding = _read_text(path)
    if not sample.strip():
        raise UploadError("The file is empty.")
    return pd.read_csv(
        path,
        sep=_sniff_delimiter(sample),
        dtype="string",
        encoding=encoding,
        keep_default_na=False,
        na_values=_NA_VALUES,
    )


def _load_xlsx(path: Path, sheet: str | None) -> pd.DataFrame:
    names = [s.name for s in list_sheets(path)]
    if not names:
        raise UploadError("The workbook has no sheets.")
    target = sheet or names[0]
    if target not in names:
        raise UploadError(f"Sheet '{target}' was not found in the workbook.")
    return pd.read_excel(path, sheet_name=target, engine="calamine", dtype="string")


def load_dataframe(path: Path, file_type: FileType, sheet: str | None) -> pd.DataFrame:
    """Read a CSV or one Excel sheet with every value as text.

    Args:
        path: Location of the stored upload.
        file_type: ``"csv"`` or ``"xlsx"``.
        sheet: Worksheet name for Excel files (first sheet when None).

    Raises:
        UploadError: If the file is empty, unreadable, or the sheet is missing.
    """
    try:
        df = _load_csv(path) if file_type == "csv" else _load_xlsx(path, sheet)
    except UploadError:
        raise
    except (pd.errors.ParserError, pd.errors.EmptyDataError, ValueError) as exc:
        raise UploadError("The file could not be parsed as a table.") from exc
    df = _tidy(df)
    if df.empty or len(df.columns) == 0:
        raise UploadError("The file contains no data rows.")
    return df
