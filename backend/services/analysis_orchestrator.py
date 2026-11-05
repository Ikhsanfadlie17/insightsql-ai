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
import re
import uuid
from datetime import UTC, date, datetime, timedelta

from backend.core.config import Settings
from backend.core.errors import (
    AppError,
    LLMNotConfiguredError,
    MappingRequiredError,
    ValidationError,
)
from backend.core.redaction import redact
from backend.repositories.history_repo import HistoryRepository
from backend.repositories.usage_repo import UsageRepository
from backend.schemas.analysis import KPI, AnalysisResponse, AnalysisUsage
from backend.schemas.analytics import Period, RetentionOptions, RetentionResult
from backend.schemas.datasets import ColumnNote
from backend.schemas.llm_outputs import RetentionParams, SQLPlanOutput
from backend.schemas.query import QueryResult
from backend.services.analytics_service import DatasetAnalytics, year_period
from backend.services.dataset_service import DatasetService
from backend.services.insight_generator import InsightGenerator
from backend.services.llm.gateway import LLMGateway
from backend.services.schema_context import SchemaContextBuilder
from backend.services.sql_generator import SQLGenerationFailed, TextToSQLService
from backend.services.visualization_service import recommend_chart, validate_chart_spec

logger = logging.getLogger(__name__)

HISTORY_TURNS = 3
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_RATE_TOKENS = {"rate", "ratio", "rasio"}
_PERCENT_TOKENS = {"pct", "percent", "persen", "percentage", "persentase"}
_YEAR_TOKENS = {"year", "tahun", "thn"}


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
    """Periods from the plan. A year-to-date request is rebuilt deterministically: both
    years are cut at the current period's last month/day (29 Feb -> 28 Feb)."""
    iso = date.fromisoformat
    previous = period_from(iso(params.previous_start), iso(params.previous_end))
    current = period_from(iso(params.current_start), iso(params.current_end))
    if params.ytd and previous.start.year < current.start.year:
        through = current.end - timedelta(days=1)
        previous = year_period(previous.start.year, through=through)
        current = year_period(current.start.year, through=through)
    return previous, current


def _short(label: str) -> str:
    """Compact card label: '2025' stays, '2025 (1 Jan–31 Oct)' -> '2025 YTD', a custom
    range stays whole."""
    if " (1 Jan–" in label:
        return label.split(" (")[0] + " YTD"
    return label


