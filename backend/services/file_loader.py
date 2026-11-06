"""Upload validation and reading CSV/XLSX files into string DataFrames."""

from __future__ import annotations

import csv
import io
import itertools
import logging
import re
from dataclasses import dataclass, field
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


# --------------------------------------------------------------- smart import (layout)
HEADER_SCAN_ROWS = 30  # the header must be within the first 30 rows
_NUMERIC_LIKE = re.compile(r"^[\d\s.,%+\-/:()]*$")
_NAMED_DATE_LIKE = re.compile(r"^\d{1,2}[\s/.-]+[A-Za-z]{3,9}\.?[\s/.,-]+\d{4}$")
_PERIOD_LABEL = re.compile(r"^(19|20)\d{2}$|^\d{4}-\d{2}(-\d{2})?$|^\d{1,2}[/-]\d{1,2}[/-]\d{2,4}$")
_CONTENT_FILL = 0.98  # columns filled in (almost) every row: row numbers, formula defaults
_MIN_TEMPLATE_BLOCK = 3
_MAX_NOTE_ROWS = 5  # lone-cell rows allowed below a template block
_MIN_RUNNING_LABELS = 5  # a header of running numbers: the days of a month, for example


@dataclass
class TableLayout:
    """Where the table is inside a sheet laid out for people."""

    header_index: int  # 0-based row of the header
    chosen: bool  # True when the user picked the header row
    columns: list[int]  # 0-based positions kept, in order
    ignored_columns: list[int] = field(default_factory=list)  # side tables / helper columns
    ignored_column_labels: list[str] = field(default_factory=list)
    template_rows: int = 0  # trailing rows with only row numbers / repeated default values
    rows_above_header: int = 0  # non-empty rows above the header, which are not imported
    table_like_rows_above: int = 0  # of those, rows about as wide as the header

    @property
    def header_row(self) -> int:
        """1-based, as people count rows."""
        return self.header_index + 1


def _cell(value: object) -> str:
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return ""
    return str(value).strip()


_MIDNIGHT = re.compile(r"^(\d{4}-\d{2}-\d{2}) 00:00:00$")
_WHOLE_FLOAT = re.compile(r"^(-?\d+)\.0$")


def _header_label(value: object) -> str:
    """A header cell as people wrote it: an Excel date header without '00:00:00' and a
    number header without '.0' (as the inspection showed them before smart import)."""
    text = _cell(value)
    for pattern in (_MIDNIGHT, _WHOLE_FLOAT):
        match = pattern.match(text)
        if match:
            return match.group(1)
    return text


def _is_text_label(value: str) -> bool:
    return bool(value) and not _NUMERIC_LIKE.match(value) and not _NAMED_DATE_LIKE.match(value)


def _as_int(value: str) -> int | None:
    try:
        number = float(value)
    except ValueError:
        return None
    return int(number) if number.is_integer() else None


def _has_period_labels(values: list[str]) -> bool:
    """Text labels next to distinct years, dates, or running numbers: 'Kota, 2023, 2024'.

    Other numbers do not count: 'Total, 100, 200, 300' above a table is a row of values.
    """
    labels = [_header_label(v) for v in values]
    others = [v for v in labels if not _is_text_label(v)]
    if len(others) < 2 or len(others) == len(labels) or len(set(others)) != len(others):
        return False
    if all(_PERIOD_LABEL.match(v) or _NAMED_DATE_LIKE.match(v) for v in others):
        return True
    numbers = [_as_int(v) for v in others]
    if len(numbers) < _MIN_RUNNING_LABELS or None in numbers:  # "A, 1, 2, 3" may well be data
        return False
    return all(b == a + 1 for a, b in itertools.pairwise(numbers))


