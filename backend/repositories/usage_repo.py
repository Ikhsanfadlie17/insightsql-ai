"""Persistence and aggregation of LLM request records."""

from __future__ import annotations

from pathlib import Path

from backend.repositories.db import connect
from backend.schemas.usage import UsageRecord, UsageSummary, UsageTotals

_COLUMNS = (
    "id", "created_at", "purpose", "dataset_id", "analysis_id", "model", "status", "attempts",
    "input_tokens", "output_tokens", "cached_tokens", "est_cost_usd", "cost_status",
    "latency_ms", "error_code", "error_message", "prompt_version",
)  # fmt: skip
_AGGREGATES = """
    COUNT(*) AS requests,
    SUM(status = 'success') AS succeeded,
    SUM(status = 'error') AS failed,
    COALESCE(SUM(input_tokens), 0) AS input_tokens,
    COALESCE(SUM(output_tokens), 0) AS output_tokens,
    COALESCE(SUM(cached_tokens), 0) AS cached_tokens,
    COALESCE(SUM(est_cost_usd), 0) AS estimated_cost_usd,
    SUM(est_cost_usd IS NULL) AS requests_without_cost
"""


class UsageRepository:
    """SQLite store for :class:`UsageRecord`."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path

    def add(self, record: UsageRecord) -> UsageRecord:
        """Insert one request record."""
        data = record.model_dump()
        data["created_at"] = record.created_at.isoformat()
        with connect(self._db_path) as conn:
            conn.execute(
                f"INSERT INTO llm_requests ({', '.join(_COLUMNS)}) "
                f"VALUES ({', '.join('?' for _ in _COLUMNS)})",
                [data[c] for c in _COLUMNS],
            )
        return record

    def list_requests(self, limit: int = 50) -> list[UsageRecord]:
        """Most recent requests first."""
        with connect(self._db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM llm_requests ORDER BY created_at DESC, rowid DESC LIMIT ?", (limit,)
            ).fetchall()
        return [UsageRecord.model_validate(dict(r)) for r in rows]

    def by_ids(self, ids: list[str]) -> list[UsageRecord]:
        """The records with these ids (oldest first)."""
        if not ids:
            return []
        marks = ", ".join("?" for _ in ids)
        with connect(self._db_path) as conn:
            rows = conn.execute(
                f"SELECT * FROM llm_requests WHERE id IN ({marks}) ORDER BY created_at, rowid",
                tuple(ids),
            ).fetchall()
        return [UsageRecord.model_validate(dict(r)) for r in rows]

    def by_analysis(self, analysis_id: str) -> list[UsageRecord]:
        """All requests recorded for one analysis (oldest first)."""
        with connect(self._db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM llm_requests WHERE analysis_id = ? ORDER BY created_at, rowid",
                (analysis_id,),
            ).fetchall()
        return [UsageRecord.model_validate(dict(r)) for r in rows]

    def summary(self) -> UsageSummary:
        """Totals overall, per model, and per purpose."""
        with connect(self._db_path) as conn:
            total = conn.execute(f"SELECT {_AGGREGATES} FROM llm_requests").fetchone()
            by_model = conn.execute(
                f"SELECT model AS key, {_AGGREGATES} FROM llm_requests "
                "GROUP BY model ORDER BY model"
            ).fetchall()
            by_purpose = conn.execute(
                f"SELECT purpose AS key, {_AGGREGATES} FROM llm_requests "
                "GROUP BY purpose ORDER BY purpose"
            ).fetchall()

        def totals(row, key: str = "all") -> UsageTotals:
            values = {k: (row[k] or 0) for k in row.keys() if k != "key"}  # noqa: SIM118
            return UsageTotals(key=row["key"] if "key" in row.keys() else key, **values)  # noqa: SIM118

        return UsageSummary(
            totals=totals(total),
            by_model=[totals(r) for r in by_model],
            by_purpose=[totals(r) for r in by_purpose],
        )
