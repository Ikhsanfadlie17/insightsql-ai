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


class UploadInspection(BaseModel):
    """Result of inspecting an upload before ingestion."""

    upload_id: str
    filename: str
    file_type: FileType
    sheets: list[SheetInfo]


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


class DatasetColumn(BaseModel):
    """A stored column: SQL name, original header, and inferred type."""

    position: int
    name: str
    original_name: str
    logical_type: LogicalTypeName
    failed_conversions: int = 0
    notes: list[ColumnNote] = Field(default_factory=list)
