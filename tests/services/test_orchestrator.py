"""Task 18: the ask pipeline end to end with a fake LLM (proves our code, not the model)."""

from __future__ import annotations

import duckdb
import pytest
from fastapi.testclient import TestClient

from backend.core.config import Settings
from backend.core.errors import ValidationError
from backend.main import create_app
from backend.schemas.insights import InsightOutput
from backend.schemas.llm_outputs import ChartHint, RetentionParams, SQLPlanOutput
from tests.api.test_mapping_api import FULL
from tests.conftest import SAMPLE_DATA_DIR
from tests.llm_fakes import FakeLLMClient

DEPT_SQL = (
    "SELECT departemen, COUNT(DISTINCT id_klien) AS klien_aktif FROM dataset "
    "WHERE year(tanggal_layanan) = 2026 GROUP BY departemen ORDER BY klien_aktif DESC"
)


def plan(intent: str = "sql", sql: str | None = DEPT_SQL, **extra) -> SQLPlanOutput:
    values = {
        "intent": intent,
        "language": "id",
        "sql": sql if intent == "sql" else None,
        "retention": None,
        "explanation": "Menghitung klien unik per departemen.",
        "chart": ChartHint(type="bar", x="departemen", y=["klien_aktif"]),
        "metrics": ["klien_aktif"],
        "assumptions": ["Tahun 2026 sesuai tanggal layanan."],
        "clarification_question": None,
    }
    values.update(extra)
    return SQLPlanOutput(**values)


def insight_echo(call) -> InsightOutput:
    """An insight that only repeats the first number of the result (always grounded)."""
    import json
    import re

    data = json.loads(re.search(r"<data>\n(.*)\n</data>", call.user, re.S).group(1))
    first = next(v for row in data["rows"] for v in row if isinstance(v, int | float))
    return InsightOutput(
        summary=f"Nilai teratas adalah {first}.",
        facts=[f"Nilai teratas: {first}."],
        interpretations=[],
        recommendations=["Bandingkan dengan tahun sebelumnya."],
        limitations=[],
    )


class Env:
    def __init__(self, settings: Settings, *responses) -> None:
        self.settings = settings.model_copy(update={"openai_model": "fake-model"})
        self.fake = FakeLLMClient(responses=list(responses))
        self.app = create_app(self.settings, llm_client=self.fake)
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.dataset_id = self.upload()

    def upload(self, mapping: dict | None = None) -> str:
        data = (SAMPLE_DATA_DIR / "client_services.csv").read_bytes()
        upload = self.client.post("/datasets/inspect", files={"file": ("c.csv", data)}).json()
        dataset_id = self.client.post("/datasets", json={"upload_id": upload["upload_id"]}).json()[
            "id"
        ]
        self.client.put(f"/datasets/{dataset_id}/column-mapping", json=mapping or FULL)
        return dataset_id

    @property
    def orchestrator(self):
        return self.app.state.orchestrator

    def ask(self, question: str, conversation_id: str | None = None, dataset_id=None):
        return self.orchestrator.ask(dataset_id or self.dataset_id, conversation_id, question)

    def close(self) -> None:
        self.client.__exit__(None, None, None)


@pytest.fixture
def env_factory(settings: Settings):
    made: list[Env] = []

    def make(*responses) -> Env:
        made.append(Env(settings, *responses))
        return made[-1]

    yield make
    for env in made:
        env.close()


def test_sql_intent_returns_answer_table_chart_sql_and_insight(env_factory) -> None:
    env = env_factory(plan(), insight_echo)

    r = env.ask("Departemen mana yang punya klien terbanyak di 2026?")

    assert r.status == "answered"
    assert r.table is not None and r.table.columns == ["departemen", "klien_aktif"]
    assert r.chart is not None and r.chart.type == "bar"
    assert "COUNT(DISTINCT id_klien)" in r.sql
    assert r.insight is not None and r.insight.source == "llm"
    assert r.answer == r.insight.summary
    assert r.explanation and r.assumptions
    assert r.usage.requests == 2  # one SQL plan, one insight
    assert [c.schema.__name__ for c in env.fake.calls] == ["SQLPlanOutput", "InsightOutput"]


def test_numbers_in_the_answer_come_from_the_query(env_factory) -> None:
    env = env_factory(plan(), insight_echo)

    r = env.ask("Klien per departemen 2026")

    with duckdb.connect(str(env.app.state.dataset_service.duckdb_path(env.dataset_id))) as conn:
        expected = conn.execute(DEPT_SQL).fetchall()
    assert [tuple(row) for row in r.table.rows] == expected
    assert str(expected[0][1]) in r.answer


