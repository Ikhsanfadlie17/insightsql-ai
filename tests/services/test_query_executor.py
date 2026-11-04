"""Task 10: the executor runs only validated SQL inside a read-only sandbox."""

from __future__ import annotations

import time
from pathlib import Path

import duckdb
import pytest

from backend.core.config import Settings
from backend.core.errors import QueryExecutionError, QueryTimeoutError
from backend.services import sql_validator
from backend.services.query_executor import QueryExecutor, sanitize_error
from backend.services.sql_validator import TableSchema, ValidatedSQL, validate_sql

SCHEMA = TableSchema(columns={"id_klien": "text", "nilai": "integer", "tanggal": "date"})


@pytest.fixture
def dataset_path(tmp_path: Path) -> Path:
    path = tmp_path / "ds.duckdb"
    with duckdb.connect(str(path)) as conn:
        conn.execute(
            "CREATE TABLE dataset AS SELECT 'KL-' || LPAD(CAST(i AS VARCHAR), 4, '0') AS id_klien, "
            "i AS nilai, DATE '2026-01-01' + CAST(i % 300 AS INTEGER) AS tanggal "
            "FROM range(5000) t(i)"
        )
    return path


@pytest.fixture
def executor(settings: Settings) -> QueryExecutor:
    return QueryExecutor(settings)


def _unchecked(sql: str) -> ValidatedSQL:
    """Bypass the validator to prove the sandbox holds on its own (defense in depth)."""
    return ValidatedSQL(sql, frozenset(), _token=sql_validator._TOKEN)  # noqa: SLF001


def test_executes_validated_sql(executor: QueryExecutor, dataset_path: Path) -> None:
    result = executor.execute(
        dataset_path,
        validate_sql("SELECT COUNT(*) AS n, MAX(tanggal) AS last FROM dataset", SCHEMA),
    )

    assert result.columns == ["n", "last"]
    assert result.rows == [[5000, "2026-10-27"]]
    assert result.truncated is False
    assert result.elapsed_ms >= 0


def test_rejects_plain_strings(executor: QueryExecutor, dataset_path: Path) -> None:
    with pytest.raises(TypeError):
        executor.execute(dataset_path, "SELECT 1")  # type: ignore[arg-type]


def test_row_limit_marks_truncation(settings: Settings, dataset_path: Path) -> None:
    executor = QueryExecutor(settings.model_copy(update={"max_result_rows": 10}))

    result = executor.execute(dataset_path, validate_sql("SELECT * FROM dataset", SCHEMA))

    assert result.row_count == 10 and len(result.rows) == 10
    assert result.truncated is True


def test_exact_limit_is_not_truncated(settings: Settings, dataset_path: Path) -> None:
    executor = QueryExecutor(settings.model_copy(update={"max_result_rows": 5000}))

    result = executor.execute(dataset_path, validate_sql("SELECT * FROM dataset", SCHEMA))

    assert result.row_count == 5000 and result.truncated is False


def test_empty_result(executor: QueryExecutor, dataset_path: Path) -> None:
    result = executor.execute(
        dataset_path, validate_sql("SELECT * FROM dataset WHERE nilai < 0", SCHEMA)
    )

    assert (result.rows, result.row_count, result.truncated) == ([], 0, False)
    assert result.columns == ["id_klien", "nilai", "tanggal"]


def test_timeout_interrupts_slow_queries(settings: Settings, dataset_path: Path) -> None:
    executor = QueryExecutor(settings.model_copy(update={"query_timeout_seconds": 0.5}))
    slow = validate_sql(
        "SELECT COUNT(*) FROM dataset a JOIN dataset b ON a.nilai >= b.nilai "
        "JOIN dataset c ON b.nilai >= c.nilai",
        SCHEMA,
    )

    started = time.perf_counter()
    with pytest.raises(QueryTimeoutError):
        executor.execute(dataset_path, slow)

    assert time.perf_counter() - started < 3


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM read_csv('/etc/hosts')",
        "SELECT * FROM '/etc/hosts'",
        "COPY dataset TO 'leak.csv'",
        "ATTACH 'other.duckdb'",
        "INSTALL httpfs",
        "CREATE TABLE x AS SELECT 1",
        "DELETE FROM dataset",
        "SET threads = 64",
    ],
)
def test_sandbox_blocks_dangerous_sql_even_without_the_validator(
    executor: QueryExecutor, dataset_path: Path, sql: str
) -> None:
    with pytest.raises(QueryExecutionError):
        executor.execute(dataset_path, _unchecked(sql))

    with duckdb.connect(str(dataset_path), read_only=True) as conn:
        assert conn.execute("SELECT COUNT(*) FROM dataset").fetchone()[0] == 5000


def test_errors_do_not_leak_values_or_paths(executor: QueryExecutor, dataset_path: Path) -> None:
    with pytest.raises(QueryExecutionError) as caught:
        executor.execute(
            dataset_path, validate_sql("SELECT CAST(id_klien AS INTEGER) FROM dataset", SCHEMA)
        )

    message = caught.value.message
    assert "KL-0000" not in message
    assert str(dataset_path.parent) not in message
    assert "convert" in message.lower()


def test_missing_dataset_file_is_a_clean_error(executor: QueryExecutor, tmp_path: Path) -> None:
    with pytest.raises(QueryExecutionError) as caught:
        executor.execute(tmp_path / "missing.duckdb", validate_sql("SELECT 1", SCHEMA))

    assert str(tmp_path) not in caught.value.message


@pytest.mark.parametrize(
    ("raw", "forbidden"),
    [
        ("Conversion Error: Could not convert string 'KL-77' to INT32", "KL-77"),
        ('Permission Error: Cannot access file "/etc/hosts" - disabled', "/etc/hosts"),
        ('Binder Error: Referenced column "nik" not found\nLINE 1: SELECT nik', "LINE 1"),
        ("Error near 3201234567890123 in value", "3201234567890123"),
    ],
)
def test_sanitize_error(raw: str, forbidden: str) -> None:
    assert forbidden not in sanitize_error(raw)


def test_order_by_is_preserved_through_the_row_limit_wrapper(
    settings: Settings, dataset_path: Path
) -> None:
    executor = QueryExecutor(settings.model_copy(update={"max_result_rows": 3}))

    result = executor.execute(
        dataset_path, validate_sql("SELECT nilai FROM dataset ORDER BY nilai DESC", SCHEMA)
    )

    assert result.rows == [[4999], [4998], [4997]]


def test_duplicate_output_column_names(executor: QueryExecutor, dataset_path: Path) -> None:
    result = executor.execute(
        dataset_path,
        validate_sql(
            "SELECT a.nilai, b.nilai FROM dataset a JOIN dataset b ON a.id_klien = b.id_klien "
            "WHERE a.nilai < 2 ORDER BY a.nilai",
            SCHEMA,
        ),
    )

    assert result.row_count == 2
    assert len(result.columns) == 2
