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
        ((["n"], [[34], [25]]), True),  # narrower: every model column is in the reference
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
    assert summary["result_accuracy"] == 1.0 and summary["semantic_accuracy"] == 1.0
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
    assert summarize(records)["semantic_accuracy"] == 0.0
