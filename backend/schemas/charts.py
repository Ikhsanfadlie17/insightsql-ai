"""Chart specifications. A spec only names columns of a real result; it holds no data."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

SpecType = Literal["bar", "line", "area", "pie", "donut", "hbar", "grouped_bar", "kpi"]
ColumnKind = Literal["number", "date", "period", "text", "empty"]


class ChartSpec(BaseModel):
    """What to draw from a ``QueryResult``: x is a category or time column, y are numbers."""

    type: SpecType
    x: str | None = None
    y: list[str] = Field(default_factory=list)
    color: str | None = None
    title: str = ""
    top_n: int | None = None  # hbar: only the largest N categories are drawn
