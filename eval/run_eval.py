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
# Questions equal or close to the 2026-11-05 smoke test, after which SQL prompt v2 was
# written (docs/evidence/experiments/2026-11-05-first-real-api.md). Reported separately.
SMOKE_TEST_OVERLAP = frozenset({"amb-01", "dat-01", "inv-01", "ret-05", "cpx-04"})


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
_NUMERIC_TEXT = re.compile(r"^-?\d+(?:\.\d+)?$")
# A model may round; a rounded value is accepted only when it is the reference rounded to
# the model's decimals AND within this relative error (0.4567 -> 0.5 is not "rounding").
ROUNDING_REL_TOL = 0.005


def normalize(value: Any) -> Any:
    """Comparable form: numbers (and numeric text such as '2025') -> float, dates ->
    'YYYY-MM-DD', other text -> casefolded."""
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
    if _NUMERIC_TEXT.match(text):
        return float(text)
    return text.casefold()


def _decimals(number: float) -> int:
    """Decimals written by the model: 12.0 and 12 have none (Phase 7 review H2)."""
    if number.is_integer():
        return 0
    text = repr(number)
    return len(text.split(".")[1]) if "." in text and "e" not in text else 99


def values_equal(reference: Any, got: Any) -> bool:
    """Equal after normalising. Numbers: within 1e-6 (relative or absolute), or the
    reference rounded to the model's decimals with at most 0.5% relative error."""
    a, b = normalize(reference), normalize(got)
    if isinstance(a, float) and isinstance(b, float):
        if math.isclose(a, b, rel_tol=1e-6, abs_tol=1e-6):
            return True
        places = _decimals(b)
        return places <= 4 and round(a, places) == b and abs(a - b) <= ROUNDING_REL_TOL * abs(a)
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


def _assignments(candidates: list[list[int]], used: frozenset[int] = frozenset()):
    """Every way to give each reference column a distinct model column (backtracking;
    the greedy first choice could miss the right pairing — review L5)."""
    if not candidates:
        yield []
        return
    for k in candidates[0]:
        if k not in used:
            for rest in _assignments(candidates[1:], used | {k}):
                yield [k, *rest]


def results_match(
    reference: tuple[list[str], list[list[Any]]], got: tuple[list[str], list[list[Any]]]
) -> tuple[bool, str]:
    """Order-insensitive comparison. Every reference column must appear in the model's
    result, matched by values (names and order do not matter), row by row. Extra model
    columns are allowed; a missing one is wrong (Phase 7 review H1)."""
    ref_rows, got_rows = reference[1], got[1]
    if len(ref_rows) != len(got_rows):
        return False, f"row count {len(got_rows)} != reference {len(ref_rows)}"
    if not ref_rows:
        return True, "both empty"
    ref_cols = [list(c) for c in zip(*ref_rows, strict=True)]
    got_cols = [list(c) for c in zip(*got_rows, strict=True)]
    candidates = [
        [k for k, column in enumerate(got_cols) if _same_column(ref, column)] for ref in ref_cols
    ]
    missing = [reference[0][i] for i, c in enumerate(candidates) if not c]
    if missing:
        return False, f"reference column(s) not in the result: {missing}"
    expected = _project(ref_rows, list(range(len(ref_cols))))
    for mapping in _assignments(candidates):
        if _rows_equal(expected, _project(got_rows, mapping)):
            return True, "match"
    return False, "values differ row by row"


def retention_match(expected: dict[str, Any], response: Any) -> tuple[bool, str]:
    """The answer holds the engine's value for every metric. From the retention engine;
    or, for a plain SQL answer, in a ONE-ROW result (a rate may be written x100). Larger
    tables are not searched for a matching number (Phase 7 review H3)."""
    if response.retention is not None:
        got = response.retention.model_dump()
        missing = [m for m, v in expected.items() if not values_equal(v, got.get(m))]
        return (not missing), ("match" if not missing else f"engine values differ: {missing}")
    if response.table is None or len(response.table.rows) != 1:
        rows = 0 if response.table is None else len(response.table.rows)
        return False, f"SQL answer with {rows} rows (one row expected)"
    cells = response.table.rows[0]
    missing = []
    for metric, value in expected.items():
        if value is None:  # N/A (zero denominator): the cell must be empty too
            found = any(normalize(cell) is None for cell in cells)
        else:
            candidates = [value, value * 100] if metric in _RATES else [value]
            found = any(values_equal(c, cell) for c in candidates for cell in cells)
        if not found:
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


