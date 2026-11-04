"""FastAPI dependency providers."""

from __future__ import annotations

from fastapi import Request

from backend.services.dataset_service import DatasetService


def get_dataset_service(request: Request) -> DatasetService:
    """Return the application's dataset service."""
    return request.app.state.dataset_service
