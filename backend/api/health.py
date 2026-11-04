"""Health endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    """Service status. Never includes secrets."""

    status: str
    llm_configured: bool
    model: str | None


@router.get("/health", response_model=HealthResponse)
def health(request: Request) -> HealthResponse:
    """Report liveness and whether AI features are configured."""
    settings = request.app.state.settings
    return HealthResponse(
        status="ok",
        llm_configured=settings.llm_configured,
        model=settings.openai_model if settings.llm_configured else None,
    )
