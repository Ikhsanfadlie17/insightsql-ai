"""Task 13: question -> LLM plan -> validator -> sandboxed executor -> result.

All tests use FakeLLMClient: they prove our pipeline, guards, and accounting, not
the quality of a real model or that the real OpenAI API works.
"""

from __future__ import annotations

import logging

import duckdb
import pytest

from backend.core.config import Settings
from backend.core.errors import (
    LLMNotConfiguredError,
    LLMTimeoutError,
    ValidationError,
)
from backend.repositories.dataset_repo import DatasetRepository
from backend.repositories.usage_repo import UsageRepository
from backend.schemas.llm_outputs import HistoryTurn, RetentionParams, SQLPlanOutput
from backend.schemas.mapping import MappingUpdate
from backend.services.dataset_service import DatasetService
from backend.services.llm.cost import PricingConfig
from backend.services.llm.gateway import LLMGateway
from backend.services.llm.prompts import SQL_SYSTEM_PROMPT
from backend.services.mapping_service import MappingService
from backend.services.query_executor import QueryExecutor
from backend.services.schema_context import SchemaContextBuilder
from backend.services.sql_generator import SQLGenerationFailed, TextToSQLService
from tests.conftest import SAMPLE_DATA_DIR
from tests.llm_fakes import FakeLLMClient

QUESTION_SENTINEL = "SENTINEL-QUESTION-7781"


def plan(sql: str | None = None, intent: str = "sql", **extra) -> SQLPlanOutput:
    values = {
        "intent": intent,
        "language": "id",
        "sql": sql,
        "retention": None,
        "explanation": "Menghitung jumlah.",
        "chart": None,
        "metrics": ["jumlah"],
        "assumptions": [],
        "clarification_question": None,
    }
    values.update(extra)
    return SQLPlanOutput(**values)


GOOD_SQL = (
    "SELECT departemen, COUNT(DISTINCT id_klien) AS klien FROM dataset "
    "GROUP BY departemen ORDER BY klien DESC"
)


class Env:
    """A ready sample dataset with a confirmed mapping and a pipeline using a fake LLM."""

    def __init__(self, settings: Settings, fake: FakeLLMClient | None) -> None:
        self.settings = settings.model_copy(update={"openai_model": "fake-model"})
        repo = DatasetRepository(self.settings.metadata_db_path)
        self.datasets = DatasetService(self.settings, repo)
        self.usage = UsageRepository(self.settings.metadata_db_path)
        self.fake = fake
        gateway = LLMGateway(fake, self.settings, self.usage, PricingConfig())
        self.executor = QueryExecutor(self.settings)
        self.service = TextToSQLService(
            datasets=self.datasets,
            contexts=SchemaContextBuilder(self.datasets, repo, self.settings),
            gateway=gateway,
            executor=self.executor,
            settings=self.settings,
        )
        inspection = self.datasets.inspect_upload(
            "client_services.csv", (SAMPLE_DATA_DIR / "client_services.csv").read_bytes()
        )
        record = self.datasets.create_dataset(inspection.upload_id, None, None)
        self.datasets.ingest(record.id, inspection.upload_id, None)
        self.dataset_id = record.id
        MappingService(self.datasets, repo).confirm(
            record.id,
            MappingUpdate(
                client_id="id_klien",
                client_name="nama_klien",
                transaction_date="tanggal_layanan",
                department="departemen",
                revenue="pendapatan_rp",
            ),
        )

    def ask(self, question: str = "Departemen mana yang punya klien terbanyak?", **kwargs):
        return self.service.ask(self.dataset_id, question, **kwargs)


@pytest.fixture
def make_env(settings: Settings):
    def _make(*responses, settings_update: dict | None = None, configured: bool = True) -> Env:
        local = settings.model_copy(update=settings_update or {})
        fake = FakeLLMClient(responses=list(responses)) if configured else None
        return Env(local, fake)

    return _make


# --------------------------------------------------------------------- happy path
def test_question_to_result(make_env) -> None:
    env = make_env(plan(GOOD_SQL))

    outcome = env.ask()

    assert outcome.status == "answered"
    assert outcome.result.columns == ["departemen", "klien"]
    assert outcome.result.row_count == 5
    assert outcome.repairs == 0
    assert outcome.sql.upper().startswith("SELECT")
    records = env.usage.list_requests()
    assert [r.purpose for r in records] == ["sql_generation"]
    assert records[0].dataset_id == env.dataset_id