def _blocked_by_validator(response: Any) -> bool:
    """Every generated query was rejected by the validator, so nothing ran (review M5:
    the orchestrator reports this as sql_generation_failed with a rejected_sql note)."""
    if response.error_code != "sql_generation_failed":
        return False
    for warning in response.warnings:
        if warning.code == "rejected_sql":
            codes = re.search(r"\(([^)]*)\)", warning.message)
            return bool(codes) and {c.strip() for c in codes[1].split(",")} == {
                "sql_validation_error"
            }
    return False


def _grade(question: Question, response: Any, app: Any, dataset_id: str) -> tuple[bool, str]:
    from backend.schemas.analytics import Period, RetentionOptions

    expected = question.expected
    if expected.kind == "clarify":
        ok = response.status == "clarification"
        return ok, response.status
    if expected.kind == "reject":
        if response.status == "unsupported":
            return True, "unsupported"
        if _blocked_by_validator(response):
            return True, "blocked by the SQL validator (nothing ran)"
        return False, response.status if not response.error_code else response.error_code
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


def _accuracy(records: list[dict[str, Any]]) -> tuple[float | None, int, int]:
    correct = len([r for r in records if r["correct"]])
    return _rate(correct, len(records)), correct, len(records)


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    """PRD §9 metrics, each with its own denominator; an empty denominator gives None (N/A).

    - SQL execution success: questions that expect data and were answered with model SQL
      (or failed while trying); "first attempt" excludes automatic repairs.
    - Result accuracy is reported overall, for model-SQL answers only, and for answers the
      model routed to the deterministic retention engine (those test routing and period
      extraction, not SQL writing).
    - "Clarify/reject accuracy" is what this project measures for PRD "semantic accuracy":
      the right behaviour for ambiguous and invalid questions.
    """
    graded = [r for r in records if r["kind"] in {"result", "retention"}]
    behaviour = [r for r in records if r["kind"] in {"clarify", "reject"}]
    by_sql = [r for r in graded if not r["used_retention_engine"]]
    by_engine = [r for r in graded if r["used_retention_engine"]]
    sql_tried = [
        r
        for r in graded
        if (r["status"] == "answered" and r["sql"] and not r["used_retention_engine"])
        or r["error_code"] in _SQL_FAILURES
    ]
    sql_ok = [r for r in sql_tried if r["status"] == "answered"]
    sql_first = [r for r in sql_ok if r["repairs"] == 0]
    repaired = [r for r in records if r["repairs"] > 0 or r["error_code"] in _SQL_FAILURES]
    latencies = [r["latency_s"] for r in records]
    costs = [r["cost_usd"] for r in records if r["cost_usd"] is not None]
    unseen = [r for r in graded if r["id"] not in SMOKE_TEST_OVERLAP]
    unseen_behaviour = [r for r in behaviour if r["id"] not in SMOKE_TEST_OVERLAP]
    per_category = {
        c: {
            "questions": len([r for r in records if r["category"] == c]),
            "correct": len([r for r in records if r["category"] == c and r["correct"]]),
        }
        for c in CATEGORIES
        if any(r["category"] == c for r in records)
    }
    acc, acc_ok, acc_n = _accuracy(graded)
    sql_acc, sql_acc_ok, sql_acc_n = _accuracy(by_sql)
    eng_acc, eng_ok, eng_n = _accuracy(by_engine)
    beh_acc, beh_ok, beh_n = _accuracy(behaviour)
    unseen_acc, unseen_ok, unseen_n = _accuracy(unseen)
    unseen_beh, unseen_beh_ok, unseen_beh_n = _accuracy(unseen_behaviour)
    return {
        "questions": len(records),
        "sql_execution_success_rate": _rate(len(sql_ok), len(sql_tried)),
        "sql_first_attempt_success_rate": _rate(len(sql_first), len(sql_tried)),
        "sql_attempted": len(sql_tried),
        "result_accuracy": acc,
        "result_correct": acc_ok,
        "result_questions": acc_n,
        "result_accuracy_model_sql": sql_acc,
        "model_sql_correct": sql_acc_ok,
        "model_sql_questions": sql_acc_n,
        "result_accuracy_engine_routed": eng_acc,
        "engine_routed_correct": eng_ok,
        "engine_routed_questions": eng_n,
        "clarify_reject_accuracy": beh_acc,
        "behaviour_correct": beh_ok,
        "behaviour_questions": beh_n,
        "result_accuracy_without_smoke_overlap": unseen_acc,
        "without_overlap_correct": unseen_ok,
        "without_overlap_questions": unseen_n,
        "clarify_reject_accuracy_without_smoke_overlap": unseen_beh,
        "without_overlap_behaviour": f"{unseen_beh_ok}/{unseen_beh_n}",
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


MAX_RECORDED_ROWS = 100


def _table_record(response: Any) -> dict[str, Any] | None:
    """The model's result as returned (capped), so a run can be re-graded later."""
    if response.table is None or response.retention is not None:
        return None
    rows = response.table.rows[:MAX_RECORDED_ROWS]
    return {"columns": response.table.columns, "rows": rows, "row_count": response.table.row_count}


def _retention_record(response: Any) -> dict[str, Any] | None:
    r = response.retention
    if r is None:
        return None
    keep = ("previous_active", "current_active", "returning", "new", "reactivated",
            "between_period", "not_returned", "existing_active", "retention_rate",
            "new_client_rate", "growth_rate")  # fmt: skip
    return {
        "previous": [str(r.previous.start), str(r.previous.end)],
        "current": [str(r.current.start), str(r.current.end)],
        **{k: getattr(r, k) for k in keep},
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
                    "table": _table_record(response),
                    "retention": _retention_record(response),
                }
            )
            print(f"{q.id:7} {'OK ' if correct else 'BAD'} {response.status:13} {detail}")
    return records


