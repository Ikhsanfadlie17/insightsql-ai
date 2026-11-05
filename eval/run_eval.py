"""Text-to-SQL evaluation benchmark (PRD §9, plan Task 23).

Runs every question in ``eval/questions.yaml`` through the real pipeline (FastAPI app,
SQL validator, sandboxed DuckDB worker, analytics engine) against the sample data, then
grades the answers against references that never come from the model:

- ``result``     reference SQL written by hand, executed on the same table;
- ``retention``  the analytics engine for the stated periods;
- ``clarify`` / ``reject``  the expected behaviour (nothing executed).

Usage (needs OPENAI_API_KEY and network; costs real money, see EVALUATION.md):

    python -m eval.run_eval --model "$OPENAI_MODEL"
    python -m eval.run_eval --only agg-01,ret-01      # a cheap subset

Writes ``eval/results/<UTC timestamp>.json`` and ``.md``. Nothing here is a claim until
it has been run; the numbers in EVALUATION.md are copied from these files.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import sys
import tempfile
import time
from collections import Counter
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any, Literal

import duckdb
import yaml
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent.parent
QUESTIONS = Path(__file__).resolve().parent / "questions.yaml"
RESULTS_DIR = Path(__file__).resolve().parent / "results"
SAMPLE_CSV = ROOT / "sample_data" / "client_services.csv"

Category = Literal[
    "simple_aggregation",
    "filtering",
    "grouping",
    "date_analysis",
    "client_retention",
    "new_vs_existing",
    "multi_condition",
    "ambiguous",
    "invalid",
    "complex_business",
]
CATEGORIES: tuple[str, ...] = Category.__args__  # type: ignore[attr-defined]
RetentionMetric = Literal[
    "previous_active",
    "current_active",
    "returning",
    "new",
    "reactivated",
    "between_period",
    "not_returned",
    "existing_active",
    "retention_rate",
    "new_client_rate",
    "growth_rate",
]
_RATES = {"retention_rate", "new_client_rate", "growth_rate"}
_SQL_FAILURES = {"sql_generation_failed", "query_execution_error", "query_timeout"}


# ------------------------------------------------------------------------ question file
class PeriodSpec(BaseModel):
    start: date
    end: date  # exclusive


class ResultExpected(BaseModel):
    kind: Literal["result"]
    reference_sql: str


class RetentionExpected(BaseModel):
    kind: Literal["retention"]
    previous: PeriodSpec
    current: PeriodSpec
    metric: RetentionMetric | list[RetentionMetric]

    @property
    def metrics(self) -> list[str]:
        return [self.metric] if isinstance(self.metric, str) else list(self.metric)


class BehaviourExpected(BaseModel):
    kind: Literal["clarify", "reject"]


Expected = Annotated[
    ResultExpected | RetentionExpected | BehaviourExpected, Field(discriminator="kind")
]


class Question(BaseModel):
    id: str = Field(pattern=r"^[a-z]{3}-\d{2}$")
    category: Category
    language: Literal["id", "en"]
    question: str = Field(min_length=3)
    expected: Expected


def load_questions(path: Path = QUESTIONS) -> list[Question]:
    """Parse and validate the question file."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [Question.model_validate(item) for item in data]


# ---------------------------------------------------------------------------- grading
_ISO_LIKE = re.compile(r"^(\d{4})-(\d{2})(?:-(\d{2}))?(?:[T ]00:00(?::00(?:\.0+)?)?)?$")


def normalize(value: Any) -> Any:
    """Comparable form: numbers -> float, dates -> 'YYYY-MM-DD', text -> casefolded."""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int | float | Decimal):
        number = float(value)
        return None if math.isnan(number) else number
    if isinstance(value, datetime):
        return value.date().isoformat() if value.time() == datetime.min.time() else value
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    match = _ISO_LIKE.match(text)
    if match:
        return f"{match[1]}-{match[2]}-{match[3] or '01'}"
    return text.casefold()


def _decimals(number: float) -> int:
    text = repr(number)
    return len(text.split(".")[1]) if "." in text and "e" not in text else 0


def values_equal(reference: Any, got: Any) -> bool:
    """Equal after normalising; numbers within 1e-6, or equal to the reference rounded to
    the model's decimals (a model that rounds 12.3456 to 12.35 is not wrong)."""
    a, b = normalize(reference), normalize(got)
    if isinstance(a, float) and isinstance(b, float):
        if math.isclose(a, b, rel_tol=1e-6, abs_tol=1e-6):
            return True
        places = _decimals(b)
        return places <= 4 and round(a, places) == b
    return a == b


def _sort_key(value: Any) -> tuple[int, Any]:
    v = normalize(value)
    if v is None:
        return (0, "")
    if isinstance(v, float):
        return (1, v)
    return (2, str(v))