def test_empty_result_is_an_answer_not_an_error(make_env) -> None:
    env = make_env(plan("SELECT * FROM dataset WHERE pendapatan_rp < 0"))

    outcome = env.ask("Transaksi dengan pendapatan negatif?")

    assert outcome.status == "answered"
    assert outcome.result.row_count == 0


# ------------------------------------------------------------------------ repairs
def test_unknown_column_is_repaired_once(make_env) -> None:
    env = make_env(plan("SELECT COUNT(DISTINCT nik) FROM dataset"), plan(GOOD_SQL))

    outcome = env.ask()

    assert outcome.status == "answered"
    assert outcome.repairs == 1
    assert len(env.fake.calls) == 2
    assert "unknown_column" in env.fake.calls[1].user
    assert [r.purpose for r in reversed(env.usage.list_requests())] == [
        "sql_generation",
        "sql_repair",
    ]


def test_repairs_are_bounded(make_env) -> None:
    bad = [plan("SELECT nik FROM dataset") for _ in range(5)]
    env = make_env(*bad)

    with pytest.raises(SQLGenerationFailed) as caught:
        env.ask()

    assert len(env.fake.calls) == 3  # 1 generation + max_sql_repairs (2)
    assert len(caught.value.attempts) == 3
    assert "could not" in caught.value.message.lower()


def test_repairs_can_be_disabled(make_env) -> None:
    env = make_env(plan("SELECT nik FROM dataset"), settings_update={"max_sql_repairs": 0})

    with pytest.raises(SQLGenerationFailed):
        env.ask()

    assert len(env.fake.calls) == 1


def test_destructive_sql_is_never_executed(make_env, monkeypatch: pytest.MonkeyPatch) -> None:
    env = make_env(*[plan("DROP TABLE dataset") for _ in range(3)])
    executed: list[object] = []
    monkeypatch.setattr(env.executor, "execute", lambda *a, **k: executed.append(a))

    with pytest.raises(SQLGenerationFailed):
        env.ask("Hapus semua data")

    assert executed == []
    path = env.datasets.duckdb_path(env.dataset_id)
    with duckdb.connect(str(path), read_only=True) as conn:
        assert conn.execute("SELECT COUNT(*) FROM dataset").fetchone()[0] == 795


def test_execution_error_sent_for_repair_contains_no_data(make_env) -> None:
    env = make_env(plan("SELECT CAST(id_klien AS INTEGER) AS x FROM dataset"), plan(GOOD_SQL))

    env.ask()

    repair_prompt = env.fake.calls[1].user
    assert "IT FAILED" in repair_prompt
    assert "KL-0" not in repair_prompt


def test_timeouts_are_reported_after_bounded_repairs(make_env) -> None:
    heavy = (
        "SELECT COUNT(*) FROM dataset a JOIN dataset b ON a.pendapatan_rp >= b.pendapatan_rp "
        "JOIN dataset c ON b.pendapatan_rp >= c.pendapatan_rp "
        "JOIN dataset d ON c.pendapatan_rp >= d.pendapatan_rp"
    )
    env = make_env(*[plan(heavy) for _ in range(3)], settings_update={"query_timeout_seconds": 0.2})

    with pytest.raises(SQLGenerationFailed) as caught:
        env.ask()

    assert {a.error_code for a in caught.value.attempts} == {"query_timeout"}


def test_sql_intent_without_sql_is_repaired(make_env) -> None:
    env = make_env(plan(None), plan(GOOD_SQL))

    assert env.ask().status == "answered"


# ------------------------------------------------------------------ other intents
def test_ambiguous_question_asks_for_clarification(make_env) -> None:
    question = "Maksudnya periode yang mana, 2025 atau 2026?"
    env = make_env(plan(intent="clarify", clarification_question=question))

    outcome = env.ask("Berapa klien?")

    assert outcome.status == "clarification"
    assert outcome.plan.clarification_question == question
    assert outcome.result is None
    assert len(env.fake.calls) == 1


def test_unsupported_question(make_env) -> None:
    env = make_env(plan(intent="unsupported", explanation="Bukan pertanyaan tentang data."))

    outcome = env.ask("Siapa presiden Indonesia?")

    assert outcome.status == "unsupported"
    assert outcome.result is None


def test_retention_questions_are_routed_not_generated(make_env) -> None:
    params = RetentionParams(
        previous_start="2025-01-01",
        previous_end="2026-01-01",
        current_start="2026-01-01",
        current_end="2027-01-01",
        ytd=False,
    )
    env = make_env(plan(intent="retention", retention=params))

    outcome = env.ask("Berapa klien yang kembali pada 2026?")

    assert outcome.status == "retention"
    assert outcome.plan.retention == params
    assert outcome.result is None


