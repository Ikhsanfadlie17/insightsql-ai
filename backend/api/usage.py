"""LLM usage endpoints: per-request records and summaries (no prompts, no secrets)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request

from backend.repositories.usage_repo import UsageRepository
from backend.schemas.usage import UsageRecord, UsageSummary

router = APIRouter(prefix="/usage", tags=["usage"])


def _repo(request: Request) -> UsageRepository:
    return request.app.state.usage_repo


@router.get("/summary", response_model=UsageSummary)
def usage_summary(request: Request) -> UsageSummary:
    """Totals overall, per model, and per purpose. Costs are estimates."""
    return _repo(request).summary()


@router.get("/requests", response_model=list[UsageRecord])
def usage_requests(
    request: Request, limit: Annotated[int, Query(ge=1, le=500)] = 50
) -> list[UsageRecord]:
    """Most recent LLM requests, each traceable by id, purpose, dataset, and analysis."""
    return _repo(request).list_requests(limit)
