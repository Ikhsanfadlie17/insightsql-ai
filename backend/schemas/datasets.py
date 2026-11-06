"""API and domain models for uploads and datasets."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

FileType = Literal["csv", "xlsx"]
DatasetStatus = Literal["processing", "ready", "failed"]
LogicalTypeName = Literal["integer", "numeric", "date", "datetime", "boolean", "text"]
# "plain" = canonical numbers (Excel cells); "id" = 1.234,5; "en" = 1,234.5.
NumberFormat = Literal["auto", "id", "en", "plain"]
DateOrder = Literal["auto", "day_first", "month_first"]


class ColumnNote(BaseModel):
    """A user-facing explanation of how a column was read (never silent changes)."""

    level: Literal["info", "warning"]
    code: str
    message: str


class SheetInfo(BaseModel):
    """One worksheet (or the single table of a CSV) and its data-row count."""

    name: str
    row_count: int
    columns: list[str] = Field(default_factory=list)  # header row as written
    # Type each header would get at import, from a sample of rows (Task 21: warn before
    # a date or number column is chosen under "Keep as text").
    detected_types: dict[str, LogicalTypeName] = Field(default_factory=dict)
    # Smart import: where the table is inside the sheet (title rows, side tables).
    header_row: int = 1  # 1-based
    header_detected: bool = True  # False when the user chose the header row
    ignored_columns: list[str] = Field(default_factory=list)  # side tables / helper columns
    template_rows: int = 0  # trailing template rows the import will skip (whole sheet read)
    rows_above_header: int = 0  # non-empty rows above the header, not imported
    table_like_rows_above: int = 0  # of those, rows about as wide as the header


class UploadInspection(BaseModel):
    """Result of inspecting an upload before ingestion."""

    upload_id: str
    filename: str
    file_type: FileType
    sheets: list[SheetInfo]


class ImportLayout(BaseModel):
    """How the table was found inside the file (smart import), shown to the user."""

    header_row: int  # 1-based row used as the header
    header_chosen: bool  # True when the user picked it, False when detected
    ignored_columns: list[str] = Field(default_factory=list)
    template_rows_skipped: int = 0
    rows_above_header: int = 0
    table_like_rows_above: int = 0


class DatasetRecord(BaseModel):
    """A dataset (one CSV or one Excel sheet) and its ingestion state."""

    id: str
    name: str
    source_filename: str
    sheet_name: str | None
    status: DatasetStatus
    error: str | None = None
    row_count: int | None = None
    column_count: int | None = None
    created_at: datetime
    number_format: NumberFormat = "auto"
    date_order: DateOrder = "auto"
    text_columns: list[str] = Field(default_factory=list)  # original headers kept as text
    header_row: int | None = None  # requested header row; None = detect
    skip_template_rows: bool = True
    layout: ImportLayout | None = None  # filled in at ingest


class DatasetColumn(BaseModel):
    """A stored column: SQL name, original header, and inferred type."""

    position: int
    name: str
    original_name: str
    logical_type: LogicalTypeName
    failed_conversions: int = 0
    notes: list[ColumnNote] = Field(default_factory=list)