def _same_column(reference: list[Any], got: list[Any]) -> bool:
    a = sorted(reference, key=_sort_key)
    b = sorted(got, key=_sort_key)
    return len(a) == len(b) and all(values_equal(x, y) for x, y in zip(a, b, strict=True))


def _project(rows: list[list[Any]], indexes: list[int]) -> list[tuple[Any, ...]]:
    return [tuple(row[i] for i in indexes) for row in rows]


def _rows_equal(reference: list[tuple], got: list[tuple]) -> bool:
    key = lambda row: tuple(_sort_key(v) for v in row)  # noqa: E731
    a, b = sorted(reference, key=key), sorted(got, key=key)
    return len(a) == len(b) and all(
        all(values_equal(x, y) for x, y in zip(ra, rb, strict=True))
        for ra, rb in zip(a, b, strict=True)
    )


def _match_columns(small: list[list[Any]], big: list[list[Any]]) -> list[int] | None:
    """For each column of ``small``, a distinct column of ``big`` with the same values."""
    used: set[int] = set()
    chosen: list[int] = []
    for column in small:
        candidate = next(
            (k for k, other in enumerate(big) if k not in used and _same_column(column, other)),
            None,
        )
        if candidate is None:
            return None
        used.add(candidate)
        chosen.append(candidate)
    return chosen


def results_match(
    reference: tuple[list[str], list[list[Any]]], got: tuple[list[str], list[list[Any]]]
) -> tuple[bool, str]:
    """Order-insensitive comparison. Columns are matched by their values, so names and
    column order do not matter; extra columns on either side are allowed as long as every
    column of the narrower result appears in the wider one, row by row."""
    ref_rows, got_rows = reference[1], got[1]
    if len(ref_rows) != len(got_rows):
        return False, f"row count {len(got_rows)} != reference {len(ref_rows)}"
    if not ref_rows:
        return True, "both empty"
    ref_cols = [list(c) for c in zip(*ref_rows, strict=True)]
    got_cols = [list(c) for c in zip(*got_rows, strict=True)]
    for narrow, wide, narrow_rows, wide_rows in (
        (ref_cols, got_cols, ref_rows, got_rows),
        (got_cols, ref_cols, got_rows, ref_rows),
    ):
        mapping = _match_columns(narrow, wide)
        if mapping is not None and _rows_equal(
            _project(narrow_rows, list(range(len(narrow)))), _project(wide_rows, mapping)
        ):
            return True, "match"
    return False, "values differ"


def retention_match(expected: dict[str, Any], response: Any) -> tuple[bool, str]:
    """The answer holds the engine's value for every metric: from the retention engine,
    or (for a plain SQL answer) somewhere in the result table."""
    if response.retention is not None:
        got = response.retention.model_dump()
        missing = [m for m, v in expected.items() if not values_equal(v, got.get(m))]
        return (not missing), ("match" if not missing else f"engine values differ: {missing}")
    if response.table is None:
        return False, "no result"
    cells = [v for row in response.table.rows for v in row if isinstance(v, int | float)]
    missing = []
    for metric, value in expected.items():
        candidates = [value] if value is None else [value, value * 100]
        if metric not in _RATES:
            candidates = [value]
        if not any(values_equal(c, cell) for c in candidates for cell in cells):
            missing.append(metric)
    return (not missing), ("match (SQL)" if not missing else f"not in result: {missing}")


# ----------------------------------------------------------------------------- running
def _reference_result(duckdb_path: Path, sql: str) -> tuple[list[str], list[list[Any]]]:
    with duckdb.connect(str(duckdb_path), read_only=True) as conn:
        cursor = conn.execute(sql)
        columns = [d[0] for d in cursor.description]
        return columns, [list(r) for r in cursor.fetchall()]


def _prepare(client: TestClient) -> str:
    """Import the sample CSV with defaults and confirm the suggested mapping."""
    upload = client.post(
        "/datasets/inspect", files={"file": (SAMPLE_CSV.name, SAMPLE_CSV.read_bytes())}
    ).json()
    dataset_id = client.post("/datasets", json={"upload_id": upload["upload_id"]}).json()["id"]
    if client.get(f"/datasets/{dataset_id}").json()["status"] != "ready":
        raise RuntimeError("The sample dataset could not be imported.")
    draft = client.get(f"/datasets/{dataset_id}/column-mapping").json()["draft"]
    mapping = {k: v for k, v in draft.items() if k not in {"confirmed", "confirmed_at"}}
    response = client.put(f"/datasets/{dataset_id}/column-mapping", json=mapping)
    response.raise_for_status()
    return dataset_id


