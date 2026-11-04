"""Query history: conversations, single analyses, and recent activity."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request, Response

from backend.repositories.history_repo import HistoryRepository
from backend.schemas.analysis import (
    AnalysisResponse,
    Conversation,
    ConversationSummary,
    RecentAnalysis,
)

router = APIRouter(tags=["history"])


def _history(request: Request) -> HistoryRepository:
    return request.app.state.history_repo


@router.get("/conversations", response_model=list[ConversationSummary])
def list_conversations(
    request: Request,
    dataset_id: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
) -> list[ConversationSummary]:
    """Conversations newest first, of one dataset or (without ``dataset_id``) of all."""
    if dataset_id is not None:
        request.app.state.dataset_service.get(dataset_id)  # 404 for unknown datasets
    return _history(request).list_conversations(dataset_id)


@router.get("/conversations/{conversation_id}", response_model=Conversation)
def get_conversation(conversation_id: str, request: Request) -> Conversation:
    """One conversation with all of its analyses."""
    return _history(request).get_conversation(conversation_id)


@router.delete("/conversations/{conversation_id}", status_code=204)
def delete_conversation(conversation_id: str, request: Request) -> Response:
    """Delete a conversation and all of its analyses."""
    _history(request).delete_conversation(conversation_id)
    return Response(status_code=204)


@router.get("/analyses/recent", response_model=list[RecentAnalysis])
def recent_analyses(
    request: Request, limit: Annotated[int, Query(ge=1, le=50)] = 5
) -> list[RecentAnalysis]:
    """Newest analyses across all datasets (question and status only)."""
    return _history(request).recent_analyses(limit)


@router.get("/analyses/{analysis_id}", response_model=AnalysisResponse)
def get_analysis(analysis_id: str, request: Request) -> AnalysisResponse:
    """One stored analysis with its SQL, result, and insight."""
    return _history(request).get_analysis(analysis_id)