def _retention_kpis(r: RetentionResult) -> list[KPI]:
    d = r.definitions
    p, c = _short(r.previous.label), _short(r.current.label)
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
        tokens = set(re.split(r"[^a-z0-9]+", name.lower()))
        if isinstance(value, int) and tokens & _YEAR_TOKENS:
            fmt = "plain"  # 2026, not 2.026
        elif isinstance(value, int):
            fmt = "int"
        elif (
            value is not None
            and tokens & _RATE_TOKENS
            and not tokens & _PERCENT_TOKENS  # "growth_rate_pct" is already a percentage
            and abs(value) <= 1.5
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
        try:
            response = self._answer(base, dataset_id, question, turns, analysis_id)
        except SQLGenerationFailed as error:
            response = AnalysisResponse(
                **base, status="error", answer=redact(error.message), error_code=error.code
            )
            rejected = [a for a in error.attempts if a.sql]
            if rejected:
                codes = ", ".join(sorted({a.error_code for a in rejected}))
                response.warnings.append(
                    ColumnNote(
                        level="info",
                        code="rejected_sql",
                        message=f"{len(rejected)} generated query/queries were rejected or "
                        f"failed ({codes}). Only validated read-only SQL can run, so your "
                        "data was not changed.",
                    )
                )
        except AppError as error:  # provider errors, mapping required, invalid periods
            response = AnalysisResponse(
                **base, status="error", answer=redact(error.message), error_code=error.code
            )
        except Exception:  # never lose the question: store a generic error
            logger.exception("Analysis %s failed unexpectedly", analysis_id)
            response = AnalysisResponse(
                **base,
                status="error",
                answer="Something went wrong while answering this question. Please try again.",
                error_code="internal_error",
            )
        response.usage = self._usage_for(analysis_id)
        self._history.add_analysis(response)
        return response

    # -- internals -----------------------------------------------------------------------------
    def _usage_for(self, analysis_id: str) -> AnalysisUsage:
        """Every LLM request recorded for this analysis, including failed ones."""
        records = self._usage.by_analysis(analysis_id)
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
    ) -> AnalysisResponse:
        outcome = self._text_to_sql.ask(dataset_id, question, turns, analysis_id)
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
            return self._retention(common, dataset_id, question, plan, analysis_id)
        return self._sql_answer(common, dataset_id, question, plan, outcome, analysis_id)

    def _retention(self, common, dataset_id, question, plan, analysis_id):
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

    def _partial_period_note(
        self, dataset_id: str, result: QueryResult, sql: str | None
    ) -> ColumnNote | None:
        """Warn when a result compares complete years with the year the data stops in."""
        try:
            coverage = self._analytics.coverage(dataset_id)
        except MappingRequiredError:
            return None  # no confirmed transaction date: the data end is unknown
        except Exception:
            logger.warning("Coverage unavailable for the partial-period check", exc_info=True)
            return None
        return partial_year_note(result, coverage.data_end, coverage.years, sql)

    def _sql_answer(self, common, dataset_id, question, plan, outcome, analysis_id):
        result: QueryResult = outcome.result
        context = self._contexts.build(dataset_id)
        safe_values = {v for c in context.columns if c.examples for v in c.examples}
        partial = self._partial_period_note(dataset_id, result, outcome.sql)
        insight = self._insights.generate(
            question=question,
            language=plan.language,
            result=result,
            definitions={str(i + 1): d for i, d in enumerate(context.definitions)},
            safe_values=safe_values,
            dataset_id=dataset_id,
            analysis_id=analysis_id,
            sql=outcome.sql,
            notes=[partial.message] if partial else None,
        )
        warnings: list[ColumnNote] = [partial] if partial else []
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


def _as_year(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and 1900 <= value <= 2100:
        return value
    if isinstance(value, str) and len(value) == 4 and value.isdigit():
        return _as_year(int(value))
    return None


# The query already restricts every year to the same months or days (e.g. January-October).
_SAME_SPAN_SQL = re.compile(
    r"\b(?:month|dayofyear|doy|quarter|week|weekofyear)\s*\("
    r"|\bextract\s*\(\s*(?:month|doy|dayofyear|quarter|week|day)\b"
    r"|\bstrftime\s*\([^)]*%[mjdbB]"
    r"|\bmake_date\s*\("
    r"|\b(?:date_part|datepart)\s*\(\s*'(?:month|doy|dayofyear|quarter|week|day)'"
    r"|'\d{4}-(?!01-01')\d{2}-\d{2}'",
    re.IGNORECASE,
)


def partial_year_note(
    result: QueryResult, data_end: date | None, years: list[int], sql: str | None = None
) -> ColumnNote | None:
    """A result column of years that includes the year the data stops in, next to other
    years: comparing them is not like-for-like (first real-API run, 2026-11-05: all of 2025
    was compared with 2026 up to 31 October)."""
    if data_end is None or (data_end.month, data_end.day) == (12, 31) or not result.rows:
        return None
    if sql and _SAME_SPAN_SQL.search(sql):
        return None  # e.g. EXTRACT(MONTH ...) <= 10 or a date bound that is not 1 January
    known = set(years)
    for index, name in enumerate(result.columns):
        found = {_as_year(row[index]) for row in result.rows}
        if None in found or len(found) < 2 or not found <= known or data_end.year not in found:
            continue
        return ColumnNote(
            level="warning",
            code="partial_period",
            # Conditional on purpose: the SQL check above is a heuristic and may miss a
            # same-months filter, so the note must stay true either way.
            message=f"Data for {data_end.year} ends on {data_end.isoformat()}, so "
            f"{data_end.year} covers only part of the year. If the other years in '{name}' "
            "are whole years, the comparison is not like-for-like: compare the same months "
            "(year-to-date), for example in Client Analytics, before drawing conclusions.",
        )
    return None