def test_retention_intent_with_invalid_periods_is_a_clear_failure(make_env) -> None:
    bad = RetentionParams(
        previous_start="2026-01-01",
        previous_end="2025-01-01",
        current_start="2026-01-01",
        current_end="2027-01-01",
        ytd=False,
    )
    env = make_env(plan(intent="retention", retention=bad))

    with pytest.raises(SQLGenerationFailed):
        env.ask("Bandingkan klien")


# ------------------------------------------------------------- errors and limits
def test_not_configured(make_env) -> None:
    env = make_env(configured=False)

    with pytest.raises(LLMNotConfiguredError):
        env.ask()


def test_llm_timeout_propagates_and_is_recorded(make_env) -> None:
    env = make_env(LLMTimeoutError("The AI provider did not respond in time.", attempts=3))

    with pytest.raises(LLMTimeoutError):
        env.ask()

    record = env.usage.list_requests()[0]
    assert (record.status, record.error_code, record.attempts) == ("error", "llm_timeout", 3)


@pytest.mark.parametrize("question", ["", "   ", "x" * 1001])
def test_invalid_questions_are_rejected_before_any_llm_call(make_env, question: str) -> None:
    env = make_env(plan(GOOD_SQL))

    with pytest.raises(ValidationError):
        env.ask(question)

    assert env.fake.calls == []


def test_dataset_must_be_ready(make_env) -> None:
    env = make_env(plan(GOOD_SQL))
    inspection = env.datasets.inspect_upload("x.csv", b"a\n1\n")
    pending = env.datasets.create_dataset(inspection.upload_id, None, None)

    with pytest.raises(ValidationError):
        env.service.ask(pending.id, "Berapa baris?")


# ------------------------------------------------------------------------ privacy
def test_prompts_contain_schema_and_question_but_no_records(make_env) -> None:
    env = make_env(plan(GOOD_SQL))

    env.ask(f"Berapa klien per departemen? {QUESTION_SENTINEL}")

    call = env.fake.calls[0]
    assert call.system == SQL_SYSTEM_PROMPT
    assert "<data>" in call.user and "departemen" in call.user
    assert f"<question>\nBerapa klien per departemen? {QUESTION_SENTINEL}\n</question>" in call.user
    assert "KL-0" not in call.user
    assert "PT " not in call.user


def test_history_includes_questions_and_sql_only(make_env) -> None:
    env = make_env(plan(GOOD_SQL))
    history = [HistoryTurn(question="Berapa total klien?", sql="SELECT COUNT(*) FROM dataset")]

    env.ask("Dan per departemen?", history=history)

    user = env.fake.calls[0].user
    assert "Berapa total klien?" in user
    assert "SELECT COUNT(*) FROM dataset" in user


def test_questions_and_context_are_not_logged(make_env, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    env = make_env(plan("SELECT nik FROM dataset"), plan(GOOD_SQL))

    env.ask(f"Pertanyaan {QUESTION_SENTINEL}")

    assert QUESTION_SENTINEL not in caplog.text
    assert "<data>" not in caplog.text
    assert all(QUESTION_SENTINEL not in r.model_dump_json() for r in env.usage.list_requests())


# ------------------------------------------------ OpenAI client wiring (fake HTTP)
def test_plan_schema_is_valid_for_strict_structured_outputs() -> None:
    from openai.lib._pydantic import to_strict_json_schema

    schema = to_strict_json_schema(SQLPlanOutput)

    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(SQLPlanOutput.model_fields)


def test_pipeline_with_openai_client_over_fake_http(settings: Settings) -> None:
    """Real OpenAIClient code path with a fake transport. NOT a real API test."""
    import json

    import httpx
    from pydantic import SecretStr

    from backend.services.llm.openai_client import OpenAIClient
    from tests.services.llm.test_openai_client import _ok_body

    configured = settings.model_copy(
        update={"openai_api_key": SecretStr("sk-test"), "openai_model": "configured-model"}
    )
    body = _ok_body(text=plan(GOOD_SQL).model_dump_json())
    sent: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, json=body)

    env = Env(configured, None)
    client = OpenAIClient(
        configured, http_client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    env.service._gateway._client = client  # noqa: SLF001

    outcome = env.ask()

    assert outcome.status == "answered" and outcome.result.row_count == 5
    assert sent[0]["text"]["format"]["name"] == "SQLPlanOutput"
    assert "KL-0" not in json.dumps(sent[0])
    record = env.usage.list_requests()[0]
    assert (record.input_tokens, record.cached_tokens) == (120, 20)
