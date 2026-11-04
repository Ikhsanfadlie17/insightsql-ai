"""FastAPI dependency providers."""

from __future__ import annotations

from fastapi import Request

from backend.services.dataset_service import DatasetService
from backend.services.mapping_service import MappingService


def get_dataset_service(request: Request) -> DatasetService:
    """Return the application's dataset service."""
    return request.app.state.dataset_service


def get_mapping_service(request: Request) -> MappingService:
    """Return the application's column-mapping service."""
    return request.app.state.mapping_service
