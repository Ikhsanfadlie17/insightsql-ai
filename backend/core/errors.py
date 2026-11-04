"""Domain exceptions and their mapping to safe HTTP error responses."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


class AppError(Exception):
    """Base error whose message is safe to show to API clients."""

    code: str = "app_error"
    status_code: int = 400

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class UploadError(AppError):
    """The uploaded file is missing, too large, or not a supported format."""

    code = "upload_error"
    status_code = 400


class NotFoundError(AppError):
    """A requested resource does not exist."""

    code = "not_found"
    status_code = 404


class ValidationError(AppError):
    """A request is well-formed but semantically invalid."""

    code = "validation_error"
    status_code = 422


class SQLValidationError(AppError):
    """Generated SQL broke one or more security rules."""

    code = "sql_validation_error"
    status_code = 422

    def __init__(self, reasons: list[str]) -> None:
        self.reasons = reasons
        super().__init__("SQL rejected: " + "; ".join(reasons))


class QueryTimeoutError(AppError):
    """A query exceeded the configured execution time."""

    code = "query_timeout"
    status_code = 408


class QueryExecutionError(AppError):
    """DuckDB failed to execute a validated query."""

    code = "query_execution_error"
    status_code = 400


class LLMNotConfiguredError(AppError):
    """AI features were requested but no API key / model is configured."""

    code = "llm_not_configured"
    status_code = 503


class LLMError(AppError):
    """The LLM provider failed or returned an unusable response."""

    code = "llm_error"
    status_code = 502


def _error_body(code: str, message: str) -> dict[str, dict[str, str]]:
    return {"error": {"code": code, "message": message}}


def register_error_handlers(app: FastAPI) -> None:
    """Attach handlers that turn exceptions into ``{"error": {...}}`` responses."""

    @app.exception_handler(AppError)
    async def handle_app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=_error_body(exc.code, exc.message))

    @app.exception_handler(Exception)
    async def handle_unexpected(_: Request, exc: Exception) -> JSONResponse:
        # Details go to the server log only; clients get a generic message.
        logger.exception("Unhandled error", exc_info=exc)
        return JSONResponse(
            status_code=500,
            content=_error_body("internal_error", "An unexpected error occurred."),
        )
