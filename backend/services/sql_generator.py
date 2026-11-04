"""Text-to-SQL pipeline: question -> LLM plan -> validator -> sandboxed executor -> result.

The LLM only proposes a plan. It cannot execute anything: SQL reaches DuckDB only
as a ``ValidatedSQL`` produced by ``validate_sql`` for the active dataset, and the
executor runs it read-only with a timeout and a row limit. When validation or
execution fails, the model may repair its SQL at most ``max_sql_repairs`` times;
the error it sees is sanitized (no data values, no paths). Every LLM call goes
through the gateway, so each one is recorded with tokens, cost, and status.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Literal

from backend.core.config import Settings
from backend.core.errors import (
    AppError,
    QueryExecutionError,
    QueryTimeoutError,
    SQLValidationError,
    ValidationError,
)
from backend.schemas.llm_outputs import HistoryTurn, RetentionParams, SQLPlanOutput
from backend.schemas.query import QueryResult
from backend.services.dataset_service import DatasetService
from backend.services.llm.gateway import LLMGateway
from backend.services.llm.prompts import (
    SQL_SYSTEM_PROMPT,
    build_repair_prompt,
    build_sql_user_prompt,
)
from backend.services.query_executor import QueryExecutor
from backend.services.schema_context import SchemaContextBuilder, render_schema_context
from backend.services.sql_validator import TableSchema, validate_sql

logger = logging.getLogger(__name__)

OutcomeStatus = Literal["answered", "clarification", "unsupported", "retention"]
_TIMEOUT_HINT = " Simplify the query: fewer joins, aggregate before joining, filter early."


@dataclass
class SQLAttempt:
    """One SQL attempt that failed; ``error`` is sanitized."""

    sql: str | None
    error_code: str
    error: str


class SQLGenerationFailed(AppError):
    """No safe, working query could be produced within the repair limit."""

    code = "sql_generation_failed"
    status_code = 422

    def __init__(self, attempts: list[SQLAttempt], message: str | None = None) -> None:
        self.attempts = attempts
        super().__init__(
            message
            or (
                f"I could not write a safe, working query for this question after "
                f"{len(attempts)} attempt(s). Try rephrasing it, or name the columns and "
                "the period you mean."
            )
        )


@dataclass
class TextToSQLOutcome:
    """Result of one question. ``result`` is only set when ``status == "answered"``."""

    status: OutcomeStatus
    plan: SQLPlanOutput
    sql: str | None = None
    result: QueryResult | None = None
    repairs: int = 0
    attempts: list[SQLAttempt] = field(default_factory=list)
    llm_request_ids: list[str] = field(default_factory=list)


def _check_retention(params: RetentionParams | None) -> None:
    """Periods must be valid ISO dates, non-empty, and non-overlapping."""
    try:
        if params is None:
            raise ValueError("missing periods")
        prev_start, prev_end = (
            date.fromisoformat(params.previous_start),
            date.fromisoformat(params.previous_end),
        )
        cur_start, cur_end = (
            date.fromisoformat(params.current_start),
            date.fromisoformat(params.current_end),
        )
        if not (prev_start < prev_end <= cur_start < cur_end):
            raise ValueError("periods out of order")
        if prev_start < date(1900, 1, 1) or cur_end > date(2200, 1, 1):
            raise ValueError("periods out of range")
    except ValueError:
        raise SQLGenerationFailed(
            [],
            "The periods to compare could not be understood. Name both periods, for "
            "example: 'compare 2025 with 2026'.",
        ) from None


class TextToSQLService:
    """Answers one question about one ready dataset."""

    def __init__(
        self,
        datasets: DatasetService,
        contexts: SchemaContextBuilder,
        gateway: LLMGateway,
        executor: QueryExecutor,
        settings: Settings,
    ) -> None:
        self._datasets = datasets
        self._contexts = contexts
        self._gateway = gateway
        self._executor = executor
        self._settings = settings

    def ask(
        self,
        dataset_id: str,
        question: str,
        history: list[HistoryTurn] | None = None,
        analysis_id: str | None = None,
    ) -> TextToSQLOutcome:
        """Plan, validate, execute, and (if needed) repair within the configured limits.

        Raises:
            ValidationError: Empty or too long question, or the dataset is not ready.
            LLMNotConfiguredError / LLMError: Provider unavailable or failing.
            SQLGenerationFailed: No safe, working query within the repair limit.
        """
        question = question.strip()
        if not question:
            raise ValidationError("Please type a question.")
        if len(question) > self._settings.max_question_chars:
            raise ValidationError(
                f"The question is too long (maximum {self._settings.max_question_chars} "
                "characters)."
            )
        self._datasets.require_ready(dataset_id)

        context = self._contexts.build(dataset_id)
        context_text = render_schema_context(context)
        schema = TableSchema(columns={c.name: c.type for c in context.columns})
        turns = [(t.question, t.sql, t.clarification) for t in (history or [])][-5:]
        request_ids: list[str] = []

        def call(purpose: str, user: str) -> SQLPlanOutput:
            response, record = self._gateway.complete(
                purpose=purpose,
                system=SQL_SYSTEM_PROMPT,
                user=user,
                schema=SQLPlanOutput,
                dataset_id=dataset_id,
                analysis_id=analysis_id,
            )
            request_ids.append(record.id)
            return response.parsed

        current = call("sql_generation", build_sql_user_prompt(question, context_text, turns))
        attempts: list[SQLAttempt] = []
        path = self._datasets.duckdb_path(dataset_id)
        max_repairs = self._settings.max_sql_repairs

        for repairs in range(max_repairs + 1):
            if current.intent in ("clarify", "unsupported"):
                status: OutcomeStatus = (
                    "clarification" if current.intent == "clarify" else "unsupported"
                )
                return TextToSQLOutcome(
                    status, current, repairs=repairs, attempts=attempts,
                    llm_request_ids=request_ids,
                )  # fmt: skip
            if current.intent == "retention":
                _check_retention(current.retention)
                return TextToSQLOutcome(
                    "retention", current, repairs=repairs, attempts=attempts,
                    llm_request_ids=request_ids,
                )  # fmt: skip

            sql = current.sql
            try:
                if not sql or not sql.strip():
                    raise SQLValidationError(["empty: the plan did not contain SQL"])
                validated = validate_sql(sql, schema)
                result = self._executor.execute(path, validated)
            except SQLValidationError as error:
                attempt = SQLAttempt(sql, "sql_validation", "; ".join(error.reasons))
            except QueryTimeoutError as error:
                attempt = SQLAttempt(sql, "query_timeout", error.message + _TIMEOUT_HINT)
            except QueryExecutionError as error:
                attempt = SQLAttempt(sql, "query_execution_error", error.message)
            else:
                return TextToSQLOutcome(
                    "answered", current, sql=validated.sql, result=result, repairs=repairs,
                    attempts=attempts, llm_request_ids=request_ids,
                )  # fmt: skip

            attempts.append(attempt)
            logger.info(
                "SQL attempt %d failed (%s) for dataset %s", repairs + 1, attempt.error_code,
                dataset_id,
            )  # fmt: skip
            if repairs == max_repairs:
                break
            current = call(
                "sql_repair", build_repair_prompt(question, context_text, sql, attempt.error)
            )

        raise SQLGenerationFailed(attempts)
