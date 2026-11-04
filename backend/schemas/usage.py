"""LLM request records and usage summaries."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, computed_field

from backend.services.llm.cost import CostStatus


class UsageRecord(BaseModel):
    """One LLM request (successful or not). Never contains prompts or answers."""

    id: str
    created_at: datetime
    purpose: str
    dataset_id: str | None = None
    analysis_id: str | None = None
    model: str
    status: Literal["success", "error"]
    attempts: int
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_tokens: int | None = None
    est_cost_usd: float | None = None
    cost_status: CostStatus
    latency_ms: float
    error_code: str | None = None
    error_message: str | None = None
    prompt_version: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def retry_count(self) -> int:
        """Retries after the first attempt."""
        return max(self.attempts - 1, 0)


class UsageTotals(BaseModel):
    """Aggregated counts. ``estimated_cost_usd`` sums only requests with an estimate."""

    key: str = "all"
    requests: int = 0
    succeeded: int = 0
    failed: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    estimated_cost_usd: float = 0.0
    requests_without_cost: int = 0


class UsageSummary(BaseModel):
    """Totals overall, per model, and per purpose."""

    currency: str = "USD"
    totals: UsageTotals
    by_model: list[UsageTotals]
    by_purpose: list[UsageTotals]
