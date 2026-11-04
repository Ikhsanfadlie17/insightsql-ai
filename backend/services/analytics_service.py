"""Deterministic client analytics: fixed SQL templates, no LLM.

Every query is built here from trusted parts only — column names from the dataset's
own metadata (quoted), ``DATE`` literals rendered from validated ``date`` objects, and
fixed category names — then passed through :func:`validate_sql` and run by the
sandboxed :class:`QueryExecutor`, exactly like generated SQL.

Definitions (ARCHITECTURE §8): a client identity is *active* in a period ``[start, end)``
when it has at least one transaction dated inside it; ``first_seen`` is its earliest
transaction in the whole dataset. Clients are always counted as distinct identities,
never as rows. Rows without an identity or a date are excluded and counted.
"""

from __future__ import annotations

import calendar
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from backend.core.errors import MappingRequiredError, ValidationError
from backend.repositories.dataset_repo import DatasetRepository
from backend.schemas.analytics import (
    CATEGORIES,
    AnalyticsCoverage,
    ClientListPage,
    DepartmentBreakdown,
    Granularity,
    IdentityKind,
    IdentityReport,
    Period,
    RetentionOptions,
    RetentionResult,
    TrendResult,
)
from backend.schemas.datasets import ColumnNote
from backend.schemas.mapping import ColumnMapping
from backend.schemas.query import QueryResult
from backend.services.dataset_service import DatasetService
from backend.services.query_executor import QueryExecutor
from backend.services.sql_utils import quote_identifier as q
from backend.services.sql_validator import TableSchema, validate_sql

NO_DEPARTMENT = "(no department)"
MAX_IDENTITY_EXAMPLES = 50
# RE2 class: whitespace, Unicode separators (no-break space), and invisible format
# characters (zero-width space, BOM) that often stick to IDs copied from other systems.
_WS = r"[\s\p{Z}\p{Cf}]"
MAX_TREND_BUCKETS = 600  # zero-filling stops here (e.g. a typo year 0202 in the data)
PLAUSIBLE_START = date(1950, 1, 1)
_CATEGORY_SQL = {c: (c,) for c in CATEGORIES} | {
    "existing_active": ("returning", "reactivated", "between_period")
}
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


# --- periods -----------------------------------------------------------------------------
def year_period(year: int, through: date | None = None) -> Period:
    """Calendar ``year``, or 1 Jan up to and including ``through``'s month/day in ``year``
    (year-to-date). 29 Feb is clamped to 28 Feb in non-leap years."""
    if through is None:
        return Period(label=str(year), start=date(year, 1, 1), end=date(year + 1, 1, 1))
    day = min(through.day, calendar.monthrange(year, through.month)[1])
    last = date(year, through.month, day)
    label = f"{year} (1 Jan–{last.day} {_MONTHS[last.month - 1]})"
    return Period(label=label, start=date(year, 1, 1), end=last + timedelta(days=1))


def _check_order(previous: Period, current: Period) -> None:
    if previous.end > current.start:
        raise ValidationError(
            "The previous period must end before the current period starts "
            f"({previous.label}: {previous.start}–{previous.end - timedelta(days=1)}, "
            f"{current.label}: {current.start}–{current.end - timedelta(days=1)})."
        )


def _lit(day: date) -> str:
    return f"DATE '{day.isoformat()}'"


def _inside(column: str, period: Period) -> str:
    return f"({column} >= {_lit(period.start)} AND {column} < {_lit(period.end)})"


