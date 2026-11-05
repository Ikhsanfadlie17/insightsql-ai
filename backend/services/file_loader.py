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
        sheets = []
        for name in workbook.sheet_names:
            sheet = workbook.get_sheet_by_name(name)
            first = sheet.to_python(nrows=1)
            headers = [_header_text(v) for v in first[0]] if first else []
            sheets.append(SheetInfo(name=name, row_count=max(sheet.height - 1, 0), columns=headers))
        return sheets
    except Exception as exc:  # calamine raises several error types for corrupt files
        logger.warning("Could not read workbook: %s", type(exc).__name__)
        raise UploadError("The Excel file could not be read. It may be corrupt.") from exc


def _header_text(value: object) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def read_headers(path: Path, file_type: FileType, sheet: str | None) -> list[str]:
    """Return the header row as written (before any normalization)."""
    if file_type == "xlsx":
        sheets = {s.name: s for s in list_sheets(path)}
        target = sheet or next(iter(sheets), None)
        if target not in sheets:
            raise UploadError(f"Sheet '{target}' was not found in the workbook.")
        return sheets[target].columns
    sample, _ = _read_text(path)
    first_line = sample.splitlines()[0] if sample.strip() else ""
    row = next(csv.reader([first_line], delimiter=_sniff_delimiter(sample)), [])
    return [cell.strip() for cell in row]


def _read_text(path: Path) -> tuple[str, str]:
    """Return a delimiter-sniffing sample and the encoding that decodes the WHOLE file.

    Decoding only a prefix could cut a multi-byte UTF-8 character and wrongly fall
    back to cp1252, turning every accented value into mojibake.
    """
    raw = path.read_bytes()
    for encoding in _ENCODINGS:
        try:
            return raw.decode(encoding)[:_SNIFF_BYTES], encoding
        except UnicodeDecodeError:
            continue
    raise UploadError("The CSV file encoding is not supported.")


def _sniff_delimiter(sample: str) -> str:
    try:
        return csv.Sniffer().sniff(sample, delimiters=_CSV_DELIMITERS).delimiter
    except csv.Error:
        return ","


def _with_header_row(raw: pd.DataFrame) -> pd.DataFrame:
    """Use the first row as headers exactly as written.

    Reading with ``header=None`` keeps duplicate headers ("Nama", "Nama") instead of
    pandas' "Nama.1" renaming; blank headers become "". Labels may repeat here; they
    are made unique by ``normalize_columns`` during ingestion.
    """
    if raw.empty:
        return raw
    headers = ["" if pd.isna(v) else str(v).strip() for v in raw.iloc[0]]
    body = raw.iloc[1:].reset_index(drop=True)
    body.columns = headers
    return body


def _tidy(df: pd.DataFrame) -> pd.DataFrame:
    """Drop fully empty rows, and columns that have neither a header nor any values."""
    df = df.dropna(how="all")
    keep = [
        position
        for position, header in enumerate(df.columns)
        if not (_is_blank_header(header) and df.iloc[:, position].isna().all())
    ]
    return df.iloc[:, keep].reset_index(drop=True)


def _is_blank_header(header: object) -> bool:
    text = str(header).strip()
    return text == "" or text.startswith("Unnamed:")


def _load_csv(path: Path, max_rows: int | None = None) -> pd.DataFrame:
    sample, encoding = _read_text(path)
    if not sample.strip():
        raise UploadError("The file is empty.")
    raw = pd.read_csv(
        path,
        sep=_sniff_delimiter(sample),
        header=None,
        dtype="string",
        encoding=encoding,
        keep_default_na=False,
        na_values=_NA_VALUES,
        nrows=None if max_rows is None else max_rows + 1,  # + the header row
    )
    return _with_header_row(raw)


def _load_xlsx(path: Path, sheet: str | None, max_rows: int | None = None) -> pd.DataFrame:
    names = [s.name for s in list_sheets(path)]
    if not names:
        raise UploadError("The workbook has no sheets.")
    target = sheet or names[0]
    if target not in names:
        raise UploadError(f"Sheet '{target}' was not found in the workbook.")
    raw = pd.read_excel(
        path,
        sheet_name=target,
        engine="calamine",
        dtype="string",
        header=None,
        nrows=None if max_rows is None else max_rows + 1,
    )
    return _with_header_row(raw)


def load_dataframe(
    path: Path, file_type: FileType, sheet: str | None, max_rows: int | None = None
) -> pd.DataFrame:
    """Read a CSV or one Excel sheet with every value as text.

    Args:
        path: Location of the stored upload.
        file_type: ``"csv"`` or ``"xlsx"``.
        sheet: Worksheet name for Excel files (first sheet when None).
        max_rows: Read only the first data rows (a sample); None reads all.

    Raises:
        UploadError: If the file is empty, unreadable, or the sheet is missing.

    Returns:
        A frame whose column labels are the raw header cells (possibly repeated or "").
    """
    try:
        df = _load_csv(path, max_rows) if file_type == "csv" else _load_xlsx(path, sheet, max_rows)
    except UploadError:
        raise
    except pd.errors.ParserError as exc:
        raise UploadError(
            "Some rows have more values than the header row (for example an extra "
            "delimiter at the end of a line). Fix those rows and upload again."
        ) from exc
    except (pd.errors.EmptyDataError, ValueError) as exc:
        raise UploadError("The file could not be parsed as a table.") from exc
    df = _tidy(df)
    if df.empty or len(df.columns) == 0:
        raise UploadError("The file contains no data rows.")
    return df
