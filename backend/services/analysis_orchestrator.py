"""AI Analyst pipeline: question -> plan -> (validated SQL | retention templates) -> chart,
KPIs, grounded insight -> persisted analysis.

* Conversations belong to one dataset. A conversation id from another dataset is
  rejected, so context never leaks between datasets; a new dataset starts a new
  conversation.
* Follow-ups get the last 3 turns of the same conversation: questions, SQL, and the
  clarifying question asked, never result rows.
* Retention questions are routed to the deterministic analytics engine; the LLM only
  supplies the periods. Every number shown comes from an executed query.
* Every analysis (also errors and clarifications) is stored with its SQL and LLM usage.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, date, datetime, timedelta

from backend.core.config import Settings
from backend.core.errors import (
    AppError,
    LLMError,
    LLMNotConfiguredError,
    MappingRequiredError,
    ValidationError,
)
from backend.repositories.history_repo import HistoryRepository
from backend.repositories.usage_repo import UsageRepository
from backend.schemas.analysis import KPI, AnalysisResponse, AnalysisUsage
from backend.schemas.analytics import Period, RetentionOptions, RetentionResult
from backend.schemas.datasets import ColumnNote
from backend.schemas.llm_outputs import RetentionParams, SQLPlanOutput
from backend.schemas.query import QueryResult
from backend.services.analytics_service import DatasetAnalytics
from backend.services.dataset_service import DatasetService
from backend.services.insight_generator import InsightGenerator
from backend.services.llm.gateway import LLMGateway
from backend.services.schema_context import SchemaContextBuilder
from backend.services.sql_generator import SQLGenerationFailed, TextToSQLService
from backend.services.visualization_service import recommend_chart, validate_chart_spec

logger = logging.getLogger(__name__)

HISTORY_TURNS = 3
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_RATE_WORDS = ("rate", "ratio", "rasio")


def _day(value: date) -> str:
    return f"{value.day} {_MONTHS[value.month - 1]} {value.year}"


def period_from(start: date, end: date) -> Period:
    """A readable period for ``[start, end)``: '2025', '2026 (1 Jan–31 Oct)', or a range."""
    last = end - timedelta(days=1)
    if (start.month, start.day) == (1, 1) and end == date(start.year + 1, 1, 1):
        label = str(start.year)
    elif (start.month, start.day) == (1, 1) and last.year == start.year:
        label = f"{start.year} (1 Jan–{last.day} {_MONTHS[last.month - 1]})"
    else:
        label = f"{_day(start)} – {_day(last)}"
    return Period(label=label, start=start, end=end)


def _periods(params: RetentionParams) -> tuple[Period, Period]:
    iso = date.fromisoformat
    return (
        period_from(iso(params.previous_start), iso(params.previous_end)),
        period_from(iso(params.current_start), iso(params.current_end)),
    )


def _retention_kpis(r: RetentionResult) -> list[KPI]:
    d = r.definitions
    p, c = r.previous.label.split(" (")[0], r.current.label.split(" (")[0]
    return [
        KPI(label=f"Active in {p}", value=r.previous_active, format="int",
            definition=d.get("previous_active")),
        KPI(label=f"Active in {c}", value=r.current_active, format="int",
            definition=d.get("current_active")),
        KPI(label="Retention rate", value=r.retention_rate, format="pct",
            definition=d.get("retention_rate")),
        KPI(label="Client growth", value=r.growth_rate, format="signed_pct",
            definition=d.get("growth_rate")),
        KPI(label="New", value=r.new, format="int", definition=d.get("new")),
        KPI(label="Returning", value=r.returning, format="int", definition=d.get("returning")),
        KPI(label="Reactivated", value=r.reactivated, format="int",
            definition=d.get("reactivated")),
        KPI(label="Between-period", value=r.between_period, format="int",
            definition=d.get("between_period")),
        KPI(label="Not returned", value=r.not_returned, format="int",
            definition=d.get("not_returned")),
        KPI(label="Existing active", value=r.existing_active, format="int",
            definition=d.get("existing_active")),
    ]  # fmt: skip


def _result_kpis(result: QueryResult) -> list[KPI]:
    """Headline numbers for a one-row result of 1-4 numbers."""
    if result.row_count != 1 or not result.rows:
        return []
    row = result.rows[0]
    numbers = [
        (name, value)
        for name, value in zip(result.columns, row, strict=False)
        if isinstance(value, int | float) and not isinstance(value, bool) or value is None
    ]
    if not numbers or len(numbers) != len(row) or len(numbers) > 4:
        return []
    kpis = []
    for name, value in numbers:
        if isinstance(value, int):
            fmt = "int"
        elif (
            value is not None and any(w in name.lower() for w in _RATE_WORDS) and abs(value) <= 1.5
        ):
            fmt = "pct"
        else:
            fmt = "number"
        kpis.append(KPI(label=name.replace("_", " ").capitalize(), value=value, format=fmt))
    return kpis


class AnalysisOrchestrator:
    """Answers one question in a dataset-bound conversation and stores the analysis."""

    def __init__(
        self,
        *,
        datasets: DatasetService,
        contexts: SchemaContextBuilder,
        text_to_sql: TextToSQLService,
        analytics: DatasetAnalytics,
        insights: InsightGenerator,
        history: HistoryRepository,
        usage: UsageRepository,
        gateway: LLMGateway,
        settings: Settings,
    ) -> None:
        self._datasets = datasets
        self._contexts = contexts
        self._text_to_sql = text_to_sql
        self._analytics = analytics
        self._insights = insights
        self._history = history
        self._usage = usage
        self._gateway = gateway
        self._settings = settings

    def ask(self, dataset_id: str, conversation_id: str | None, question: str) -> AnalysisResponse:
        """Answer ``question``.

        Raises (nothing is stored in these cases):
            ValidationError: empty/too long question, dataset not ready, or a conversation
                of another dataset.
            NotFoundError: unknown dataset or conversation.
            LLMNotConfiguredError: no API key / model configured.
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
        if conversation_id is not None:
            conversation = self._history.get_summary(conversation_id)
            if conversation.dataset_id != dataset_id:
                raise ValidationError(
                    "This conversation belongs to another dataset. Start a new conversation "
                    "for this dataset."
                )
        if not self._gateway.configured:
            raise LLMNotConfiguredError(
                "AI features are not configured: set OPENAI_API_KEY and OPENAI_MODEL."
            )
        if conversation_id is None:
            conversation_id = self._history.create_conversation(dataset_id, question).id
            turns = []
        else:
            turns = self._history.recent_turns(conversation_id, HISTORY_TURNS)

        analysis_id = uuid.uuid4().hex
        base = {
            "analysis_id": analysis_id,
            "conversation_id": conversation_id,
            "dataset_id": dataset_id,
            "question": question,
            "created_at": datetime.now(UTC),
        }
        request_ids: list[str] = []
        try:
            response = self._answer(base, dataset_id, question, turns, analysis_id, request_ids)
        except SQLGenerationFailed as error:
            response = AnalysisResponse(
                **base, status="error", answer=error.message, error_code=error.code
            )
            request_ids += self._request_ids_for(analysis_id)
        except (LLMError, MappingRequiredError) as error:
            response = AnalysisResponse(
                **base, status="error", answer=error.message, error_code=error.code
            )
            request_ids += self._request_ids_for(analysis_id)
        except AppError as error:  # e.g. invalid periods from the plan
            response = AnalysisResponse(
                **base, status="error", answer=error.message, error_code=error.code
            )
            request_ids += self._request_ids_for(analysis_id)
        response.usage = self._usage_for(list(dict.fromkeys(request_ids)))
        self._history.add_analysis(response)
        return response

    # -- internals -----------------------------------------------------------------------------
    def _request_ids_for(self, analysis_id: str) -> list[str]:
        return [r.id for r in self._usage.list_requests(50) if r.analysis_id == analysis_id]

    def _usage_for(self, ids: list[str]) -> AnalysisUsage:
        records = self._usage.by_ids(ids)
        costs = [r.est_cost_usd for r in records]
        known = [c for c in costs if c is not None]
        return AnalysisUsage(
            requests=len(records),
            input_tokens=sum(r.input_tokens or 0 for r in records),
            output_tokens=sum(r.output_tokens or 0 for r in records),
            est_cost_usd=sum(known) if known else None,
            cost_complete=len(known) == len(records),
            request_ids=[r.id for r in records],
        )

    def _answer(
        self,
        base: dict,
        dataset_id: str,
        question: str,
        turns: list,
        analysis_id: str,
        request_ids: list[str],
    ) -> AnalysisResponse:
        outcome = self._text_to_sql.ask(dataset_id, question, turns, analysis_id)
        request_ids += outcome.llm_request_ids
        plan: SQLPlanOutput = outcome.plan
        common = {
            **base,
            "language": plan.language,
            "explanation": plan.explanation,
            "assumptions": plan.assumptions,
            "repairs": outcome.repairs,
        }
        if outcome.status == "clarification":
            return AnalysisResponse(
                **common,
                status="clarification",
                answer=plan.clarification_question or plan.explanation,
            )
        if outcome.status == "unsupported":
            return AnalysisResponse(**common, status="unsupported", answer=plan.explanation)
        if outcome.status == "retention":
            return self._retention(common, dataset_id, question, plan, analysis_id, request_ids)
        return self._sql_answer(common, dataset_id, question, plan, outcome, analysis_id,
                                request_ids)  # fmt: skip

    def _retention(self, common, dataset_id, question, plan, analysis_id, request_ids):
        previous, current = _periods(plan.retention)
        r = self._analytics.compare(dataset_id, previous, current, RetentionOptions())
        rows = [
            [label, getattr(r, key)]
            for key, label in (("returning", "Returning"), ("new", "New"),
                               ("reactivated", "Reactivated"),
                               ("between_period", "Between-period"))
        ]  # fmt: skip
        table = QueryResult(columns=["category", "clients"], rows=rows, row_count=len(rows))
        chart = recommend_chart(table, hint=None, title=f"Active clients in {current.label}")
        donut = chart.model_copy(update={"type": "donut"}) if chart else None
        if donut is not None and validate_chart_spec(donut, table):
            chart = donut
        insight = self._insights.generate(
            question=question,
            language=plan.language,
            retention=r,
            dataset_id=dataset_id,
            analysis_id=analysis_id,
        )
        if insight.llm_request_id:
            request_ids.append(insight.llm_request_id)
        return AnalysisResponse(
            **common,
            status="answered",
            answer=insight.summary,
            kpis=_retention_kpis(r),
            chart=chart,
            table=table,
            sql=r.sql[0] if r.sql else None,
            insight=insight,
            retention=r,
            warnings=r.warnings,
        )

    def _sql_answer(self, common, dataset_id, question, plan, outcome, analysis_id, request_ids):
        result: QueryResult = outcome.result
        context = self._contexts.build(dataset_id)
        safe_values = {v for c in context.columns if c.examples for v in c.examples}
        insight = self._insights.generate(
            question=question,
            language=plan.language,
            result=result,
            definitions={str(i + 1): d for i, d in enumerate(context.definitions)},
            safe_values=safe_values,
            dataset_id=dataset_id,
            analysis_id=analysis_id,
        )
        if insight.llm_request_id:
            request_ids.append(insight.llm_request_id)
        warnings: list[ColumnNote] = []
        if result.truncated:
            warnings.append(
                ColumnNote(
                    level="warning",
                    code="result_truncated",
                    message=f"Only the first {result.row_count:,} rows are returned. Totals "
                    "computed from this table would be incomplete; ask for an aggregate instead.",
                )
            )
        if outcome.repairs:
            warnings.append(
                ColumnNote(
                    level="info",
                    code="sql_repaired",
                    message=f"The first query failed and was corrected automatically "
                    f"({outcome.repairs} repair(s)). Check the SQL below.",
                )
            )
        if not result.rows:
            warnings.append(
                ColumnNote(
                    level="info",
                    code="no_rows",
                    message="The query returned no rows: there is no data for this question "
                    "(check the period or filters).",
                )
            )
        kpis = _result_kpis(result)
        chart = None if kpis else recommend_chart(result, hint=plan.chart)
        return AnalysisResponse(
            **common,
            status="answered",
            answer=insight.summary,
            kpis=kpis,
            chart=chart,
            table=result,
            sql=outcome.sql,
            insight=insight,
            warnings=warnings,
        )
