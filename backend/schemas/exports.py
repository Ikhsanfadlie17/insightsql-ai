"""Export formats and the context written next to an exported result."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from backend.schemas.analytics import Period

ExportFormat = Literal["csv", "xlsx", "pdf", "sql"]


class ExportMeta(BaseModel):
    """What a reader of the file needs to interpret the numbers."""

    title: str
    question: str | None = None
    dataset_name: str
    generated_at: datetime
    periods: list[Period] = Field(default_factory=list)
    definitions: dict[str, str] = Field(default_factory=dict)
    sql: str | None = None
    assumptions: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)  # data-quality warnings
