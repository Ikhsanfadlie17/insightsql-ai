"""Task 10: the executor runs only validated SQL inside a read-only sandbox."""

from __future__ import annotations

import time
from pathlib import Path

import duckdb
import pytest

from backend.core.config import Settings
from backend.core.errors import QueryExecutionError, QueryTimeoutError
from backend.services import sql_validator
from backend.services.query_executor import QueryExecutor
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
    # Passes the validator (equality joins) but explodes: only the timeout can stop it.
    slow = validate_sql(
        "SELECT COUNT(*) FROM dataset a JOIN dataset b ON a.nilai % 2 = b.nilai % 2 "
        "JOIN dataset c ON b.nilai % 2 = c.nilai % 2 JOIN dataset d ON c.nilai % 2 = d.nilai % 2",
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


def test_missing_dataset_file_is_a_clean_error(executor: QueryExecutor, tmp_path: Path) -> None:
    with pytest.raises(QueryExecutionError) as caught:
        executor.execute(tmp_path / "missing.duckdb", validate_sql("SELECT 1", SCHEMA))

    assert str(tmp_path) not in caught.value.message


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


# --- Phase 4 review regressions -----------------------------------------------------


@pytest.fixture
def pii_path(tmp_path: Path) -> Path:
    path = tmp_path / "pii.duckdb"
    with duckdb.connect(str(path)) as conn:
        conn.execute(
            "CREATE TABLE dataset AS SELECT * FROM (VALUES "
            "('KL-4321', 4321, 'Jl. Merdeka No. 10\nRT 02 Jakarta', 'budi@example.com'), "
            "('KL-0001', 1, 'Jl. Melati 5', 'sari@example.com')) "
            "t(id_klien, nilai, alamat, email)"
        )
    return path


LEAKS = ["KL-4321", "4321", "Merdeka", "Jakarta", "budi@example.com", "Melati", "KL-0"]


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT CAST(alamat AS INTEGER) FROM dataset",  # multi-line value in the error
        "SELECT CAST(nilai AS UTINYINT) FROM dataset WHERE nilai = 4321",  # unquoted number
        "SELECT error('NIK ' || id_klien || ' / ' || CAST(nilai AS VARCHAR)) FROM dataset",
        "SELECT regexp_matches(id_klien, id_klien || '(') FROM dataset",
        "SELECT make_date(2026, nilai, 1) FROM dataset",
        "SELECT CAST(email AS DATE) FROM dataset",
        "SELECT nilai // 0 FROM dataset",
    ],
)
def test_error_messages_never_contain_data(
    settings: Settings, pii_path: Path, sql: str, caplog: pytest.LogCaptureFixture
) -> None:
    """Review H3: messages are chosen by error category; DuckDB text is never forwarded."""
    caplog.set_level("DEBUG")
    executor = QueryExecutor(settings)

    with pytest.raises(QueryExecutionError) as caught:
        executor.execute(pii_path, _unchecked(sql))

    combined = caught.value.message + caplog.text
    assert all(leak not in combined for leak in LEAKS), combined
    assert str(pii_path.parent) not in combined
    assert caught.value.message


def test_conversion_errors_explain_the_fix(executor: QueryExecutor, pii_path: Path) -> None:
    with pytest.raises(QueryExecutionError) as caught:
        executor.execute(pii_path, _unchecked("SELECT CAST(alamat AS INTEGER) FROM dataset"))

    assert "TRY_CAST" in caught.value.message


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT count(*) FROM (SELECT unnest(list_resize([1], 300000000, 1)))",
        "SELECT length(lpad('', 3000000000, 'x'))",
        "SELECT count(*) FROM repeat_row(1, num_rows = 100000000000)",
    ],
)
def test_runaway_queries_are_stopped_by_hard_limits(
    settings: Settings, dataset_path: Path, sql: str
) -> None:
    """Review H2: these ignore DuckDB's interrupt and memory_limit; the worker is killed."""
    limited = settings.model_copy(
        update={"query_timeout_seconds": 3.0, "query_process_memory_mb": 768}
    )
    started = time.perf_counter()

    with pytest.raises((QueryExecutionError, QueryTimeoutError)):
        QueryExecutor(limited).execute(dataset_path, _unchecked(sql))

    assert time.perf_counter() - started < 8


def test_memory_kill_has_a_clear_message(settings: Settings, dataset_path: Path) -> None:
    limited = settings.model_copy(update={"query_process_memory_mb": 512})

    with pytest.raises(QueryExecutionError) as caught:
        QueryExecutor(limited).execute(
            dataset_path,
            _unchecked("SELECT count(*) FROM (SELECT unnest(list_resize([1], 300000000, 1)))"),
        )

    assert "memory" in caught.value.message.lower()


def test_executor_runs_exactly_one_statement(executor: QueryExecutor, dataset_path: Path) -> None:
    """Review Low: even if the validator were bypassed, smuggled statements do not run."""
    smuggled = "SELECT 1) r; CREATE TEMP TABLE t AS SELECT * FROM dataset; SELECT * FROM (SELECT 1"

    with pytest.raises(QueryExecutionError):
        executor.execute(dataset_path, _unchecked(smuggled))


def test_result_size_is_capped_in_bytes(settings: Settings, dataset_path: Path) -> None:
    small = settings.model_copy(update={"max_result_bytes": 2_000})

    with pytest.raises(QueryExecutionError) as caught:
        QueryExecutor(small).execute(dataset_path, validate_sql("SELECT * FROM dataset", SCHEMA))

    assert "too large" in caught.value.message.lower()


def test_queries_run_in_a_separate_process(
    executor: QueryExecutor, dataset_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The backend process never opens the dataset for LLM SQL itself."""
    opened: list[str] = []
    real_connect = duckdb.connect
    monkeypatch.setattr(
        duckdb, "connect", lambda *a, **k: opened.append("x") or real_connect(*a, **k)
    )

    executor.execute(dataset_path, validate_sql("SELECT COUNT(*) FROM dataset", SCHEMA))

    assert opened == []