def test_retention_intent_uses_the_analytics_templates(env_factory) -> None:
    params = RetentionParams(
        previous_start="2025-01-01", previous_end="2026-01-01",
        current_start="2026-01-01", current_end="2027-01-01", ytd=False,
    )  # fmt: skip
    env = env_factory(plan("retention", retention=params, chart=None))
    env.settings.enable_llm_insights = False  # template insight: one LLM call in total

    r = env.ask("Bandingkan klien 2025 dengan 2026")

    direct = env.app.state.analytics.service.compare_periods(
        env.app.state.analytics.source(env.dataset_id),
        r.retention.previous,
        r.retention.current,
    )
    assert r.status == "answered" and r.retention is not None
    assert r.retention.current_active == direct.current_active
    assert r.retention.retention_rate == direct.retention_rate
    assert {k.label for k in r.kpis} >= {"Retention rate", "New", "Existing active"}
    assert r.sql and "WITH" in r.sql  # the executed template, not LLM SQL
    assert len(env.fake.calls) == 1  # no SQL was generated or sent back to the LLM
    assert r.retention.previous.label == "2025" and r.retention.current.label == "2026"
    assert r.insight.source == "deterministic"


def test_question_about_a_year_without_data(env_factory) -> None:
    params = RetentionParams(
        previous_start="2029-01-01", previous_end="2030-01-01",
        current_start="2030-01-01", current_end="2031-01-01", ytd=False,
    )  # fmt: skip
    env = env_factory(plan("retention", retention=params, chart=None))

    r = env.ask("Retensi klien 2030?")

    assert r.status == "answered"
    kpis = {k.label: k.value for k in r.kpis}
    assert kpis["Retention rate"] is None  # rendered as N/A
    assert any(w.code == "no_data" for w in r.warnings)
    assert "N/A" in r.answer


def test_clarification_executes_nothing(env_factory) -> None:
    env = env_factory(
        plan("clarify", clarification_question="Periode mana yang dimaksud?", chart=None)
    )

    r = env.ask("Bagaimana performanya?")

    assert r.status == "clarification"
    assert r.answer == "Periode mana yang dimaksud?"
    assert r.table is None and r.sql is None and r.insight is None


def test_unsupported_question(env_factory) -> None:
    env = env_factory(plan("unsupported", explanation="Pertanyaan di luar dataset.", chart=None))

    r = env.ask("Siapa presiden Indonesia?")

    assert r.status == "unsupported" and r.table is None


def test_drop_table_plan_is_rejected_and_the_table_survives(env_factory) -> None:
    drop = plan(sql="DROP TABLE dataset")
    env = env_factory(drop, drop, drop)

    r = env.ask("hapus tabel")

    assert r.status == "error" and r.error_code == "sql_generation_failed"
    assert r.table is None and r.insight is None
    with duckdb.connect(str(env.app.state.dataset_service.duckdb_path(env.dataset_id))) as conn:
        assert conn.execute("SELECT COUNT(*) FROM dataset").fetchone()[0] == 795


def test_each_analysis_is_persisted_with_sql_and_usage(env_factory) -> None:
    env = env_factory(plan(), insight_echo)

    r = env.ask("Klien per departemen 2026")
    stored = env.app.state.history_repo.get_conversation(r.conversation_id)

    assert [a.analysis_id for a in stored.analyses] == [r.analysis_id]
    assert stored.analyses[0].sql == r.sql
    assert stored.analyses[0].usage.requests == 2
    records = env.app.state.usage_repo.list_requests(10)
    assert {rec.analysis_id for rec in records} == {r.analysis_id}


def test_follow_up_includes_previous_question_and_sql_but_no_results(env_factory) -> None:
    env = env_factory(plan(), insight_echo, plan(), insight_echo)

    first = env.ask("Klien per departemen 2026")
    env.ask("Bagaimana dengan 2025?", conversation_id=first.conversation_id)

    follow_up_prompt = env.fake.calls[2].user
    assert "Klien per departemen 2026" in follow_up_prompt
    assert "COUNT(DISTINCT id_klien)" in follow_up_prompt
    top_value = str(first.table.rows[0][1])
    assert f'"klien_aktif": {top_value}' not in follow_up_prompt
    assert "Audit" not in follow_up_prompt.split("QUESTION")[0].split("PREVIOUS TURNS")[1]


