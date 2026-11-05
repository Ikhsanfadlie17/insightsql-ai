"""Task 23: grading rules and a dry run of the runner with a scripted LLM (LLM: mock)."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from backend.core.config import Settings
from backend.schemas.llm_outputs import RetentionParams, SQLPlanOutput
from eval.run_eval import load_questions, results_match, run, summarize, values_equal
from tests.llm_fakes import FakeLLMClient

REF = (["departemen", "n"], [["Audit", 34], ["Pelatihan", 25]])


@pytest.mark.parametrize(
    ("got", "ok"),
    [
        ((["n", "dept"], [[25, "Pelatihan"], [34, "Audit"]]), True),  # order, names
        ((["d", "n", "share"], [["Audit", 34, 0.58], ["Pelatihan", 25, 0.42]]), True),  # extra
        ((["n"], [[34], [25]]), False),  # the department is missing (review H1)
        ((["d"], [["Audit"], ["Pelatihan"]]), False),  # the requested count is missing
        ((["d", "n"], [["audit", 34.0000000001], ["Pelatihan", 25]]), True),  # case, 1e-6
        ((["d", "n"], [["Audit", 34], ["Pelatihan", 26]]), False),
        ((["d", "n"], [["Audit", 34]]), False),  # row count
        ((["d", "n"], [["Audit", 25], ["Pelatihan", 34]]), False),  # values swapped per row
    ],
)
def test_results_match(got, ok: bool) -> None:
    assert results_match(REF, got)[0] is ok


def test_dates_and_rounding_normalise() -> None:
    assert values_equal(date(2025, 1, 1), "2025-01-01T00:00:00")
    assert values_equal(datetime(2025, 3, 1), "2025-03")
    assert values_equal(12.3456, 12.35)  # the model rounded to two decimals
    assert values_equal(12.3456, 12.3)  # ... or to one: still the reference, rounded
    assert not values_equal(12.3456, 12.4)  # wrong rounding
    assert not values_equal(34.2, 34.0)  # "34.0" claims one decimal of precision


def _plan(intent: str, **fields) -> SQLPlanOutput:
    base = dict(intent=intent, language="id", sql=None, retention=None, explanation="",
                chart=None, metrics=[], assumptions=[], clarification_question=None)  # fmt: skip
    return SQLPlanOutput(**{**base, **fields})


def test_dry_run_grades_a_perfect_model(settings: Settings) -> None:
    """A model that returns the references must score 100%: proves the runner and the
    references agree, without any network call."""
    by_id = {q.id: q for q in load_questions()}
    chosen = [by_id[i] for i in ("agg-01", "grp-01", "ret-01", "amb-01", "inv-01")]
    ret = by_id["ret-01"].expected
    fake = FakeLLMClient(
        responses=[
            _plan("sql", sql=by_id["agg-01"].expected.reference_sql),
            _plan("sql", sql=by_id["grp-01"].expected.reference_sql),
            _plan("retention", retention=RetentionParams(
                previous_start=str(ret.previous.start), previous_end=str(ret.previous.end),
                current_start=str(ret.current.start), current_end=str(ret.current.end),
                ytd=False)),
            _plan("clarify", clarification_question="Metrik apa?"),
            _plan("unsupported", explanation="Data tidak bisa diubah."),
        ]
    )  # fmt: skip
    configured = settings.model_copy(
        update={"openai_model": "fake-model", "enable_llm_insights": False}
    )

    records = run(chosen, configured, llm_client=fake)
    summary = summarize(records)

    assert [r["correct"] for r in records] == [True] * 5, [r["detail"] for r in records]
    assert summary["result_accuracy"] == 1.0 and summary["clarify_reject_accuracy"] == 1.0
    assert summary["sql_execution_success_rate"] == 1.0 and summary["sql_attempted"] == 2
    assert summary["error_recovery_rate"] is None  # no repairs happened: N/A, not 100%


def test_dry_run_catches_a_wrong_answer(settings: Settings) -> None:
    by_id = {q.id: q for q in load_questions()}
    fake = FakeLLMClient(
        responses=[
            _plan("sql", sql="SELECT COUNT(*) AS n FROM dataset WHERE status = 'Selesai'"),
            _plan("sql", sql="SELECT COUNT(*) AS n FROM dataset"),  # answers the ambiguous one
        ]
    )
    configured = settings.model_copy(
        update={"openai_model": "fake-model", "enable_llm_insights": False}
    )

    records = run([by_id["flt-01"], by_id["amb-01"]], configured, llm_client=fake)

    assert [r["correct"] for r in records] == [False, False]
    assert summarize(records)["clarify_reject_accuracy"] == 0.0


# --- Phase 7 review: the grader must not be lenient where it matters ------------------------
@pytest.mark.parametrize(
    ("reference", "got", "ok"),
    [
        (12.4, 12, False),  # 3% off: was accepted as "rounding" before (H2)
        (12.04, 12, True),  # rounding to an integer, 0.3% off: within the 0.5% bound
        (0.4567, 0.5, False),  # 9% off is not rounding
        (0.04, 0, False),  # a 4% rate answered as 0
        (23.4567, 23.46, True),  # real rounding, 0.002% off
        (1_234_567.0, 1_234_567, True),
        (2025, "2025", True),  # strftime('%Y') returns text (L5)
        ("KL-0019", "kl-0019 ", True),
    ],
)
def test_numeric_tolerance(reference, got, ok: bool) -> None:
    assert values_equal(reference, got) is ok


def test_column_pairing_is_not_greedy() -> None:
    """Two model columns with the same values: the right pairing must still be found."""
    reference = (["a", "b"], [[1, 2], [2, 1]])
    got = (["x", "y"], [[2, 1], [1, 2]])  # swapped per row, same multisets per column

    assert results_match(reference, got)[0] is True


class _Table:
    def __init__(self, rows):
        self.rows = rows


class _Response:
    def __init__(self, rows=None, retention=None, error_code=None, warnings=()):
        self.table = None if rows is None else _Table(rows)
        self.retention = retention
        self.error_code = error_code
        self.warnings = list(warnings)
        self.status = "error" if error_code else "answered"


def test_retention_by_sql_needs_a_one_row_answer() -> None:
    """H3: a number that happens to appear somewhere in a big table is not an answer."""
    from eval.run_eval import retention_match

    assert retention_match({"returning": 55}, _Response([[55, 87, 103]]))[0] is True
    assert retention_match({"retention_rate": 0.632}, _Response([[63.2]]))[0] is True
    assert retention_match({"returning": 12}, _Response([[2024, 12], [2025, 30]]))[0] is False
    assert retention_match({"retention_rate": 0.3}, _Response([[30, 99]]))[0] is True  # x100
    assert retention_match({"retention_rate": None}, _Response([[None]]))[0] is True  # N/A
    assert retention_match({"retention_rate": None}, _Response([[0.0]]))[0] is False


def test_a_write_blocked_by_the_validator_is_a_correct_refusal() -> None:
    """M5: the orchestrator reports a blocked write as sql_generation_failed."""
    from backend.schemas.datasets import ColumnNote
    from eval.run_eval import _blocked_by_validator

    def note(codes: str) -> ColumnNote:
        message = f"3 generated query/queries were rejected or failed ({codes})."
        return ColumnNote(level="info", code="rejected_sql", message=message)

    blocked = _Response(error_code="sql_generation_failed", warnings=[note("sql_validation_error")])
    failed = _Response(error_code="sql_generation_failed",
                       warnings=[note("query_execution_error, sql_validation_error")])  # fmt: skip

    assert _blocked_by_validator(blocked) is True
    assert _blocked_by_validator(failed) is False  # a SELECT that failed is not a refusal
    assert _blocked_by_validator(_Response(error_code="llm_error")) is False


def test_summary_separates_sql_answers_engine_answers_and_first_attempts() -> None:
    def rec(i, kind="result", correct=True, engine=False, repairs=0, status="answered"):
        return {"id": i, "category": "grouping", "kind": kind, "status": status,
                "error_code": None, "sql": "SELECT 1", "used_retention_engine": engine,
                "repairs": repairs, "correct": correct, "latency_s": 1.0, "requests": 1,
                "input_tokens": 1, "output_tokens": 1, "cost_usd": 0.0}  # fmt: skip

    s = summarize([
        rec("grp-01"), rec("grp-02", correct=False), rec("cpx-02", repairs=1),
        rec("ret-01", kind="retention", engine=True), rec("amb-01", kind="clarify"),
    ])  # fmt: skip

    assert s["sql_attempted"] == 3  # clarify/reject and engine answers are not SQL attempts
    assert s["sql_execution_success_rate"] == 1.0
    assert s["sql_first_attempt_success_rate"] == pytest.approx(2 / 3)
    assert s["result_accuracy_model_sql"] == pytest.approx(2 / 3)
    assert s["model_sql_questions"] == 3  # only answers that came from model SQL
    assert s["result_accuracy_engine_routed"] == 1.0
    assert s["result_accuracy"] == pytest.approx(3 / 4)
    assert s["clarify_reject_accuracy_without_smoke_overlap"] is None  # amb-01 overlaps


def test_unanswered_data_questions_are_not_counted_as_sql_answers() -> None:
    """A clarification for a data question is wrong, but it is not a model-SQL answer."""
    base = {"category": "new_vs_existing", "error_code": None, "used_retention_engine": False,
            "repairs": 0, "latency_s": 1.0, "requests": 1, "input_tokens": 1,
            "output_tokens": 1, "cost_usd": 0.0}  # fmt: skip
    records = [
        {**base, "id": "new-05", "kind": "retention", "status": "clarification", "sql": None,
         "correct": False},
        {**base, "id": "grp-01", "kind": "result", "status": "answered", "sql": "SELECT 1",
         "correct": True},
    ]  # fmt: skip

    s = summarize(records)

    assert s["model_sql_questions"] == 1 and s["result_accuracy_model_sql"] == 1.0
    assert s["result_accuracy"] == 0.5
    assert s["data_questions_not_answered"] == ["new-05 (clarification)"]
