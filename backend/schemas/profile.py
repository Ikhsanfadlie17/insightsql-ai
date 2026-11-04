"""Dataset profile models (computed in DuckDB, stored as JSON)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from backend.schemas.datasets import ColumnNote, LogicalTypeName


class ValueCount(BaseModel):
    """A frequent value and how many rows have it."""

    value: str
    count: int


class ColumnProfile(BaseModel):
    """Statistics for one column. Fields that do not apply to the type are None."""

    name: str
    original_name: str
    logical_type: LogicalTypeName
    null_count: int
    null_pct: float
    unique_count: int
    top_values: list[ValueCount] = Field(default_factory=list)
    min: float | None = None
    max: float | None = None
    mean: float | None = None
    median: float | None = None
    std: float | None = None
    date_min: str | None = None
    date_max: str | None = None
    notes: list[ColumnNote] = Field(default_factory=list)


class DatasetProfile(BaseModel):
    """Whole-dataset quality summary plus per-column statistics."""

    row_count: int
    column_count: int
    duplicate_row_count: int
    missing_cells: int
    missing_pct: float
    columns: list[ColumnProfile]
    notes: list[ColumnNote] = Field(default_factory=list)
    generated_at: datetime