def test_clarification_answer_is_part_of_the_next_turn(env_factory) -> None:
    env = env_factory(
        plan("clarify", clarification_question="Tahun berapa?", chart=None), plan(), insight_echo
    )

    first = env.ask("Klien per departemen")
    env.ask("2026", conversation_id=first.conversation_id)

    assert "Tahun berapa?" in env.fake.calls[1].user


def test_conversation_cannot_switch_datasets(env_factory) -> None:
    env = env_factory(plan(), insight_echo)
    first = env.ask("Klien per departemen 2026")
    other = env.upload()

    with pytest.raises(ValidationError, match="another dataset"):
        env.ask("Dan dataset ini?", conversation_id=first.conversation_id, dataset_id=other)
    assert len(env.fake.calls) == 2  # nothing was sent for the rejected question


def test_new_conversation_per_dataset_has_no_history(env_factory) -> None:
    env = env_factory(plan(), insight_echo, plan(), insight_echo)
    env.ask("Pertanyaan pertama yang unik XYZ")
    other = env.upload()

    env.ask("Klien per departemen", dataset_id=other)

    assert "XYZ" not in env.fake.calls[2].user
    assert "PREVIOUS TURNS: none" in env.fake.calls[2].user


def test_llm_failure_is_reported_and_persisted(env_factory) -> None:
    from backend.core.errors import LLMRateLimitError

    env = env_factory(LLMRateLimitError("The AI provider is rate limiting requests."))

    r = env.ask("Klien per departemen 2026")

    assert r.status == "error" and r.error_code == "llm_rate_limited"
    assert env.app.state.history_repo.get_conversation(r.conversation_id).analyses


def test_mapping_required_for_retention(env_factory) -> None:
    params = RetentionParams(
        previous_start="2025-01-01", previous_end="2026-01-01",
        current_start="2026-01-01", current_end="2027-01-01", ytd=False,
    )  # fmt: skip
    env = env_factory(plan("retention", retention=params, chart=None))
    unmapped = env.upload(mapping={"department": "departemen"})

    r = env.ask("Retensi 2025 vs 2026", dataset_id=unmapped)

    assert r.status == "error" and r.error_code == "mapping_required"


# --- Phase 5 review regressions -------------------------------------------------------------
def test_ytd_flag_rebuilds_comparable_periods(env_factory) -> None:
    """M7: the model asked for YTD but sent a full previous year."""
    params = RetentionParams(
        previous_start="2025-01-01", previous_end="2026-01-01",
        current_start="2026-01-01", current_end="2026-11-01", ytd=True,
    )  # fmt: skip
    env = env_factory(plan("retention", retention=params, chart=None))
    env.settings.enable_llm_insights = False

    r = env.ask("Bandingkan klien tahun lalu dengan tahun ini (year-to-date)")

    assert (str(r.retention.previous.end), str(r.retention.current.end)) == (
        "2025-11-01", "2026-11-01",
    )  # fmt: skip
    assert {k.label for k in r.kpis} >= {"Active in 2025 YTD", "Active in 2026 YTD"}
    assert not any(w.code == "unequal_period_lengths" for w in r.warnings)


def test_kpi_formats_follow_column_name_tokens() -> None:
    """M6 / L8."""
    from backend.schemas.query import QueryResult
    from backend.services.analysis_orchestrator import _result_kpis

    def fmt(name, value):
        result = QueryResult(columns=[name], rows=[[value]], row_count=1)
        return _result_kpis(result)[0].format

    assert fmt("corporate_clients", 0.0) == "number"
    assert fmt("separate_orders", 1.0) == "number"
    assert fmt("growth_rate_pct", 1.2) == "number"
    assert fmt("retention_rate", 0.63) == "pct"
    assert fmt("tahun", 2026) == "plain"


def test_failed_insight_request_is_counted_in_usage(env_factory) -> None:
    """L1."""
    from backend.core.errors import LLMTimeoutError

    env = env_factory(plan(), LLMTimeoutError("timeout"))

    r = env.ask("Klien per departemen 2026")

    assert r.insight.fallback_reason == "llm_error"
    assert r.usage.requests == 2  # the failed insight request is billed and counted


def test_periods_out_of_range_are_an_error_not_a_crash(env_factory) -> None:
    """L2."""
    params = RetentionParams(
        previous_start="2299-01-01", previous_end="2300-01-01",
        current_start="2300-01-01", current_end="2301-01-01", ytd=False,
    )  # fmt: skip
    env = env_factory(plan("retention", retention=params, chart=None))

    r = env.ask("Retensi tahun 2300?")

    assert r.status == "error" and r.error_code == "sql_generation_failed"


