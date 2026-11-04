"""FastAPI application factory."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from backend.api import client_analytics, datasets, health, usage
from backend.core.config import Settings, get_settings
from backend.core.errors import register_error_handlers
from backend.core.logging import setup_logging
from backend.repositories.dataset_repo import DatasetRepository
from backend.repositories.usage_repo import UsageRepository
from backend.services.analytics_service import AnalyticsService, DatasetAnalytics
from backend.services.dataset_service import DatasetService
from backend.services.llm.cost import load_pricing
from backend.services.llm.gateway import LLMGateway
from backend.services.llm.openai_client import OpenAIClient
from backend.services.llm.prompts import PROMPT_VERSION
from backend.services.mapping_service import MappingService
from backend.services.query_executor import QueryExecutor
from backend.services.schema_context import SchemaContextBuilder
from backend.services.sql_generator import TextToSQLService

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the API application.

    Args:
        settings: Optional settings override (used by tests); defaults to env settings.
    """
    settings = settings or get_settings()
    setup_logging(settings.log_level)

    repo = DatasetRepository(settings.metadata_db_path)
    dataset_service = DatasetService(settings, repo)
    mapping_service = MappingService(dataset_service, repo)
    usage_repo = UsageRepository(settings.metadata_db_path)
    llm_client = OpenAIClient(settings) if settings.llm_configured else None
    llm_gateway = LLMGateway(
        llm_client,
        settings,
        usage_repo,
        load_pricing(settings.pricing_file),
        prompt_version=PROMPT_VERSION,
    )
    executor = QueryExecutor(settings)
    analytics = DatasetAnalytics(dataset_service, repo, AnalyticsService(executor))
    # Used by the analysis endpoint in Phase 5 (Task 18); no HTTP route yet.
    text_to_sql = TextToSQLService(
        datasets=dataset_service,
        contexts=SchemaContextBuilder(dataset_service, repo, settings),
        gateway=llm_gateway,
        executor=executor,
        settings=settings,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        interrupted = dataset_service.recover_interrupted_imports()
        if interrupted:
            logger.warning("Marked %d interrupted imports as failed", interrupted)
        removed = dataset_service.cleanup_stale_uploads()
        if removed:
            logger.info("Removed %d stale uploads", removed)
        yield

    app = FastAPI(title="InsightSQL AI", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.dataset_service = dataset_service
    app.state.mapping_service = mapping_service
    app.state.dataset_repo = repo
    app.state.usage_repo = usage_repo
    app.state.analytics = analytics
    app.state.llm_gateway = llm_gateway
    app.state.text_to_sql = text_to_sql
    register_error_handlers(app)
    app.include_router(health.router)
    app.include_router(datasets.router)
    app.include_router(usage.router)
    app.include_router(client_analytics.router)
    return app


app = create_app()