def _ratio(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else numerator / denominator


# --- the data source -----------------------------------------------------------------------
@dataclass(frozen=True)
class AnalyticsSource:
    """A dataset file, its column types, and the **user-confirmed** mapping.

    Raises:
        MappingRequiredError: The mapping is not confirmed or lacks a client identity
            (ID or name) or a transaction date.
    """

    duckdb_path: Path
    columns: dict[str, str]
    mapping: ColumnMapping
    identity_notes: list[ColumnNote] = field(default_factory=list)

    def __post_init__(self) -> None:
        mapping = self.mapping
        if not mapping.confirmed:
            raise MappingRequiredError(
                "Confirm the column mapping (Data Sources → Column mapping) to enable client "
                "analytics."
            )
        if not (mapping.client_id or mapping.client_name):
            raise MappingRequiredError(
                "Client analytics need a confirmed Client ID (or Client name) column."
            )
        if not mapping.transaction_date:
            raise MappingRequiredError("Client analytics need a confirmed Transaction date column.")
        for column in (mapping.client_id, mapping.client_name, mapping.transaction_date,
                       mapping.department):  # fmt: skip
            if column and column not in self.columns:
                raise MappingRequiredError(
                    "The confirmed mapping refers to a column that no longer exists. "
                    "Confirm the column mapping again."
                )

    @classmethod
    def for_dataset(
        cls, datasets: DatasetService, repo: DatasetRepository, dataset_id: str
    ) -> AnalyticsSource:
        datasets.require_ready(dataset_id)
        mapping = repo.get_document(dataset_id, "mapping", ColumnMapping)
        if mapping is None:
            raise MappingRequiredError(
                "Confirm the column mapping (Data Sources → Column mapping) to enable client "
                "analytics."
            )
        columns = repo.get_columns(dataset_id)
        identity = mapping.client_id or mapping.client_name
        notes = [
            ColumnNote(
                level="warning",
                code=f"client_id_{n.code}",
                message=f"Client identity column: {n.message}",
            )
            for c in columns
            if c.name == identity
            for n in c.notes
            if n.level == "warning"
        ]
        return cls(
            duckdb_path=datasets.duckdb_path(dataset_id),
            columns={c.name: c.logical_type for c in columns},
            mapping=mapping,
            identity_notes=notes,
        )

    @property
    def schema(self) -> TableSchema:
        return TableSchema(columns=dict(self.columns))

    @property
    def identity_kind(self) -> IdentityKind:
        return "client_id" if self.mapping.client_id else "client_name"

    @property
    def identity_column(self) -> str:
        return self.mapping.client_id or self.mapping.client_name  # type: ignore[return-value]

    @property
    def date_column(self) -> str:
        return self.mapping.transaction_date  # type: ignore[return-value]

    @property
    def key_sql(self) -> str:
        """Client identity: trimmed ID text, or the normalized name; empty -> NULL."""
        text = f"CAST({q(self.identity_column)} AS VARCHAR)"
        if self.identity_kind == "client_id":
            return f"NULLIF(regexp_replace({text}, '^{_WS}+|{_WS}+$', '', 'g'), '')"
        return f"NULLIF(lower(trim(regexp_replace({text}, '{_WS}+', ' ', 'g'))), '')"

    @property
    def day_sql(self) -> str:
        return f"CAST({q(self.date_column)} AS DATE)"

    def name_key_sql(self) -> str | None:
        """Normalized client name when both an ID and a name are mapped."""
        if not (self.mapping.client_id and self.mapping.client_name):
            return None
        text = f"CAST({q(self.mapping.client_name)} AS VARCHAR)"
        return f"NULLIF(lower(trim(regexp_replace({text}, '{_WS}+', ' ', 'g'))), '')"


def _base_ctes(source: AnalyticsSource, dedupe: bool, extra: dict[str, str] | None = None) -> str:
    """``raw`` = every row; ``valid`` = (deduplicated) rows with an identity and a date."""
    extras = "".join(f", {expr} AS {alias}" for alias, expr in (extra or {}).items())
    rows = "SELECT DISTINCT * FROM dataset" if dedupe else "SELECT * FROM dataset"
    return (
        f"src AS ({rows}), "
        f"raw AS (SELECT {source.key_sql} AS client_key, {source.day_sql} AS tx_day "
        "FROM dataset), "
        f"tx AS (SELECT {source.key_sql} AS client_key, {source.day_sql} AS tx_day{extras} "
        "FROM src), "
        "valid AS (SELECT * FROM tx WHERE client_key IS NOT NULL AND tx_day IS NOT NULL)"
    )


def _classified_ctes(
    source: AnalyticsSource, previous: Period, current: Period, dedupe: bool, extra: dict[str, str]
) -> str:
    extra_aggregates = "".join(f", {expr} AS {alias}" for alias, expr in extra.items())
    p, c = previous, current
    return (
        "WITH "
        + _base_ctes(
            source,
            dedupe,
            {"name_value": q(source.mapping.client_name)}
            if source.mapping.client_id and source.mapping.client_name
            else None,
        )  # fmt: skip
        + ", per_client AS (SELECT client_key, MIN(tx_day) AS first_seen, "
        "MAX(tx_day) AS last_seen, "
        f"COUNT(*) FILTER (WHERE {_inside('tx_day', p)}) AS prev_tx, "
        f"COUNT(*) FILTER (WHERE {_inside('tx_day', c)}) AS cur_tx"
        f"{extra_aggregates} FROM valid GROUP BY client_key), "
        "classified AS (SELECT *, CASE "
        "WHEN prev_tx > 0 AND cur_tx > 0 THEN 'returning' "
        "WHEN prev_tx > 0 THEN 'not_returned' "
        f"WHEN cur_tx > 0 AND first_seen >= {_lit(c.start)} THEN 'new' "
        f"WHEN cur_tx > 0 AND first_seen < {_lit(p.start)} THEN 'reactivated' "
        f"WHEN cur_tx > 0 AND first_seen >= {_lit(p.end)} AND first_seen < {_lit(c.start)} "
        "THEN 'between_period' "
        "END AS category FROM per_client)"
    )


# --- definitions -------------------------------------------------------------------------
def retention_definitions(previous: Period, current: Period) -> dict[str, str]:
    p, c = previous.label, current.label
    return {
        "previous_active": f"Unique clients with at least one transaction in {p}.",
        "current_active": f"Unique clients with at least one transaction in {c}.",
        "new": f"Active in {c}, and their first transaction in this dataset is in {c}. "
        "Clients active before the dataset starts cannot be recognized, so some may not "
        "be truly new.",
        "returning": f"Active in both {p} and {c}.",
        "reactivated": f"Active in {c}, not active in {p}, and first seen in this dataset "
        f"before {p} started.",
        "between_period": f"Active in {c}, not active in {p}, and first seen between the end "
        f"of {p} and the start of {c}. Always 0 when the periods are adjacent.",
        "not_returned": f"Active in {p} but not in {c}. An observation, not proof of churn.",
        "existing_active": f"Active in {c} and first seen before {c} started (= returning + "
        "reactivated + between-period). Overlaps those categories: never add it to them.",
        "retention_rate": f"Returning ÷ active in {p} (the previous period). N/A when no "
        f"client was active in {p}.",
        "new_client_rate": f"New ÷ active in {c}. N/A when no client was active in {c}.",
        "growth_rate": f"(Active in {c} − active in {p}) ÷ active in {p}. N/A when no client "
        f"was active in {p}.",
        "transactions": "Rows dated inside the period with a client identity; exact duplicate "
        "rows are counted once (unless turned off).",
        "repeat_transactions": "Transactions − active clients in the period.",
        "clients_with_repeat": "Unique clients with two or more transactions in the period.",
    }


TREND_DEFINITIONS = {
    "active_clients": "Unique clients with at least one transaction in the bucket.",
    "new_clients": "Unique clients whose first transaction in this dataset falls in the bucket.",
    "transactions": "Rows in the bucket with a client identity and a date (exact duplicates "
    "counted once unless turned off).",
}


def _history_note(data_start: date | None) -> ColumnNote:
    start = data_start.isoformat() if data_start else "unknown"
    return ColumnNote(
        level="warning",
        code="history_completeness",
        message=f"New and Reactivated are classified from the history in this dataset, which "
        f"starts on {start}. Clients who were active before that date are not visible, so "
        "they can be counted as New, or not recognized as Reactivated.",
    )


# --- the service -------------------------------------------------------------------------
class AnalyticsService:
    """Client metrics from fixed, validated SQL templates."""

    def __init__(self, executor: QueryExecutor) -> None:
        self._executor = executor

    def _run(self, source: AnalyticsSource, sql: str) -> tuple[QueryResult, str]:
        validated = validate_sql(sql, source.schema)
        return self._executor.execute(source.duckdb_path, validated), validated.sql

    # -- comparison ------------------------------------------------------------------------
    def compare_periods(
        self,
        source: AnalyticsSource,
        previous: Period,
        current: Period,
        options: RetentionOptions | None = None,
    ) -> RetentionResult:
        """Client categories, counts, and rates for ``previous`` vs ``current``."""
        _check_order(previous, current)
        options = options or RetentionOptions()
        either = f"({_inside('tx_day', previous)} OR {_inside('tx_day', current)})"
        sql = (
            _classified_ctes(source, previous, current, options.dedupe_exact_duplicates, {})
            + " SELECT "
            "COUNT(*) FILTER (WHERE prev_tx > 0) AS previous_active, "
            "COUNT(*) FILTER (WHERE cur_tx > 0) AS current_active, "
            "COUNT(*) FILTER (WHERE category = 'new') AS \"new\", "
            "COUNT(*) FILTER (WHERE category = 'returning') AS \"returning\", "
            "COUNT(*) FILTER (WHERE category = 'reactivated') AS reactivated, "
            "COUNT(*) FILTER (WHERE category = 'between_period') AS between_period, "
            "COUNT(*) FILTER (WHERE category = 'not_returned') AS not_returned, "
            f"COUNT(*) FILTER (WHERE cur_tx > 0 AND first_seen < {_lit(current.start)}) "
            "AS existing_active, "
            "COALESCE(SUM(prev_tx), 0) AS transactions_previous, "
            "COALESCE(SUM(cur_tx), 0) AS transactions_current, "
            "COUNT(*) FILTER (WHERE prev_tx >= 2) AS clients_with_repeat_previous, "
            "COUNT(*) FILTER (WHERE cur_tx >= 2) AS clients_with_repeat_current, "
            f"(SELECT COUNT(*) FROM raw WHERE client_key IS NULL AND {either}) "
            "AS excluded_missing_id_rows, "
            "(SELECT COUNT(*) FROM raw WHERE client_key IS NOT NULL AND tx_day IS NULL) "
            "AS excluded_missing_date_rows, "
            f"(SELECT COUNT(*) FROM raw WHERE client_key IS NOT NULL AND {either}) "
            "AS raw_rows_in_periods, "
            "(SELECT MIN(tx_day) FROM raw) AS data_start, "
            "(SELECT MAX(tx_day) FROM raw) AS data_end "
            "FROM classified"
        )
        result, executed = self._run(source, sql)
        v = dict(zip(result.columns, result.rows[0], strict=True))
        data_start = date.fromisoformat(v["data_start"]) if v["data_start"] else None
        data_end = date.fromisoformat(v["data_end"]) if v["data_end"] else None
        transactions = v["transactions_previous"] + v["transactions_current"]
        counts = {
            k: int(v[k])
            for k in (
                "previous_active", "current_active", "new", "returning", "reactivated",
                "between_period", "not_returned", "existing_active", "transactions_previous",
                "transactions_current", "clients_with_repeat_previous",
                "clients_with_repeat_current", "excluded_missing_id_rows",
                "excluded_missing_date_rows",
            )
        }  # fmt: skip
        out = RetentionResult(
            previous=previous,
            current=current,
            identity_column=source.identity_column,
            identity_kind=source.identity_kind,
            date_column=source.date_column,
            **counts,
            repeat_transactions_previous=counts["transactions_previous"]
            - counts["previous_active"],
            repeat_transactions_current=counts["transactions_current"] - counts["current_active"],
            retention_rate=_ratio(counts["returning"], counts["previous_active"]),
            new_client_rate=_ratio(counts["new"], counts["current_active"]),
            growth_rate=_ratio(
                counts["current_active"] - counts["previous_active"], counts["previous_active"]
            ),
            duplicate_rows_excluded=int(v["raw_rows_in_periods"]) - int(transactions),
            data_start=data_start,
            data_end=data_end,
            definitions=retention_definitions(previous, current),
            sql=[executed],
        )
        out.warnings = self._compare_warnings(source, out)
        return out

    @staticmethod
    def _compare_warnings(source: AnalyticsSource, r: RetentionResult) -> list[ColumnNote]:
        notes: list[ColumnNote] = [_history_note(r.data_start)]

        def add(level: str, code: str, message: str) -> None:
            notes.append(ColumnNote(level=level, code=code, message=message))

        previous, current = r.previous, r.current
        last_day = current.end - timedelta(days=1)
        if source.identity_kind == "client_name":
            add(
                "warning",
                "name_identity",
                "Clients are identified by name (no client ID is mapped). Different spellings "
                "of one client are counted as different clients.",
            )
        notes.extend(source.identity_notes)
        notes.extend(_date_sanity_notes(r.data_start, r.data_end))
        if r.previous_active == 0 and r.current_active == 0:
            add(
                "warning",
                "no_data",
                f"No client has a transaction in {previous.label} or {current.label}. All "
                "counts are 0 and the rates are N/A.",
            )
        elif r.previous_active == 0:
            add(
                "warning",
                "no_data_previous",
                f"No client has a transaction in {previous.label}, so retention and growth "
                "are N/A.",
            )
        elif r.current_active == 0:
            add("warning", "no_data_current", f"No client has a transaction in {current.label}.")
        if r.data_start is not None and r.data_start >= previous.start:
            add(
                "warning",
                "history_starts_late",
                f"The data starts on {r.data_start}, not before {previous.label} starts "
                f"({previous.start}). There is no earlier history, so Reactivated cannot be "
                "detected and New may be overstated"
                + (
                    "; the previous period is only partly covered."
                    if r.data_start > previous.start
                    else "."
                ),
            )
        if r.data_end is not None and r.data_end < last_day:
            add(
                "warning",
                "incomplete_current_period",
                f"The data ends on {r.data_end}, before {current.label} ends ({last_day}). "
                "The current period looks incomplete; compare year-to-date periods (the same "
                "date range in both years) for a fair comparison.",
            )
        if abs(previous.days - current.days) > 1:
            add(
                "warning",
                "unequal_period_lengths",
                f"The periods have different lengths ({previous.days} vs {current.days} days), "
                "so counts are not directly comparable.",
            )
        if previous.end < current.start:
            add(
                "info",
                "non_adjacent_periods",
                f"There is a gap between the periods ({previous.end} to "
                f"{current.start - timedelta(days=1)}). Clients first seen in the gap are "
                "counted as Between-period.",
            )
        if r.excluded_missing_id_rows:
            add(
                "warning",
                "missing_client_ids",
                f"{r.excluded_missing_id_rows:,} row(s) in these periods have no client "
                "identity and are excluded from all client counts.",
            )
        if r.excluded_missing_date_rows:
            add(
                "warning",
                "missing_transaction_dates",
                f"{r.excluded_missing_date_rows:,} row(s) with a client identity have no valid "
                "transaction date and are excluded from period analysis.",
            )
        if r.duplicate_rows_excluded:
            add(
                "info",
                "duplicate_rows",
                f"{r.duplicate_rows_excluded:,} exact duplicate row(s) were counted once in "
                "transaction counts. Client counts are not affected.",
            )
        return notes

    # -- drill-down ------------------------------------------------------------------------
    def client_list(
        self,
        source: AnalyticsSource,
        previous: Period,
        current: Period,
        category: str,
        *,
        offset: int = 0,
        limit: int = 100,
        options: RetentionOptions | None = None,
    ) -> ClientListPage:
        """Clients in one category, ordered by identity, one page at a time."""
        if category not in _CATEGORY_SQL:
            raise ValidationError(f"Unknown category '{category}'.")
        _check_order(previous, current)
        options = options or RetentionOptions()
        offset, limit = max(offset, 0), max(1, min(limit, 500))
        with_name = bool(source.mapping.client_id and source.mapping.client_name)
        extra = {"client_name": "arg_max(name_value, tx_day)"} if with_name else {}
        allowed = ", ".join(f"'{c}'" for c in _CATEGORY_SQL[category])
        ctes = _classified_ctes(source, previous, current, options.dedupe_exact_duplicates, extra)
        name_column = "client_name, " if with_name else ""
        sql = (
            ctes + f" SELECT client_key AS client, {name_column}first_seen, last_seen, "
            "prev_tx AS transactions_previous, cur_tx AS transactions_current, "
            "COUNT(*) OVER () AS total "
            f"FROM classified WHERE category IN ({allowed}) "
            f"ORDER BY client_key LIMIT {limit} OFFSET {offset}"
        )
        result, _ = self._run(source, sql)
        if result.rows:
            total = int(result.rows[0][-1])
        else:
            count_sql = ctes + f" SELECT COUNT(*) FROM classified WHERE category IN ({allowed})"
            total = int(self._run(source, count_sql)[0].rows[0][0])
        page = QueryResult(
            columns=result.columns[:-1],
            rows=[row[:-1] for row in result.rows],
            row_count=result.row_count,
            truncated=offset + result.row_count < total,
            elapsed_ms=result.elapsed_ms,
        )
        return ClientListPage(
            category=category,  # type: ignore[arg-type]
            previous=previous,
            current=current,
            total=total,
            offset=offset,
            limit=limit,
            result=page,
        )

    # -- trend -----------------------------------------------------------------------------
    def client_trend(
        self,
        source: AnalyticsSource,
        granularity: Granularity = "month",
        options: RetentionOptions | None = None,
    ) -> TrendResult:
        """Active clients, new clients, and transactions per bucket (zero-filled)."""
        if granularity not in ("month", "quarter", "year"):
            raise ValidationError(f"Unknown granularity '{granularity}'.")
        options = options or RetentionOptions()
        bucket = f"CAST(date_trunc('{granularity}', v.tx_day) AS DATE)"
        first_bucket = f"CAST(date_trunc('{granularity}', f.first_seen) AS DATE)"
        sql = (
            "WITH "
            + _base_ctes(source, options.dedupe_exact_duplicates)
            + ", firsts AS (SELECT client_key, MIN(tx_day) AS first_seen FROM valid "
            "GROUP BY client_key) "
            f"SELECT {bucket} AS bucket, COUNT(DISTINCT v.client_key) AS active_clients, "
            f"COUNT(DISTINCT v.client_key) FILTER (WHERE {first_bucket} = {bucket}) "
            "AS new_clients, COUNT(*) AS transactions, "
            "(SELECT MIN(tx_day) FROM raw) AS data_start, "
            "(SELECT MAX(tx_day) FROM raw) AS data_end "
            "FROM valid AS v JOIN firsts AS f ON v.client_key = f.client_key "
            "GROUP BY 1 ORDER BY 1"
        )
        result, executed = self._run(source, sql)
        by_bucket = {date.fromisoformat(r[0]): r[1:4] for r in result.rows}
        if result.rows:
            data_start = date.fromisoformat(result.rows[0][4])
            data_end = date.fromisoformat(result.rows[0][5])
        else:
            data_start, data_end = self._data_range(source)
        rows: list[list] = []
        partial_first = partial_last = False
        if data_start and data_end:
            first, last = (
                _bucket_start(data_start, granularity),
                _bucket_start(data_end, granularity),
            )
            partial_first = data_start > first
            partial_last = data_end < _next_bucket(last, granularity) - timedelta(days=1)
            buckets = _bucket_count(first, last, granularity)
            if buckets <= MAX_TREND_BUCKETS:
                cursor = first
                while cursor <= last:
                    counts = by_bucket.get(cursor, [0, 0, 0])
                    rows.append([_bucket_label(cursor, granularity), *(int(n) for n in counts)])
                    cursor = _next_bucket(cursor, granularity)
            else:  # implausible span: show only buckets with data
                rows = [
                    [_bucket_label(b, granularity), *(int(n) for n in counts)]
                    for b, counts in sorted(by_bucket.items())
                ]
        notes = [_history_note(data_start), *_date_sanity_notes(data_start, data_end)]
        if partial_first:
            notes.append(
                ColumnNote(
                    level="info",
                    code="partial_first_bucket",
                    message=f"The data starts on {data_start}, so the first {granularity} is "
                    "only partly covered.",
                )
            )
        if partial_last:
            notes.append(
                ColumnNote(
                    level="warning",
                    code="partial_last_bucket",
                    message=f"The data ends on {data_end}, so the last {granularity} is only "
                    "partly covered; a drop there may only mean missing data.",
                )
            )
        if source.identity_kind == "client_name":
            notes.append(
                ColumnNote(
                    level="warning",
                    code="name_identity",
                    message="Clients are identified by name (no client ID is mapped).",
                )
            )
        return TrendResult(
            granularity=granularity,
            result=QueryResult(
                columns=["period", "active_clients", "new_clients", "transactions"],
                rows=rows,
                row_count=len(rows),
                elapsed_ms=result.elapsed_ms,
            ),
            data_start=data_start,
            data_end=data_end,
            partial_first=partial_first,
            partial_last=partial_last,
            warnings=notes,
            definitions=TREND_DEFINITIONS,
            sql=[executed],
        )

    def _data_range(self, source: AnalyticsSource) -> tuple[date | None, date | None]:
        sql = f"SELECT MIN({source.day_sql}), MAX({source.day_sql}) FROM dataset"
        row = self._run(source, sql)[0].rows[0]
        return (
            date.fromisoformat(row[0]) if row[0] else None,
            date.fromisoformat(row[1]) if row[1] else None,
        )

    # -- departments -----------------------------------------------------------------------
    def department_breakdown(
        self,
        source: AnalyticsSource,
        previous: Period,
        current: Period,
        options: RetentionOptions | None = None,
    ) -> DepartmentBreakdown:
        """Per department: clients active in each period, returning clients, retention."""
        department = source.mapping.department
        if not department:
            raise MappingRequiredError("No department column is confirmed in the mapping.")
        _check_order(previous, current)
        options = options or RetentionOptions()
        dept_sql = f"NULLIF(trim(CAST({q(department)} AS VARCHAR)), '')"
        prev_in, cur_in = _inside("tx_day", previous), _inside("tx_day", current)
        sql = (
            "WITH "
            + _base_ctes(source, options.dedupe_exact_duplicates, {"department": dept_sql})
            + ", per_dept AS (SELECT department, client_key, "
            f"COUNT(*) FILTER (WHERE {prev_in}) AS prev_tx, "
            f"COUNT(*) FILTER (WHERE {cur_in}) AS cur_tx "
            f"FROM valid WHERE {prev_in} OR {cur_in} GROUP BY 1, 2) "
            "SELECT department, "
            "COUNT(*) FILTER (WHERE prev_tx > 0) AS previous_active, "
            "COUNT(*) FILTER (WHERE cur_tx > 0) AS current_active, "
            'COUNT(*) FILTER (WHERE prev_tx > 0 AND cur_tx > 0) AS "returning", '
            "CASE WHEN COUNT(*) FILTER (WHERE prev_tx > 0) = 0 THEN NULL ELSE "
            "CAST(COUNT(*) FILTER (WHERE prev_tx > 0 AND cur_tx > 0) AS DOUBLE) / "
            "COUNT(*) FILTER (WHERE prev_tx > 0) END AS retention_rate, "
            "SUM(cur_tx) AS transactions_current, SUM(prev_tx) AS transactions_previous, "
            "(SELECT COUNT(*) FROM (SELECT client_key FROM per_dept WHERE cur_tx > 0 "
            "GROUP BY client_key HAVING COUNT(*) > 1) AS m) AS multi "
            "FROM per_dept GROUP BY department "
            "ORDER BY current_active DESC, previous_active DESC, department NULLS LAST"
        )
        result, executed = self._run(source, sql)
        multi = int(result.rows[0][-1]) if result.rows else 0
        table = QueryResult(
            columns=result.columns[:-1],
            # Missing departments are their own group; labelled here so that a real value
            # spelled "(no department)" is never merged with them.
            rows=[
                [row[0] if row[0] is not None else NO_DEPARTMENT, *row[1:-1]] for row in result.rows
            ],  # fmt: skip
            row_count=result.row_count,
            truncated=result.truncated,
            elapsed_ms=result.elapsed_ms,
        )
        notes = []
        if multi:
            notes.append(
                ColumnNote(
                    level="info",
                    code="clients_in_multiple_departments",
                    message=f"{multi:,} client(s) used more than one department in "
                    f"{current.label}. They are counted in each department, so the "
                    "department rows add up to more than the number of unique clients.",
                )
            )
        return DepartmentBreakdown(
            department_column=department,
            previous=previous,
            current=current,
            result=table,
            clients_in_multiple_departments=multi,
            warnings=notes,
            definitions={
                "previous_active": f"Unique clients with a transaction in this department "
                f"in {previous.label}.",
                "current_active": f"Unique clients with a transaction in this department in "
                f"{current.label}.",
                "returning": "Clients with a transaction in this department in both periods.",
                "retention_rate": "Returning ÷ previous active in this department (N/A when "
                "the department had no clients in the previous period).",
            },
            sql=[executed],
        )

    # -- identity quality ------------------------------------------------------------------
    def identity_issues(self, source: AnalyticsSource) -> IdentityReport:
        """Missing identities, IDs that differ only by case/spacing, name/ID conflicts."""
        name_key = source.name_key_sql()
        extra = {"name_key": name_key} if name_key else {}
        folded = f"lower(regexp_replace(client_key, '{_WS}+', '', 'g'))"
        keys = (
            f"WITH keys AS (SELECT {source.key_sql} AS client_key"
            + "".join(f", {expr} AS {alias}" for alias, expr in extra.items())
            + " FROM dataset)"
        )
        parts = [
            f"SELECT 'id_case_or_spacing' AS issue, {folded} AS value, "
            "COUNT(DISTINCT client_key) AS variants FROM keys WHERE client_key IS NOT NULL "
            "GROUP BY 2 HAVING COUNT(DISTINCT client_key) > 1"
        ]
        if name_key:
            parts += [
                "SELECT 'id_with_multiple_names' AS issue, client_key AS value, "
                "COUNT(DISTINCT name_key) AS variants FROM keys WHERE client_key IS NOT NULL "
                "AND name_key IS NOT NULL GROUP BY 2 HAVING COUNT(DISTINCT name_key) > 1",
                "SELECT 'name_with_multiple_ids' AS issue, name_key AS value, "
                "COUNT(DISTINCT client_key) AS variants FROM keys WHERE client_key IS NOT NULL "
                "AND name_key IS NOT NULL GROUP BY 2 HAVING COUNT(DISTINCT client_key) > 1",
            ]
        issues = " UNION ALL ".join(f"({p})" for p in parts)
        sql = (
            keys + f", issues AS ({issues}) SELECT issue, value, variants, "
            "(SELECT COUNT(*) FROM keys WHERE client_key IS NULL) AS missing, "
            "COUNT(*) OVER (PARTITION BY issue) AS issue_count FROM issues "
            "ORDER BY issue, variants DESC, value"
        )
        result, _ = self._run(source, sql)
        counts = {row[0]: int(row[4]) for row in result.rows}
        if result.rows:
            missing = int(result.rows[0][3])
        else:
            count_sql = keys + " SELECT COUNT(*) FROM keys WHERE client_key IS NULL"
            missing = int(self._run(source, count_sql)[0].rows[0][0])
        shown: dict[str, int] = {}
        examples = []
        for row in result.rows:
            shown[row[0]] = shown.get(row[0], 0) + 1
            if shown[row[0]] <= MAX_IDENTITY_EXAMPLES:
                examples.append(row[:3])
        notes = []
        if counts.get("id_case_or_spacing"):
            notes.append(
                ColumnNote(
                    level="warning",
                    code="id_case_or_spacing",
                    message=f"{counts['id_case_or_spacing']:,} group(s) of client IDs differ "
                    "only by upper/lower case or spaces (for example 'KL-001' and 'kl-001'). "
                    "They are counted as different clients; clean the IDs if they are the same "
                    "client.",
                )
            )
        if counts.get("id_with_multiple_names"):
            notes.append(
                ColumnNote(
                    level="info",
                    code="id_with_multiple_names",
                    message=f"{counts['id_with_multiple_names']:,} client ID(s) appear with "
                    "more than one name (ignoring upper/lower case and extra spaces). "
                    "Clients are counted by ID, so counts are not affected.",
                )
            )
        if counts.get("name_with_multiple_ids"):
            notes.append(
                ColumnNote(
                    level="warning",
                    code="name_with_multiple_ids",
                    message=f"{counts['name_with_multiple_ids']:,} client name(s) appear with "
                    "more than one ID. If they are the same client, it is counted more than "
                    "once.",
                )
            )
        if missing:
            notes.append(
                ColumnNote(
                    level="warning",
                    code="missing_client_ids",
                    message=f"{missing:,} row(s) have no client identity and are excluded from "
                    "client analytics.",
                )
            )
        return IdentityReport(
            identity_column=source.identity_column,
            identity_kind=source.identity_kind,
            missing_id_rows=missing,
            ids_differing_only_by_case_or_spacing=counts.get("id_case_or_spacing", 0),
            ids_with_multiple_names=counts.get("id_with_multiple_names", 0) if name_key else None,
            names_with_multiple_ids=counts.get("name_with_multiple_ids", 0) if name_key else None,
            examples=QueryResult(
                columns=["issue", "value", "variants"], rows=examples, row_count=len(examples)
            ),
            warnings=notes,
        )

    # -- coverage --------------------------------------------------------------------------
    def coverage(self, source: AnalyticsSource) -> AnalyticsCoverage:
        """Rows, unique clients, and the date range of the data."""
        sql = (
            f"WITH raw AS (SELECT {source.key_sql} AS client_key, {source.day_sql} AS tx_day "
            "FROM dataset) SELECT COUNT(*) AS row_total, "
            "COUNT(DISTINCT client_key) AS unique_clients, "
            "COUNT(*) FILTER (WHERE client_key IS NULL) AS missing_id_rows, "
            "COUNT(*) FILTER (WHERE tx_day IS NULL) AS missing_date_rows, "
            "MIN(tx_day) AS data_start, MAX(tx_day) AS data_end, "
            "string_agg(DISTINCT CAST(year(tx_day) AS VARCHAR), ',') AS years FROM raw"
        )
        row = self._run(source, sql)[0].rows[0]
        start = date.fromisoformat(row[4]) if row[4] else None
        end = date.fromisoformat(row[5]) if row[5] else None
        notes = [_history_note(start), *_date_sanity_notes(start, end)]
        notes += list(source.identity_notes)
        if source.identity_kind == "client_name":
            notes.append(
                ColumnNote(
                    level="warning",
                    code="name_identity",
                    message="Clients are identified by name (no client ID is mapped). "
                    "Different spellings of one client are counted as different clients.",
                )
            )
        return AnalyticsCoverage(
            identity_column=source.identity_column,
            identity_kind=source.identity_kind,
            date_column=source.date_column,
            department_column=source.mapping.department,
            rows=int(row[0]),
            unique_clients=int(row[1]),
            missing_id_rows=int(row[2]),
            missing_date_rows=int(row[3]),
            data_start=start,
            data_end=end,
            years=sorted(int(y) for y in row[6].split(",")) if row[6] else [],
            warnings=notes,
        )


def _date_sanity_notes(start: date | None, end: date | None) -> list[ColumnNote]:
    """Flag dates that are probably typos (e.g. 0202 for 2020, or far in the future)."""
    if start is None or end is None:
        return []
    future = date.today() + timedelta(days=366)
    if start >= PLAUSIBLE_START and end <= future:
        return []
    return [
        ColumnNote(
            level="warning",
            code="implausible_dates",
            message=f"The dates run from {start} to {end}. Dates before "
            f"{PLAUSIBLE_START.year} or more than a year in the future are probably typing "
            "errors; they shift first-seen dates (New / Reactivated) and the available years.",
        )
    ]


def _bucket_count(first: date, last: date, granularity: str) -> int:
    months = (last.year - first.year) * 12 + last.month - first.month
    return months // {"month": 1, "quarter": 3, "year": 12}[granularity] + 1


# --- buckets -------------------------------------------------------------------------------
def _bucket_start(day: date, granularity: str) -> date:
    if granularity == "year":
        return date(day.year, 1, 1)
    if granularity == "quarter":
        return date(day.year, 3 * ((day.month - 1) // 3) + 1, 1)
    return date(day.year, day.month, 1)


def _next_bucket(start: date, granularity: str) -> date:
    months = {"month": 1, "quarter": 3, "year": 12}[granularity]
    index = start.year * 12 + start.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


def _bucket_label(start: date, granularity: str) -> str:
    if granularity == "year":
        return f"{start.year}"
    if granularity == "quarter":
        return f"{start.year}-Q{(start.month - 1) // 3 + 1}"
    return f"{start.year}-{start.month:02d}"


# --- per-dataset facade with a small result cache --------------------------------------------
class DatasetAnalytics:
    """Resolves a dataset id to an :class:`AnalyticsSource` and caches results.

    A dataset file never changes after import, so a result depends only on the dataset,
    the confirmed mapping (``confirmed_at``), and the request. The source is resolved on
    every call, so a deleted dataset or an unconfirmed mapping is never served from cache.
    """

    def __init__(
        self,
        datasets: DatasetService,
        repo: DatasetRepository,
        service: AnalyticsService,
        max_entries: int = 128,
    ) -> None:
        self._datasets = datasets
        self._repo = repo
        self._service = service
        self._cache: OrderedDict[tuple, object] = OrderedDict()
        self._max_entries = max_entries
        self._lock = threading.Lock()  # FastAPI runs sync endpoints in a thread pool

    @property
    def service(self) -> AnalyticsService:
        return self._service

    def source(self, dataset_id: str) -> AnalyticsSource:
        return AnalyticsSource.for_dataset(self._datasets, self._repo, dataset_id)

    def _cached(self, dataset_id: str, name: str, key: tuple, compute):  # noqa: ANN001, ANN202
        source = self.source(dataset_id)
        full_key = (dataset_id, source.mapping.confirmed_at, name, *key)
        with self._lock:
            cached = self._cache.get(full_key)
            if cached is not None:
                self._cache.move_to_end(full_key)
                return cached.model_copy(deep=True)
        value = compute(source)  # outside the lock: queries may run concurrently
        with self._lock:
            self._cache[full_key] = value
            while len(self._cache) > self._max_entries:
                self._cache.popitem(last=False)
        return value.model_copy(deep=True)  # callers can never alter the cached copy

    def coverage(self, dataset_id: str) -> AnalyticsCoverage:
        return self._cached(dataset_id, "coverage", (), self._service.coverage)

    def compare(
        self, dataset_id: str, previous: Period, current: Period, options: RetentionOptions
    ) -> RetentionResult:
        return self._cached(
            dataset_id,
            "compare",
            (previous, current, options.dedupe_exact_duplicates),
            lambda s: self._service.compare_periods(s, previous, current, options),
        )

    def clients(
        self,
        dataset_id: str,
        previous: Period,
        current: Period,
        category: str,
        offset: int,
        limit: int,
        options: RetentionOptions,
    ) -> ClientListPage:
        return self._cached(
            dataset_id,
            "clients",
            (previous, current, category, offset, limit, options.dedupe_exact_duplicates),
            lambda s: self._service.client_list(
                s, previous, current, category, offset=offset, limit=limit, options=options
            ),
        )

    def trend(
        self, dataset_id: str, granularity: Granularity, options: RetentionOptions | None = None
    ) -> TrendResult:
        options = options or RetentionOptions()
        return self._cached(
            dataset_id,
            "trend",
            (granularity, options.dedupe_exact_duplicates),
            lambda s: self._service.client_trend(s, granularity, options),
        )

    def departments(
        self, dataset_id: str, previous: Period, current: Period, options: RetentionOptions
    ) -> DepartmentBreakdown:
        return self._cached(
            dataset_id,
            "departments",
            (previous, current, options.dedupe_exact_duplicates),
            lambda s: self._service.department_breakdown(s, previous, current, options),
        )

    def identity_issues(self, dataset_id: str) -> IdentityReport:
        return self._cached(dataset_id, "identity", (), self._service.identity_issues)