def _grade(question: Question, response: Any, app: Any, dataset_id: str) -> tuple[bool, str]:
    from backend.schemas.analytics import Period, RetentionOptions

    expected = question.expected
    if expected.kind == "clarify":
        ok = response.status == "clarification"
        return ok, response.status
    if expected.kind == "reject":
        ok = response.status == "unsupported" or response.error_code == "sql_validation_error"
        return ok, response.status if not response.error_code else response.error_code
    if response.status != "answered":
        return False, f"{response.status} ({response.error_code})"
    if expected.kind == "result":
        if response.table is None:
            return False, "no result table"
        path = app.state.dataset_service.duckdb_path(dataset_id)
        reference = _reference_result(path, expected.reference_sql)
        return results_match(reference, (response.table.columns, response.table.rows))
    r = app.state.analytics.compare(
        dataset_id,
        Period(label="previous", start=expected.previous.start, end=expected.previous.end),
        Period(label="current", start=expected.current.start, end=expected.current.end),
        RetentionOptions(),
    )
    reference = {m: getattr(r, m) for m in expected.metrics}
    return retention_match(reference, response)


def _rate(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else numerator / denominator


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    """PRD §9 metrics. A rate with an empty denominator is None (shown as N/A)."""
    graded = [r for r in records if r["kind"] in {"result", "retention"}]
    behaviour = [r for r in records if r["kind"] in {"clarify", "reject"}]
    sql_tried = [
        r
        for r in records
        if (r["status"] == "answered" and r["sql"] and not r["used_retention_engine"])
        or r["error_code"] in _SQL_FAILURES
    ]
    sql_ok = [r for r in sql_tried if r["status"] == "answered"]
    repaired = [r for r in records if r["repairs"] > 0 or r["error_code"] in _SQL_FAILURES]
    latencies = [r["latency_s"] for r in records]
    costs = [r["cost_usd"] for r in records if r["cost_usd"] is not None]
    per_category = {
        c: {
            "questions": len([r for r in records if r["category"] == c]),
            "correct": len([r for r in records if r["category"] == c and r["correct"]]),
        }
        for c in CATEGORIES
        if any(r["category"] == c for r in records)
    }
    return {
        "questions": len(records),
        "sql_execution_success_rate": _rate(len(sql_ok), len(sql_tried)),
        "sql_attempted": len(sql_tried),
        "result_accuracy": _rate(len([r for r in graded if r["correct"]]), len(graded)),
        "result_questions": len(graded),
        "semantic_accuracy": _rate(len([r for r in behaviour if r["correct"]]), len(behaviour)),
        "behaviour_questions": len(behaviour),
        "error_recovery_rate": _rate(
            len([r for r in repaired if r["status"] == "answered"]), len(repaired)
        ),
        "recovery_cases": len(repaired),
        "latency_avg_s": statistics.fmean(latencies) if latencies else None,
        "latency_median_s": statistics.median(latencies) if latencies else None,
        "llm_requests": sum(r["requests"] for r in records),
        "input_tokens": sum(r["input_tokens"] for r in records),
        "output_tokens": sum(r["output_tokens"] for r in records),
        "cost_usd": sum(costs) if costs else None,
        "cost_complete": all(r["cost_usd"] is not None for r in records),
        "per_category": per_category,
        "statuses": dict(Counter(r["status"] for r in records)),
    }


def run(questions: list[Question], settings: Any, llm_client: Any = None) -> list[dict[str, Any]]:
    """Ask every question in a fresh conversation and grade it. Returns one record each."""
    from backend.main import create_app

    app = create_app(settings, llm_client=llm_client)
    records: list[dict[str, Any]] = []
    with TestClient(app) as client:
        dataset_id = _prepare(client)
        for q in questions:
            started = time.perf_counter()
            response = app.state.orchestrator.ask(dataset_id, None, q.question)
            latency = time.perf_counter() - started
            try:
                correct, detail = _grade(q, response, app, dataset_id)
            except Exception as exc:  # a broken reference must not hide the other results
                correct, detail = False, f"grading failed: {type(exc).__name__}: {exc}"
            usage = response.usage
            records.append(
                {
                    "id": q.id,
                    "category": q.category,
                    "language": q.language,
                    "kind": q.expected.kind,
                    "question": q.question,
                    "status": response.status,
                    "error_code": response.error_code,
                    "sql": response.sql,
                    "used_retention_engine": response.retention is not None,
                    "assumptions": response.assumptions,
                    "answer": response.answer,
                    "repairs": response.repairs,
                    "correct": correct,
                    "detail": detail,
                    "latency_s": round(latency, 3),
                    "requests": usage.requests,
                    "input_tokens": usage.input_tokens,
                    "output_tokens": usage.output_tokens,
                    "cost_usd": usage.est_cost_usd if usage.cost_complete else None,
                }
            )
            print(f"{q.id:7} {'OK ' if correct else 'BAD'} {response.status:13} {detail}")
    return records


def _pct(value: float | None) -> str:
    return "N/A" if value is None else f"{value * 100:.1f}%"


def markdown(meta: dict[str, Any], summary: dict[str, Any], records: list[dict]) -> str:
    """Human-readable report (copied into EVALUATION.md by hand, never edited there)."""
    s = summary
    cost = "N/A" if s["cost_usd"] is None else f"US${s['cost_usd']:.4f}"
    lines = [
        f"# Evaluation run {meta['timestamp']}",
        "",
        f"- LLM: real ({meta['model']}, {meta['date']}) · prompt `{meta['prompt_version']}` "
        f"· questions: {s['questions']} · commit `{meta['commit']}`",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| SQL execution success rate | {_pct(s['sql_execution_success_rate'])} "
        f"({s['sql_attempted']} questions with SQL) |",
        f"| Query result accuracy | {_pct(s['result_accuracy'])} "
        f"({s['result_questions']} result/retention questions) |",
        f"| Semantic accuracy (clarify/reject) | {_pct(s['semantic_accuracy'])} "
        f"({s['behaviour_questions']} questions) |",
        f"| Error recovery rate | {_pct(s['error_recovery_rate'])} ({s['recovery_cases']} cases) |",
        f"| Latency avg / median | {s['latency_avg_s']:.1f} s / {s['latency_median_s']:.1f} s |"
        if s["latency_avg_s"] is not None
        else "| Latency | N/A |",
        f"| LLM requests / tokens in / out | {s['llm_requests']} / {s['input_tokens']:,} / "
        f"{s['output_tokens']:,} |",
        f"| Estimated cost | {cost}{'' if s['cost_complete'] else ' (incomplete)'} |",
        "",
        "| Category | Correct |",
        "|---|---|",
        *[f"| {c} | {v['correct']}/{v['questions']} |" for c, v in s["per_category"].items()],
        "",
        "| ID | Lang | Expected | Status | OK | Detail | s |",
        "|---|---|---|---|---|---|---|",
        *[
            f"| {r['id']} | {r['language']} | {r['kind']} | {r['status']} | "
            f"{'yes' if r['correct'] else '**no**'} | {r['detail']} | {r['latency_s']:.1f} |"
            for r in records
        ],
        "",
    ]
    return "\n".join(lines)


def _commit() -> str:
    head = ROOT / ".git" / "HEAD"
    try:
        ref = head.read_text().strip()
        if ref.startswith("ref: "):
            return (ROOT / ".git" / ref[5:]).read_text().strip()[:7]
        return ref[:7]
    except OSError:
        return "unknown"


def main(argv: list[str] | None = None) -> int:
    from backend.core.config import Settings
    from backend.services.llm.prompts import PROMPT_VERSION

    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--model", help="model ID (default: OPENAI_MODEL)")
    parser.add_argument("--questions", type=Path, default=QUESTIONS)
    parser.add_argument("--out", type=Path, default=RESULTS_DIR)
    parser.add_argument("--only", help="comma-separated question IDs")
    args = parser.parse_args(argv)

    questions = load_questions(args.questions)
    if args.only:
        wanted = set(args.only.split(","))
        questions = [q for q in questions if q.id in wanted]
    with tempfile.TemporaryDirectory(prefix="insightsql-eval-") as tmp:
        overrides: dict[str, Any] = {"data_dir": Path(tmp)}
        if args.model:
            overrides["openai_model"] = args.model
        settings = Settings(**overrides)
        if not settings.llm_configured:
            print("Not run: OPENAI_API_KEY and a model (OPENAI_MODEL or --model) are required.")
            return 2
        started = datetime.now(UTC)
        records = run(questions, settings)
    summary = summarize(records)
    meta = {
        "timestamp": started.strftime("%Y%m%dT%H%M%SZ"),
        "date": started.date().isoformat(),
        "model": settings.openai_model,
        "insight_model": settings.insight_model,
        "prompt_version": PROMPT_VERSION,
        "commit": _commit(),
        "questions_file": str(args.questions.relative_to(ROOT)),
    }
    args.out.mkdir(parents=True, exist_ok=True)
    stem = args.out / meta["timestamp"]
    payload = {"meta": meta, "summary": summary, "records": records}
    stem.with_suffix(".json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    stem.with_suffix(".md").write_text(markdown(meta, summary, records), encoding="utf-8")
    print(markdown(meta, summary, records).split("\n\n| Category")[0])
    print(f"\nWritten: {stem.with_suffix('.json')} and .md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
