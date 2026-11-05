"""FastAPI application factory."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from backend.api import (
    analyses,
    client_analytics,
    conversations,
    datasets,
    exports,
    health,
    usage,
)
from backend.api import (
    settings as settings_api,
)
from backend.core.config import Settings, get_settings
from backend.core.errors import register_error_handlers
from backend.core.logging import setup_logging
from backend.core.redaction import register_secret
from backend.repositories.dataset_repo import DatasetRepository
from backend.repositories.history_repo import HistoryRepository
from backend.repositories.usage_repo import UsageRepository
from backend.services.analysis_orchestrator import AnalysisOrchestrator
from backend.services.analytics_service import AnalyticsService, DatasetAnalytics
from backend.services.dataset_service import DatasetService
from backend.services.insight_generator import InsightGenerator
from backend.services.llm.client import LLMClient
from backend.services.llm.cost import load_pricing
from backend.services.llm.gateway import LLMGateway
from backend.services.llm.openai_client import OpenAIClient
from backend.services.llm.prompts import PROMPT_VERSION
from backend.services.mapping_service import MappingService
from backend.services.query_executor import QueryExecutor
from backend.services.schema_context import SchemaContextBuilder
from backend.services.sql_generator import TextToSQLService

logger = logging.getLogger(__name__)


async def _cleanup_uploads(service: DatasetService) -> None:
    """Remove stale raw uploads off the event loop; a failure is logged, never raised."""
    try:
        removed = await asyncio.to_thread(service.cleanup_stale_uploads)
    except Exception:
        logger.exception("Stale-upload cleanup failed")
        return
    if removed:
        logger.info("Removed %d stale uploads", removed)


async def _cleanup_uploads_forever(service: DatasetService, interval_s: float) -> None:
    """Phase 2 review M4: clean up while running, not only at startup."""
    while True:
        await asyncio.sleep(interval_s)
        await _cleanup_uploads(service)


def create_app(settings: Settings | None = None, llm_client: LLMClient | None = None) -> FastAPI:
    """Build the API application.

    Args:
        settings: Optional settings override (used by tests); defaults to env settings.
        llm_client: Optional LLM client override (tests use a fake); by default an
            OpenAI client when an API key and model are configured.
    """
    settings = settings or get_settings()
    setup_logging(settings.log_level)
    if settings.openai_api_key is not None:
        register_secret(settings.openai_api_key.get_secret_value())

    repo = DatasetRepository(settings.metadata_db_path)
    dataset_service = DatasetService(settings, repo)
    mapping_service = MappingService(dataset_service, repo)
    usage_repo = UsageRepository(settings.metadata_db_path)
    history_repo = HistoryRepository(settings.metadata_db_path)
    if llm_client is None and settings.llm_configured:
        llm_client = OpenAIClient(settings)
    pricing = load_pricing(settings.pricing_file)
    llm_gateway = LLMGateway(
        llm_client,
        settings,
        usage_repo,
        pricing,
        prompt_version=PROMPT_VERSION,
    )
    executor = QueryExecutor(settings)
    analytics = DatasetAnalytics(dataset_service, repo, AnalyticsService(executor))
    contexts = SchemaContextBuilder(dataset_service, repo, settings)
    text_to_sql = TextToSQLService(
        datasets=dataset_service,
        contexts=contexts,
        gateway=llm_gateway,
        executor=executor,
        settings=settings,
    )

    @asynccontextmanager
    async def lifespan(app_: FastAPI) -> AsyncIterator[None]:
        interrupted = dataset_service.recover_interrupted_imports()
        if interrupted:
            logger.warning("Marked %d interrupted imports as failed", interrupted)
        service = app_.state.dataset_service
        await _cleanup_uploads(service)
        task = asyncio.create_task(
            _cleanup_uploads_forever(service, settings.upload_cleanup_interval_minutes * 60)
        )
        try:
            yield
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    app = FastAPI(title="InsightSQL AI", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.pricing = pricing
    app.state.dataset_service = dataset_service
    app.state.mapping_service = mapping_service
    app.state.dataset_repo = repo
    app.state.usage_repo = usage_repo
    app.state.analytics = analytics
    app.state.llm_gateway = llm_gateway
    app.state.text_to_sql = text_to_sql
    app.state.history_repo = history_repo
    app.state.orchestrator = AnalysisOrchestrator(
        datasets=dataset_service,
        contexts=contexts,
        text_to_sql=text_to_sql,
        analytics=analytics,
        insights=InsightGenerator(llm_gateway, settings),
        history=history_repo,
        usage=usage_repo,
        gateway=llm_gateway,
        settings=settings,
    )
    register_error_handlers(app)
    app.include_router(health.router)
    app.include_router(datasets.router)
    app.include_router(usage.router)
    app.include_router(client_analytics.router)
    app.include_router(analyses.router)
    app.include_router(conversations.router)
    app.include_router(exports.router)
    app.include_router(settings_api.router)
    return app


app = create_app()
