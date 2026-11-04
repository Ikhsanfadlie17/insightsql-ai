"""Tabular query results shared by preview, SQL execution, and analytics."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class QueryResult(BaseModel):
    """JSON-safe table: dates as ISO strings, missing values as null."""

    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    truncated: bool = False
    elapsed_ms: float = 0.0
