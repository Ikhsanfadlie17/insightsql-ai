"""API and domain models for uploads and datasets."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

FileType = Literal["csv", "xlsx"]
DatasetStatus = Literal["processing", "ready", "failed"]
LogicalTypeName = Literal["integer", "numeric", "date", "datetime", "boolean", "text"]


class SheetInfo(BaseModel):
    """One worksheet (or the single table of a CSV) and its data-row count."""

    name: str
    row_count: int


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


class DatasetColumn(BaseModel):
    """A stored column: SQL name, original header, and inferred type."""

    position: int
    name: str
    original_name: str
    logical_type: LogicalTypeName
    failed_conversions: int = 0
