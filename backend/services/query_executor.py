"""Execute validated SQL in an isolated, resource-limited worker process.

Defense in depth on top of ``sql_validator``:

* only :class:`ValidatedSQL` objects are accepted (plain strings raise ``TypeError``);
* each query runs in a separate Python process (``query_worker.py``, isolated mode),
  so a runaway query cannot take the backend down. Some DuckDB functions build
  huge values without checking DuckDB's interrupt or ``memory_limit``; the worker
  is therefore **killed** by the parent when it exceeds the wall-clock timeout or
  its resident memory exceeds ``query_process_memory_mb``, and the OS caps its
  address space (``query_address_space_mb``, RLIMIT_AS) as a last resort;
* the worker opens the dataset ``read_only`` with external access, extension
  install/autoload, and community extensions disabled, locks the configuration,
  limits threads and DuckDB memory, and runs exactly one statement;
* results are capped at ``max_result_rows`` (``truncated``) and ``max_result_bytes``;
* errors are reported by **category** only. DuckDB error text is never forwarded,
  logged, or sent to the LLM, because it can quote cell values.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import threading
import time
from pathlib import Path

from backend.core.config import Settings
from backend.core.errors import QueryExecutionError, QueryTimeoutError
from backend.schemas.query import QueryResult
from backend.services.sql_validator import ValidatedSQL

logger = logging.getLogger(__name__)

_WORKER = Path(__file__).with_name("query_worker.py")
_POLL_SECONDS = 0.02
_MB = 1024 * 1024

ERROR_MESSAGES = {
    "conversion": "A value could not be converted to the requested type (for example text to "
    "a number or date). Use TRY_CAST, or filter out values that do not fit.",
    "binder": "The query uses a column, function, or operator with the wrong types or in an "
    "invalid way.",
    "out_of_range": "A computed value is out of range (for example an invalid date, a "
    "division by zero, or a number that is too large).",
    "invalid_input": "A function received invalid input.",
    "out_of_memory": "The query needed more memory than allowed. Aggregate earlier, filter "
    "first, or return fewer rows.",
    "resources": "The query was stopped because it used too many resources.",
    "not_allowed": "The query tried an operation that is not allowed.",
    "syntax": "The query could not be parsed by DuckDB.",
    "multiple_statements": "Only one statement can be executed.",
    "result_too_large": "The result is too large to return. Aggregate it or select fewer "
    "rows or columns.",
    "other": "The query could not be executed.",
}


def _rss_mb(pid: int) -> float:
    """Resident memory of a process in MB (Linux /proc); 0 when unavailable."""
    try:
        with open(f"/proc/{pid}/status", encoding="ascii") as status:
            for line in status:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024
    except (OSError, ValueError, IndexError):
        pass
    return 0.0


class QueryExecutor:
    """Runs :class:`ValidatedSQL` against one dataset's DuckDB file in a worker process."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _request(self, duckdb_path: Path, query: ValidatedSQL) -> str:
        settings = self._settings
        limit = settings.max_result_rows
        return json.dumps(
            {
                "path": str(duckdb_path),
                "sql": f"SELECT * FROM ({query.sql}) AS result_set LIMIT {limit + 1}",
                "config": {
                    "enable_external_access": False,
                    "autoload_known_extensions": False,
                    "autoinstall_known_extensions": False,
                    "allow_community_extensions": False,
                    "threads": settings.query_threads,
                    "memory_limit": settings.query_memory_limit,
                },
                "limit": limit,
                "max_result_bytes": settings.max_result_bytes,
                "address_space_bytes": settings.query_address_space_mb * _MB,
            }
        )

    def execute(self, duckdb_path: Path, query: ValidatedSQL) -> QueryResult:
        """Run a validated query and return at most ``max_result_rows`` rows.

        Raises:
            TypeError: If ``query`` is not a :class:`ValidatedSQL`.
            QueryTimeoutError: If the query runs longer than the configured timeout.
            QueryExecutionError: For any other failure (generic, category-based message).
        """
        if not isinstance(query, ValidatedSQL):
            raise TypeError("QueryExecutor only executes ValidatedSQL produced by validate_sql().")
        if not duckdb_path.exists():
            raise QueryExecutionError("The dataset file is missing. Import the dataset again.")

        settings = self._settings
        started = time.perf_counter()
        process = subprocess.Popen(
            [sys.executable, "-I", str(_WORKER)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        output: list[bytes] = []
        cap = settings.max_result_bytes * 2 + 4096
        reader = threading.Thread(
            target=lambda: output.append(process.stdout.read(cap)), daemon=True
        )
        reader.start()
        try:
            process.stdin.write(self._request(duckdb_path, query).encode())
            process.stdin.close()
        except BrokenPipeError:
            pass

        killed_for: str | None = None
        while process.poll() is None:
            elapsed = time.perf_counter() - started
            if elapsed > settings.query_timeout_seconds:
                killed_for = "timeout"
            elif _rss_mb(process.pid) > settings.query_process_memory_mb:
                killed_for = "out_of_memory"
            if killed_for:
                process.kill()
                break
            time.sleep(_POLL_SECONDS)
        process.wait()
        reader.join(timeout=5)
        process.stdout.close()

        if killed_for == "timeout":
            raise QueryTimeoutError(
                f"The query took longer than {settings.query_timeout_seconds:g} seconds and "
                "was stopped."
            )
        category, payload = self._parse(output[0] if output else b"", killed_for)
        if category is not None:
            logger.info("Query failed: category=%s", category)
            if category == "timeout":
                raise QueryTimeoutError(
                    f"The query took longer than {settings.query_timeout_seconds:g} seconds "
                    "and was stopped."
                )
            raise QueryExecutionError(ERROR_MESSAGES.get(category, ERROR_MESSAGES["other"]))
        return QueryResult(
            columns=payload["columns"],
            rows=payload["rows"],
            row_count=len(payload["rows"]),
            truncated=payload["truncated"],
            elapsed_ms=(time.perf_counter() - started) * 1000,
        )

    @staticmethod
    def _parse(raw: bytes, killed_for: str | None) -> tuple[str | None, dict]:
        if killed_for:
            return killed_for, {}
        try:
            payload = json.loads(raw.decode() or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError):
            payload = {}
        if not payload:
            # No answer: the worker crashed, e.g. hit the address-space limit.
            return "resources", {}
        if not payload.get("ok"):
            return str(payload.get("category", "other")), {}
        return None, payload
