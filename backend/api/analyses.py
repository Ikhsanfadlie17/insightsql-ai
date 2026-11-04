"""AI Analyst endpoint: ask a question (history endpoints live in ``conversations``)."""

from __future__ import annotations

from fastapi import APIRouter, Request

from backend.schemas.analysis import AnalysisResponse, AskRequest
from backend.services.analysis_orchestrator import AnalysisOrchestrator

router = APIRouter(tags=["analyses"])


def _orchestrator(request: Request) -> AnalysisOrchestrator:
    return request.app.state.orchestrator


@router.post("/analyses/ask", response_model=AnalysisResponse)
def ask(body: AskRequest, request: Request) -> AnalysisResponse:
    """Answer a question about one dataset (503 ``llm_not_configured`` without an API key)."""
    return _orchestrator(request).ask(body.dataset_id, body.conversation_id, body.question)
