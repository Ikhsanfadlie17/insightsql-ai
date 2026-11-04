"""Execute validated SQL in a read-only, sandboxed DuckDB connection.

Defense in depth on top of ``sql_validator``:

* only :class:`ValidatedSQL` objects are accepted (plain strings raise ``TypeError``);
* the dataset file is opened ``read_only`` with external access, extension
  install/autoload, and community extensions disabled, then the configuration is
  locked so a query cannot re-enable anything;
* ``threads`` and ``memory_limit`` cap resource use;
* a watchdog interrupts queries that exceed ``query_timeout_seconds``;
* results are capped at ``max_result_rows`` (``truncated`` tells the caller);
* error messages are sanitized: quoted values, long numbers, file paths, and the
  echoed query are removed, so data values never reach logs, users, or the LLM.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from pathlib import Path

import duckdb

from backend.core.config import Settings
from backend.core.errors import QueryExecutionError, QueryTimeoutError
from backend.schemas.query import QueryResult
from backend.services.query_results import rows_to_jsonable
from backend.services.sql_validator import ValidatedSQL

logger = logging.getLogger(__name__)

_QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"")
_PATH = re.compile(r"(?<![\w])(/[^\s'\"]+)+")
_LONG_NUMBER = re.compile(r"\d{6,}")
_MAX_MESSAGE_CHARS = 300


def sanitize_error(message: str) -> str:
    """Keep the error's meaning, drop anything that could be data or infrastructure."""
    first_line = message.strip().splitlines()[0] if message.strip() else "Query failed."
    text = _QUOTED.sub("<value>", first_line)
    text = _PATH.sub("<path>", text)
    text = _LONG_NUMBER.sub("<number>", text)
    return text[:_MAX_MESSAGE_CHARS]


class QueryExecutor:
    """Runs :class:`ValidatedSQL` against one dataset's DuckDB file."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _connect(self, duckdb_path: Path) -> duckdb.DuckDBPyConnection:
        config = {
            "enable_external_access": False,
            "autoload_known_extensions": False,
            "autoinstall_known_extensions": False,
            "allow_community_extensions": False,
            "threads": self._settings.query_threads,
            "memory_limit": self._settings.query_memory_limit,
        }
        conn = duckdb.connect(str(duckdb_path), read_only=True, config=config)
        conn.execute("SET lock_configuration = true")
        return conn

    def execute(self, duckdb_path: Path, query: ValidatedSQL) -> QueryResult:
        """Run a validated query and return at most ``max_result_rows`` rows.

        Raises:
            TypeError: If ``query`` is not a :class:`ValidatedSQL`.
            QueryTimeoutError: If the query runs longer than the configured timeout.
            QueryExecutionError: For any DuckDB error (with a sanitized message).
        """
        if not isinstance(query, ValidatedSQL):
            raise TypeError("QueryExecutor only executes ValidatedSQL produced by validate_sql().")
        if not duckdb_path.exists():
            raise QueryExecutionError("The dataset file is missing. Import the dataset again.")

        limit = self._settings.max_result_rows
        wrapped = f"SELECT * FROM ({query.sql}) AS result_set LIMIT {limit + 1}"
        timeout = self._settings.query_timeout_seconds
        started = time.perf_counter()
        try:
            conn = self._connect(duckdb_path)
        except duckdb.Error as error:
            logger.warning("Could not open dataset: %s", type(error).__name__)
            raise QueryExecutionError("The dataset could not be opened.") from None
        timer = threading.Timer(timeout, conn.interrupt)
        timer.start()
        try:
            cursor = conn.execute(wrapped)
            columns = [d[0] for d in cursor.description]
            raw_rows = cursor.fetchall()
        except duckdb.InterruptException:
            raise QueryTimeoutError(
                f"The query took longer than {timeout:g} seconds and was stopped."
            ) from None
        except duckdb.Error as error:
            message = sanitize_error(str(error))
            logger.info("Query failed: %s", message)
            raise QueryExecutionError(message) from None
        finally:
            timer.cancel()
            conn.close()

        truncated = len(raw_rows) > limit
        rows = rows_to_jsonable(raw_rows[:limit])
        return QueryResult(
            columns=columns,
            rows=rows,
            row_count=len(rows),
            truncated=truncated,
            elapsed_ms=(time.perf_counter() - started) * 1000,
        )