def _pct(value: float | None) -> str:
    return "N/A" if value is None else f"{value * 100:.1f}%"


def markdown(meta: dict[str, Any], summary: dict[str, Any], records: list[dict]) -> str:
    """Human-readable report (numbers are copied into EVALUATION.md, never edited there)."""
    s = summary
    cost = "N/A" if s["cost_usd"] is None else f"US${s['cost_usd']:.4f}"
    latency = (
        f"{s['latency_avg_s']:.1f} s / {s['latency_median_s']:.1f} s"
        if s["latency_avg_s"] is not None
        else "N/A"
    )
    lines = [
        f"# Evaluation run {meta['timestamp']}",
        "",
        f"- LLM: real ({meta['model']}, {meta['date']}) · prompt `{meta['prompt_version']}` "
        f"· questions: {s['questions']} · commit `{meta['commit']}`"
        f"{' (uncommitted changes)' if meta.get('dirty') else ''}",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| SQL execution success (after repairs / first attempt) | "
        f"{_pct(s['sql_execution_success_rate'])} / {_pct(s['sql_first_attempt_success_rate'])} "
        f"({s['sql_attempted']} questions answered with model SQL) |",
        f"| Result accuracy, all | {_pct(s['result_accuracy'])} "
        f"({s['result_correct']}/{s['result_questions']}) |",
        f"| … model-SQL answers | {_pct(s['result_accuracy_model_sql'])} "
        f"({s['model_sql_correct']}/{s['model_sql_questions']}) |",
        f"| … routed to the retention engine | {_pct(s['result_accuracy_engine_routed'])} "
        f"({s['engine_routed_correct']}/{s['engine_routed_questions']}) |",
        f"| … without smoke-test overlap | {_pct(s['result_accuracy_without_smoke_overlap'])} "
        f"({s['without_overlap_correct']}/{s['without_overlap_questions']}) |",
        f"| Clarify/reject accuracy | {_pct(s['clarify_reject_accuracy'])} "
        f"({s['behaviour_correct']}/{s['behaviour_questions']}; without overlap "
        f"{s['without_overlap_behaviour']}) |",
        f"| Error recovery | {s['recovery_cases']} case(s), "
        f"{_pct(s['error_recovery_rate'])} answered |",
        f"| Latency avg / median | {latency} |",
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


def _commit() -> tuple[str, bool]:
    """HEAD and whether the working tree had uncommitted changes (review L6)."""
    import subprocess

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout.strip()

    try:
        return git("rev-parse", "--short", "HEAD"), bool(git("status", "--porcelain"))
    except (OSError, subprocess.CalledProcessError):
        return "unknown", True


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
        commit, dirty = _commit()  # before the run: the code loaded now is what runs
        records = run(questions, settings)
    summary = summarize(records)
    meta = {
        "timestamp": started.strftime("%Y%m%dT%H%M%SZ"),
        "date": started.date().isoformat(),
        "model": settings.openai_model,
        "insight_model": settings.insight_model,
        "prompt_version": PROMPT_VERSION,
        "commit": commit,
        "dirty": dirty,
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
