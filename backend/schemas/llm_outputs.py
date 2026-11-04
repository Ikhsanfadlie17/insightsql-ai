"""Structured outputs requested from the LLM (strict JSON schema compatible).

Every field is required (nullable where optional), as OpenAI strict Structured
Outputs demand. Dates are ISO strings; the service validates them.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ChartType = Literal["bar", "line", "area", "pie", "donut", "hbar", "grouped_bar", "kpi", "table"]


class ChartHint(BaseModel):
    """The model's chart suggestion; advisory only (validated before rendering)."""

    type: ChartType
    x: str | None
    y: list[str]


class RetentionParams(BaseModel):
    """Two periods for the retention engine; ends are exclusive (YYYY-MM-DD)."""

    previous_start: str
    previous_end: str
    current_start: str
    current_end: str
    ytd: bool


class SQLPlanOutput(BaseModel):
    """The model's decision for one question."""

    intent: Literal["sql", "retention", "clarify", "unsupported"]
    language: Literal["id", "en"]
    sql: str | None
    retention: RetentionParams | None
    explanation: str
    chart: ChartHint | None
    metrics: list[str]
    assumptions: list[str]
    clarification_question: str | None


class HistoryTurn(BaseModel):
    """A previous turn as given to the model: the question and its SQL, never results."""

    question: str = Field(max_length=2000)
    sql: str | None = None