def _header_index(cells: list[list[str]]) -> int:
    """First row that looks like a header: at least half as wide as the widest row near
    the top, and its cells mostly distinct text labels, or labels with periods."""
    window = cells[:HEADER_SCAN_ROWS]
    widest = max((sum(bool(v) for v in row) for row in window), default=0)
    needed = max(2, -(-widest // 2))
    for index, row in enumerate(window):
        values = [v for v in row if v]
        if len(values) < needed:
            continue
        text = [v for v in values if _is_text_label(v)]
        # Duplicate headers are allowed ("Nama, Nama, Kota"), so only half must differ.
        if len(text) >= 0.8 * len(values) and len(set(text)) >= 0.5 * len(text):
            return index
        if _has_period_labels(values):
            return index
    return 0


def _rows_above(cells: list[list[str]], header: int) -> tuple[int, int]:
    """Non-empty rows above the header, and how many of them are about as wide as it."""
    widths = [sum(bool(v) for v in row) for row in cells[:header]]
    needed = max(2, -(-sum(bool(v) for v in cells[header]) // 2))
    return sum(w > 0 for w in widths), sum(w >= needed for w in widths)


def _main_columns(cells: list[list[str]], header: int) -> tuple[list[int], list[int]]:
    """Split the sheet at runs of 2+ fully empty columns and keep the block with the most
    header labels; the other non-empty columns are reported as ignored."""
    rows = cells[header:]
    width = max((len(r) for r in rows), default=0)
    filled = [any(r[c] for r in rows if c < len(r)) for c in range(width)]
    blocks: list[list[int]] = [[]]
    empty_run = 0
    for c in range(width):
        if not filled[c]:
            empty_run += 1
            continue
        if empty_run >= 2 and blocks[-1]:
            blocks.append([])
        empty_run = 0
        blocks[-1].append(c)
    header_row = cells[header] if header < len(cells) else []

    def labels(block: list[int]) -> int:
        return sum(1 for c in block if c < len(header_row) and header_row[c])

    main = max(blocks, key=labels) if blocks else []
    ignored = [c for block in blocks if block is not main for c in block]
    return main, ignored


_SHEET_ERROR = re.compile(r"^#(REF!|N/A|VALUE!|DIV/0!|NAME\?|NUM!|NULL!|ERROR!|SPILL!)$")
_TEMPLATE_DENSITY = 0.2  # a template row holds at most 20% of what a full row holds


def _generated_length(entries: list[tuple[int, str]], size: int) -> int:
    """How many rows of a block one column allows, given its (position, value) cells with
    the last sheet row at position 0. Generated values are one value repeated or a running
    number; a value seen once, or a value that differs, may be real data."""
    if not entries:
        return size
    start, first = entries[0]
    if len(entries) == 1:
        return start
    repeated = next((i for i, (_, v) in enumerate(entries) if v != first), len(entries))
    base = None if _as_int(first) is None else _as_int(first) + start
    running = len(entries)
    for i, (position, value) in enumerate(entries):
        number = _as_int(value)
        if base is None or number is None or number + position != base:
            running = i
            break
    valid = max(repeated, running)
    if valid == len(entries):
        return size
    return start if valid < 2 else entries[valid][0]


def _generated_block(block: list[list[str]], columns: list[int]) -> int:
    """Rows of ``block`` (last sheet row first) holding only generated values, in every
    column. Shortening the block can leave a value seen once, so this repeats until stable."""
    size = len(block)
    while size >= _MIN_TEMPLATE_BLOCK:
        allowed = size
        for c in columns:
            cells = [(i, r[c]) for i, r in enumerate(block[:size]) if c < len(r) and r[c]]
            allowed = min(allowed, _generated_length(cells, size))
        if allowed == size:
            return size
        size = allowed
    return 0


def _template_rows(body: list[list[str]], columns: list[int]) -> int:
    """Trailing rows of a pre-filled template: far less filled than a full row, and holding
    only row numbers and repeated default values. Columns filled in (almost) every row are
    not counted for the fill; NA markers ("-") and spreadsheet errors (#REF!) are no value.
    Any differing value (a name, a date, an amount) makes a row data, however sparse it is.
    Only a trailing block of at least 3 rows qualifies, and never every row."""
    rows = [r for r in body if any(r[c] for c in columns if c < len(r))]
    if len(rows) <= _MIN_TEMPLATE_BLOCK:
        return 0
    markers = set(_NA_VALUES)

    def clean(row: list[str]) -> list[str]:
        return [v if v not in markers and not _SHEET_ERROR.match(v) else "" for v in row]

    cleaned = [clean(r) for r in rows]
    fill = {c: sum(1 for r in cleaned if c < len(r) and r[c]) / len(cleaned) for c in columns}
    content = [c for c, share in fill.items() if share < _CONTENT_FILL]
    if not content:
        return 0
    density = [sum(1 for c in content if c < len(r) and r[c]) for r in cleaned]
    full = sorted(density)[int(0.9 * (len(density) - 1))]  # a dense, typical full row
    limit = _TEMPLATE_DENSITY * full
    count = 0
    for value in reversed(density):
        if value > limit:
            break
        count += 1
    tail = cleaned[: -count - 1 : -1] if count else []
    count = _block_with_notes(tail, columns)
    if count < _MIN_TEMPLATE_BLOCK or count >= len(rows):
        return 0
    return count


def _block_with_notes(tail: list[list[str]], columns: list[int]) -> int:
    """The generated block of ``tail`` (last sheet row first), plus a few lone notes below
    it: a row with one cell under a proven template block is outside the table."""
    for notes in range(_MAX_NOTE_ROWS + 1):
        if notes and sum(bool(v) for v in tail[notes - 1 : notes][0]) != 1:
            return 0
        block = _generated_block(tail[notes:], columns)
        if block:
            return block + notes
        if notes == len(tail):
            return 0
    return 0


def detect_layout(
    raw: pd.DataFrame, header_index: int | None = None, skip_template_rows: bool = True
) -> TableLayout:
    """Find the header row, the main table, and trailing template rows of ``raw`` (cells
    as read with ``header=None``). ``header_index`` (0-based) overrides the detection."""
    cells = [[_cell(v) for v in row] for row in raw.itertuples(index=False, name=None)]
    if not cells:
        return TableLayout(header_index=0, chosen=header_index is not None, columns=[])
    chosen = header_index is not None
    header = min(header_index, len(cells) - 1) if chosen else _header_index(cells)
    columns, ignored = _main_columns(cells, header)
    header_cells = cells[header]
    labels = [
        header_cells[c] if c < len(header_cells) and header_cells[c] else f"column {c + 1}"
        for c in ignored
    ]
    templates = _template_rows(cells[header + 1 :], columns) if skip_template_rows else 0
    above, table_like = _rows_above(cells, header)
    return TableLayout(header, chosen, columns, ignored, labels, templates, above, table_like)


def apply_layout(raw: pd.DataFrame, layout: TableLayout) -> pd.DataFrame:
    """The table described by ``layout``: header labels as written, body rows without fully
    empty rows and without the trailing template rows."""
    if raw.empty or not layout.columns:
        return pd.DataFrame()
    headers = layout_headers(raw, layout)
    body = raw.iloc[layout.header_index + 1 :, layout.columns].copy()
    # Whitespace counts as empty, as in the layout detection, or the template cut would shift.
    filled = [any(_cell(v) for v in row) for row in body.itertuples(index=False, name=None)]
    body = body.loc[pd.Series(filled, index=body.index, dtype=bool)]
    body.columns = headers
    if layout.template_rows:
        body = body.iloc[: len(body) - layout.template_rows]
    return body.reset_index(drop=True)


# ------------------------------------------------------------------ reading tables
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


_RAGGED = (
    "Some rows have more values than the header row (for example an extra delimiter at "
    "the end of a line). Fix those rows and upload again."
)


def _csv_raw(path: Path, nrows: int | None = None) -> tuple[pd.DataFrame, list[int]]:
    """The CSV as text cells (header row included) and the field count of each row.

    Read with the csv module so rows may differ in width: title rows above the header are
    often shorter than the table (smart import). NA markers become missing, as before.
    """
    sample, encoding = _read_text(path)
    if not sample.strip():
        raise UploadError("The file is empty.")
    text = path.read_bytes().decode(encoding)
    # strict: without it, an unmatched quote silently swallows the rest of the file.
    reader = csv.reader(
        io.StringIO(text, newline=""), delimiter=_sniff_delimiter(sample), strict=True
    )
    try:
        rows = list(itertools.islice(reader, nrows))
    except csv.Error as exc:
        raise UploadError(
            'The file could not be parsed as a table. Check for a cell with an unmatched quote (").'
        ) from exc
    lengths = [len(r) for r in rows]
    width = max(lengths, default=0)
    # Only empty cells are missing here; NA markers ("NA", "-", ...) are applied to data
    # rows later (blank_na), so a header written "NA" keeps its name.
    data = [[None if v == "" else v for v in r] + [None] * (width - len(r)) for r in rows]
    return pd.DataFrame(data, columns=range(width), dtype="string"), lengths


def _xlsx_raw(path: Path, sheet: str | None, nrows: int | None = None) -> pd.DataFrame:
    names = [s.name for s in list_sheets(path)]
    if not names:
        raise UploadError("The workbook has no sheets.")
    target = sheet or names[0]
    if target not in names:
        raise UploadError(f"Sheet '{target}' was not found in the workbook.")
    return pd.read_excel(
        path, sheet_name=target, engine="calamine", dtype="string", header=None, nrows=nrows
    )


def _raw_table(
    path: Path, file_type: FileType, sheet: str | None, nrows: int | None = None
) -> tuple[pd.DataFrame, list[int] | None]:
    try:
        if file_type == "csv":
            return _csv_raw(path, nrows)
        return _xlsx_raw(path, sheet, nrows), None
    except UploadError:
        raise
    except (pd.errors.ParserError, pd.errors.EmptyDataError, ValueError) as exc:
        raise UploadError("The file could not be parsed as a table.") from exc


def sample_rows(max_rows: int | None, header_row: int | None) -> int | None:
    """Rows to read for a sample: enough to find the header, plus ``max_rows`` data rows."""
    if max_rows is None:
        return None
    return (header_row or HEADER_SCAN_ROWS) + max_rows + 1


def load_table(
    path: Path,
    file_type: FileType,
    sheet: str | None,
    max_rows: int | None = None,
    header_row: int | None = None,
    skip_template_rows: bool = True,
) -> tuple[pd.DataFrame, TableLayout]:
    """Read a CSV or one Excel sheet with every value as text, finding the table inside it.

    Args:
        path: Location of the stored upload.
        file_type: ``"csv"`` or ``"xlsx"``.
        sheet: Worksheet name for Excel files (first sheet when None).
        max_rows: Read only the first data rows (a sample); None reads all. Template rows
            are only detected on a full read.
        header_row: 1-based header row chosen by the user; None detects it.
        skip_template_rows: Drop trailing template rows (row numbers / formula defaults).

    Raises:
        UploadError: If the file is empty, unreadable, ragged, or the sheet is missing.

    Returns:
        The table (labels are the raw header cells, possibly repeated or "") and the
        layout that was used.
    """
    raw, lengths = _raw_table(path, file_type, sheet, sample_rows(max_rows, header_row))
    layout = detect_layout(
        raw,
        header_index=None if header_row is None else header_row - 1,
        skip_template_rows=skip_template_rows and max_rows is None,
    )
    if lengths and any(
        n > lengths[layout.header_index] for n in lengths[layout.header_index + 1 :]
    ):
        raise UploadError(_RAGGED)
    table = apply_layout(raw, layout)
    df = _tidy(blank_na(table) if file_type == "csv" else table)
    if df.empty or len(df.columns) == 0:
        raise UploadError("The file contains no data rows.")
    return (df.head(max_rows) if max_rows is not None else df), layout


def load_dataframe(
    path: Path,
    file_type: FileType,
    sheet: str | None,
    max_rows: int | None = None,
    header_row: int | None = None,
    skip_template_rows: bool = True,
) -> pd.DataFrame:
    """The table of a CSV or one Excel sheet (see :func:`load_table`)."""
    return load_table(path, file_type, sheet, max_rows, header_row, skip_template_rows)[0]


def read_layout(
    path: Path, file_type: FileType, sheet: str | None, header_row: int | None = None
) -> tuple[list[str], TableLayout]:
    """Header labels as written and the layout, from the first rows only."""
    raw, _ = _raw_table(path, file_type, sheet, sample_rows(500, header_row))
    layout = detect_layout(
        raw,
        header_index=None if header_row is None else header_row - 1,
        skip_template_rows=False,
    )
    return layout_headers(raw, layout), layout


def layout_headers(raw: pd.DataFrame, layout: TableLayout) -> list[str]:
    """Header labels as written, for the columns the layout keeps."""
    if raw.empty:
        return []
    return [_header_label(raw.iat[layout.header_index, c]) for c in layout.columns]


def read_headers(
    path: Path, file_type: FileType, sheet: str | None, header_row: int | None = None
) -> list[str]:
    """Return the header row as written (before any normalization)."""
    return read_layout(path, file_type, sheet, header_row)[0]


def read_csv_cells(path: Path) -> pd.DataFrame:
    """Every cell of a CSV as text (header rows included), positional columns."""
    return _csv_raw(path)[0]


def read_samples(
    path: Path, file_type: FileType, max_rows: int, header_row: int | None = None
) -> dict[str | None, pd.DataFrame]:
    """The first rows of every sheet (key: sheet name; ``None`` for a CSV), header rows
    included, as text cells with positional columns. Every Excel sheet is parsed once, in
    a single ``read_excel`` call; the caller finds the table with :func:`detect_layout`."""
    nrows = sample_rows(max_rows, header_row)
    if file_type == "csv":
        return {None: _csv_raw(path, nrows)[0]}
    raws = pd.read_excel(
        path, sheet_name=None, engine="calamine", dtype="string", header=None, nrows=nrows
    )
    return {name: raw.set_axis(range(raw.shape[1]), axis=1) for name, raw in raws.items()}


def blank_na(frame: pd.DataFrame) -> pd.DataFrame:
    """CSV data cells written as an NA marker ("NA", "N/A", "null", "-", ...) are missing."""
    markers = [v for v in _NA_VALUES if v]
    return frame.mask(frame.isin(markers))
