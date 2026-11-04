"""FastAPI application factory."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from backend.api import datasets, health
from backend.core.config import Settings, get_settings
from backend.core.errors import register_error_handlers
from backend.core.logging import setup_logging
from backend.repositories.dataset_repo import DatasetRepository
from backend.services.dataset_service import DatasetService

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the API application.

    Args:
        settings: Optional settings override (used by tests); defaults to env settings.
    """
    settings = settings or get_settings()
    setup_logging(settings.log_level)

    dataset_service = DatasetService(settings, DatasetRepository(settings.metadata_db_path))

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        removed = dataset_service.cleanup_stale_uploads()
        if removed:
            logger.info("Removed %d stale uploads", removed)
        yield

    app = FastAPI(title="InsightSQL AI", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.dataset_service = dataset_service
    register_error_handlers(app)
    app.include_router(health.router)
    app.include_router(datasets.router)
    return app


app = create_app()
