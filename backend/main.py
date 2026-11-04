"""FastAPI application factory."""

from __future__ import annotations

from fastapi import FastAPI

from backend.api import health
from backend.core.config import Settings, get_settings
from backend.core.errors import register_error_handlers
from backend.core.logging import setup_logging


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the API application.

    Args:
        settings: Optional settings override (used by tests); defaults to env settings.
    """
    settings = settings or get_settings()
    setup_logging(settings.log_level)

    app = FastAPI(title="InsightSQL AI", version="0.1.0")
    app.state.settings = settings
    register_error_handlers(app)
    app.include_router(health.router)
    return app


app = create_app()
