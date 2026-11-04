"""Isolated query worker: runs ONE validated query in its own process.

Started by ``QueryExecutor`` as ``python -I query_worker.py``. Reads one JSON request
from stdin and writes one JSON response to stdout. It never returns DuckDB's error
text (it can contain cell values); only an error *category*.

Request:  {"path", "sql", "config", "limit", "max_result_bytes", "address_space_bytes"}
Response: {"ok": true, "columns": [...], "rows": [...], "truncated": bool}
          {"ok": false, "category": "conversion" | "binder" | ... }
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Run with -I (isolated): make the app package importable without PYTHONPATH.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

_CATEGORIES = {
    "ConversionException": "conversion",
    "TypeMismatchException": "binder",
    "InvalidTypeException": "binder",
    "BinderException": "binder",
    "CatalogException": "binder",
    "OutOfRangeException": "out_of_range",
    "InvalidInputException": "invalid_input",
    "OutOfMemoryException": "out_of_memory",
    "InterruptException": "timeout",
    "PermissionException": "not_allowed",
    "ParserException": "syntax",
    "SyntaxException": "syntax",
}


def _limit_address_space(limit: int) -> None:
    if not limit:
        return
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    except (ImportError, ValueError, OSError):  # pragma: no cover - non-POSIX platforms
        pass


def _run(request: dict) -> dict:
    import duckdb

    from backend.services.query_results import rows_to_jsonable

    conn = duckdb.connect(request["path"], read_only=True, config=request["config"])
    try:
        conn.execute("SET lock_configuration = true")
        if len(conn.extract_statements(request["sql"])) != 1:
            return {"ok": False, "category": "multiple_statements"}
        cursor = conn.execute(request["sql"])
        columns = [d[0] for d in cursor.description]
        raw = cursor.fetchmany(request["limit"] + 1)
    finally:
        conn.close()
    truncated = len(raw) > request["limit"]
    rows = rows_to_jsonable(raw[: request["limit"]])
    payload = {"ok": True, "columns": columns, "rows": rows, "truncated": truncated}
    if len(json.dumps(payload)) > request["max_result_bytes"]:
        return {"ok": False, "category": "result_too_large"}
    return payload


def main() -> None:
    """Entry point: one request in, one response out."""
    request = json.loads(sys.stdin.read())
    _limit_address_space(request.get("address_space_bytes", 0))
    try:
        response = _run(request)
    except MemoryError:
        response = {"ok": False, "category": "out_of_memory"}
    except Exception as error:  # noqa: BLE001 - every failure becomes a category
        response = {"ok": False, "category": _CATEGORIES.get(type(error).__name__, "other")}
    sys.stdout.write(json.dumps(response))
    sys.stdout.flush()


if __name__ == "__main__":
    main()
