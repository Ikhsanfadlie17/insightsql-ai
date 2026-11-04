"""AI Analyst endpoints: ask a question, and read or delete conversations."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request, Response

from backend.repositories.history_repo import HistoryRepository
from backend.schemas.analysis import (
    AnalysisResponse,
    AskRequest,
    Conversation,
    ConversationSummary,
)
from backend.services.analysis_orchestrator import AnalysisOrchestrator

router = APIRouter(tags=["analyses"])


def _orchestrator(request: Request) -> AnalysisOrchestrator:
    return request.app.state.orchestrator


def _history(request: Request) -> HistoryRepository:
    return request.app.state.history_repo


@router.post("/analyses/ask", response_model=AnalysisResponse)
def ask(body: AskRequest, request: Request) -> AnalysisResponse:
    """Answer a question about one dataset (503 ``llm_not_configured`` without an API key)."""
    return _orchestrator(request).ask(body.dataset_id, body.conversation_id, body.question)


@router.get("/conversations", response_model=list[ConversationSummary])
def list_conversations(
    request: Request, dataset_id: Annotated[str, Query(min_length=1, max_length=64)]
) -> list[ConversationSummary]:
    """Conversations of one dataset, newest first."""
    request.app.state.dataset_service.get(dataset_id)  # 404 for unknown datasets
    return _history(request).list_conversations(dataset_id)


@router.get("/conversations/{conversation_id}", response_model=Conversation)
def get_conversation(conversation_id: str, request: Request) -> Conversation:
    """One conversation with all of its analyses."""
    return _history(request).get_conversation(conversation_id)


@router.delete("/conversations/{conversation_id}", status_code=204)
def delete_conversation(conversation_id: str, request: Request) -> Response:
    _history(request).delete_conversation(conversation_id)
    return Response(status_code=204)