def test_unexpected_failure_is_stored_as_an_error(env_factory, monkeypatch) -> None:
    """L2: the question and conversation are never left without an analysis."""
    env = env_factory(plan())

    def boom(*_args, **_kwargs):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(env.app.state.text_to_sql, "ask", boom)
    r = env.ask("Klien per departemen 2026")

    assert r.status == "error" and r.error_code == "internal_error"
    assert env.app.state.history_repo.get_conversation(r.conversation_id).analyses


# --- first real-API run (2026-11-05): full year vs a partial year ---------------------------
YEARS_SQL = (
    "SELECT year(tanggal_layanan) AS tahun, COUNT(DISTINCT id_klien) AS jumlah_klien "
    "FROM dataset WHERE year(tanggal_layanan) IN ({}) GROUP BY 1 ORDER BY 1"
)


def test_comparing_a_full_year_with_a_partial_year_warns(env_factory) -> None:
    """gpt-6-luna compared all of 2025 (91) with 2026, whose data ends on 31 Oct (103)."""
    env = env_factory(plan(sql=YEARS_SQL.format("2025, 2026"), chart=None), insight_echo)

    r = env.ask("Bandingkan jumlah klien 2025 dan 2026")

    [warning] = [w for w in r.warnings if w.code == "partial_period"]
    assert warning.level == "warning"
    assert "2026" in warning.message and "2026-10-31" in warning.message
    assert "year-to-date" in warning.message
    assert "2026-10-31" in env.fake.calls[-1].user  # the AI explanation is told too


def test_complete_years_do_not_warn(env_factory) -> None:
    env = env_factory(plan(sql=YEARS_SQL.format("2024, 2025"), chart=None), insight_echo)

    r = env.ask("Bandingkan jumlah klien 2024 dan 2025")

    assert not any(w.code == "partial_period" for w in r.warnings)


def test_partial_year_check_needs_no_mapping(env_factory) -> None:
    env = env_factory(plan(sql=YEARS_SQL.format("2025, 2026"), chart=None), insight_echo)
    unmapped = env.upload(mapping={"department": "departemen"})

    r = env.ask("Bandingkan jumlah klien 2025 dan 2026", dataset_id=unmapped)

    assert r.status == "answered"  # no transaction date confirmed: no warning, no crash
    assert not any(w.code == "partial_period" for w in r.warnings)


def test_sql_prompt_routes_client_count_comparisons_to_retention() -> None:
    from backend.services.llm.prompts import PROMPT_VERSION, SQL_SYSTEM_PROMPT

    assert "number of clients" in SQL_SYSTEM_PROMPT
    assert "year-to-date" in SQL_SYSTEM_PROMPT
    assert PROMPT_VERSION != "2026-11-04.sql-v1"


@pytest.mark.parametrize(
    "restriction",
    [
        "AND EXTRACT(MONTH FROM tanggal_layanan) <= 10",  # what gpt-6-luna wrote
        "AND month(tanggal_layanan) <= 10",
        "AND strftime(tanggal_layanan, '%m') <= '10'",
        "AND dayofyear(tanggal_layanan) <= 304",
        "AND tanggal_layanan < MAKE_DATE(YEAR(tanggal_layanan), 11, 1)",  # rerun, Indonesian
        "AND date_part('month', tanggal_layanan) <= 10",
    ],
)
def test_same_span_comparisons_do_not_warn(env_factory, restriction: str) -> None:
    """Rerun 2026-11-05: the SQL already compared January-October of every year, yet the
    warning said the other years were complete."""
    sql = YEARS_SQL.format("2025, 2026").replace(" GROUP BY", f" {restriction} GROUP BY")
    env = env_factory(plan(sql=sql, chart=None), insight_echo)

    r = env.ask("How many unique clients per year?")

    assert r.status == "answered", r.error_code
    assert not any(w.code == "partial_period" for w in r.warnings)


def test_partial_year_note_ignores_full_year_date_literals() -> None:
    """A filter on whole years (1 January boundaries) does not make the spans equal."""
    from datetime import date

    from backend.schemas.query import QueryResult
    from backend.services.analysis_orchestrator import partial_year_note

    result = QueryResult(columns=["tahun", "n"], rows=[[2025, 91], [2026, 103]], row_count=2)
    whole_years = "WHERE tanggal >= DATE '2025-01-01' AND tanggal < DATE '2027-01-01'"
    same_months = "WHERE tanggal >= DATE '2025-01-01' AND tanggal < DATE '2025-11-01'"

    assert partial_year_note(result, date(2026, 10, 31), [2025, 2026], whole_years)
    assert partial_year_note(result, date(2026, 10, 31), [2025, 2026], same_months) is None
