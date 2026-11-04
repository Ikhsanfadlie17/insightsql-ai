"""Dataset profiling computed inside DuckDB (rows are never pulled into Python)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import duckdb

from backend.schemas.datasets import ColumnNote, DatasetColumn
from backend.schemas.profile import ColumnProfile, DatasetProfile, ValueCount
from backend.services.query_results import to_jsonable
from backend.services.sql_utils import quote_identifier as q

TABLE = "dataset"
TOP_VALUES = 5
MAX_VALUE_CHARS = 80
_NUMERIC = {"integer", "numeric"}
_DATES = {"date", "datetime"}


def _truncate(value: str) -> str:
    return value if len(value) <= MAX_VALUE_CHARS else value[:MAX_VALUE_CHARS] + "…"


def _aggregate_sql(columns: list[DatasetColumn]) -> str:
    """One scan computing every column's counts and type-specific statistics."""
    parts: list[str] = []
    for column in columns:
        c = q(column.name)
        parts += [f"COUNT({c})", f"COUNT(DISTINCT {c})"]
        if column.logical_type in _NUMERIC:
            parts += [
                f"MIN({c})::DOUBLE",
                f"MAX({c})::DOUBLE",
                f"AVG({c})::DOUBLE",
                f"MEDIAN({c})::DOUBLE",
                f"STDDEV_SAMP({c})::DOUBLE",
            ]
        elif column.logical_type in _DATES:
            parts += [f"MIN({c})", f"MAX({c})"]
    return f"SELECT COUNT(*), {', '.join(parts)} FROM {TABLE}"


def _top_values(conn: duckdb.DuckDBPyConnection, column: str) -> list[ValueCount]:
    c = q(column)
    rows = conn.execute(
        f"""SELECT CAST({c} AS VARCHAR) AS v, COUNT(*) AS n FROM {TABLE}
            WHERE {c} IS NOT NULL GROUP BY 1 ORDER BY n DESC, v LIMIT {TOP_VALUES}"""
    ).fetchall()
    return [ValueCount(value=_truncate(v), count=n) for v, n in rows]


def _dataset_notes(
    profile_columns: list[ColumnProfile], row_count: int, duplicates: int
) -> list[ColumnNote]:
    notes: list[ColumnNote] = []
    if duplicates:
        notes.append(
            ColumnNote(
                level="warning",
                code="duplicate_rows",
                message=f"{duplicates:,} row(s) are exact duplicates of another row. "
                "They are kept in the data; client analytics counts unique clients, so "
                "duplicates only affect transaction counts.",
            )
        )
    generated = [c for c in profile_columns if c.original_name.strip() == ""]
    if profile_columns and len(generated) / len(profile_columns) > 0.5:
        notes.append(
            ColumnNote(
                level="warning",
                code="missing_headers",
                message="Most columns have no header. The header row may not be the first "
                "row of the file (for example, a title row above the table). Remove rows "
                "above the headers and import again.",
            )
        )
    for column in profile_columns:
        if row_count and column.null_count == row_count:
            label = column.original_name or column.name
            notes.append(
                ColumnNote(
                    level="info",
                    code="empty_column",
                    message=f"Column '{label}' has no values.",
                )
            )
    if not any(c.logical_type in _DATES for c in profile_columns):
        notes.append(
            ColumnNote(
                level="info",
                code="no_date_column",
                message="No date column was detected, so period and trend analysis is not "
                "available for this dataset.",
            )
        )
    return notes


def profile_dataset(duckdb_path: Path, columns: list[DatasetColumn]) -> DatasetProfile:
    """Compute the profile of a dataset's DuckDB table.

    Args:
        duckdb_path: The dataset's DuckDB file (table ``dataset``).
        columns: Stored column metadata, in table order.
    """
    with duckdb.connect(str(duckdb_path), read_only=True) as conn:
        aggregates = list(conn.execute(_aggregate_sql(columns)).fetchone())
        duplicates = conn.execute(
            f"SELECT COUNT(*) - (SELECT COUNT(*) FROM (SELECT DISTINCT * FROM {TABLE})) "
            f"FROM {TABLE}"
        ).fetchone()[0]
        top = {c.name: _top_values(conn, c.name) for c in columns}

    row_count = int(aggregates.pop(0))
    profiles: list[ColumnProfile] = []
    for column in columns:
        non_null, unique = int(aggregates.pop(0)), int(aggregates.pop(0))
        stats: dict[str, object] = {}
        if column.logical_type in _NUMERIC:
            for key in ("min", "max", "mean", "median", "std"):
                stats[key] = to_jsonable(aggregates.pop(0))
        elif column.logical_type in _DATES:
            stats["date_min"] = to_jsonable(aggregates.pop(0))
            stats["date_max"] = to_jsonable(aggregates.pop(0))
        null_count = row_count - non_null
        profiles.append(
            ColumnProfile(
                name=column.name,
                original_name=column.original_name,
                logical_type=column.logical_type,
                null_count=null_count,
                null_pct=(null_count / row_count * 100) if row_count else 0.0,
                unique_count=unique,
                top_values=top[column.name],
                notes=column.notes,
                **stats,
            )
        )

    missing = sum(p.null_count for p in profiles)
    cells = row_count * len(columns)
    return DatasetProfile(
        row_count=row_count,
        column_count=len(columns),
        duplicate_row_count=int(duplicates),
        missing_cells=missing,
        missing_pct=(missing / cells * 100) if cells else 0.0,
        columns=profiles,
        notes=_dataset_notes(profiles, row_count, int(duplicates)),
        generated_at=datetime.now(UTC),
    )
