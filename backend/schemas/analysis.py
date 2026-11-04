"""AI Analyst request/response models and conversation history."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.schemas.analytics import RetentionResult
from backend.schemas.charts import ChartSpec
from backend.schemas.datasets import ColumnNote
from backend.schemas.insights import Insight
from backend.schemas.query import QueryResult

AnalysisStatus = Literal["answered", "clarification", "unsupported", "error"]
KPIFormat = Literal["int", "number", "pct", "signed_pct", "plain"]


class AskRequest(BaseModel):
    """``POST /analyses/ask``."""

    model_config = ConfigDict(extra="forbid")

    dataset_id: str = Field(min_length=1, max_length=64)
    conversation_id: str | None = Field(default=None, max_length=64)
    question: str = Field(max_length=4000)


class KPI(BaseModel):
    """One headline number taken from the result (``None`` renders as N/A)."""

    label: str
    value: float | int | None
    format: KPIFormat = "number"
    definition: str | None = None


class AnalysisUsage(BaseModel):
    """LLM requests made for one analysis (estimates; ``None`` cost = not estimable)."""

    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    est_cost_usd: float | None = None
    cost_complete: bool = True  # False when some request has no price or no usage
    request_ids: list[str] = Field(default_factory=list)


class AnalysisResponse(BaseModel):
    """Everything the AI Analyst shows for one question."""

    analysis_id: str
    conversation_id: str
    dataset_id: str
    question: str
    status: AnalysisStatus
    language: Literal["id", "en"] = "en"
    answer: str
    kpis: list[KPI] = Field(default_factory=list)
    chart: ChartSpec | None = None
    table: QueryResult | None = None
    sql: str | None = None
    explanation: str = ""
    assumptions: list[str] = Field(default_factory=list)
    insight: Insight | None = None
    retention: RetentionResult | None = None
    warnings: list[ColumnNote] = Field(default_factory=list)
    error_code: str | None = None
    repairs: int = 0
    usage: AnalysisUsage = Field(default_factory=AnalysisUsage)
    created_at: datetime


class ConversationSummary(BaseModel):
    id: str
    dataset_id: str
    title: str
    created_at: datetime
    analysis_count: int = 0


class Conversation(ConversationSummary):
    analyses: list[AnalysisResponse] = Field(default_factory=list)
