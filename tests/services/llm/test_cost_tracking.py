"""Task 11: pricing configuration, per-request usage records, and the LLM gateway."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import BaseModel

from backend.core.config import Settings
from backend.core.errors import LLMNotConfiguredError, LLMRateLimitError
from backend.repositories.usage_repo import UsageRepository
from backend.services.llm.client import TokenUsage
from backend.services.llm.cost import PricingConfig, estimate_cost, load_pricing
from backend.services.llm.gateway import LLMGateway
from tests.llm_fakes import FakeLLMClient

PRICING = PricingConfig.model_validate(
    {
        "currency": "USD",
        "models": {
            "fake-model": {
                "input_per_1m_usd": 2.0,
                "cached_input_per_1m_usd": 0.5,
                "output_per_1m_usd": 8.0,
            },
            "no-cache-price": {"input_per_1m_usd": 2.0, "output_per_1m_usd": 8.0},
        },
    }
)


class Out(BaseModel):
    answer: str


# ------------------------------------------------------------------- pricing
def test_cost_with_cached_tokens() -> None:
    usage = TokenUsage(input_tokens=1000, output_tokens=500, cached_tokens=200)

    # (800 * 2 + 200 * 0.5 + 500 * 8) / 1e6
    assert estimate_cost(usage, "fake-model", PRICING) == pytest.approx(0.0057)


def test_cached_tokens_billed_at_input_price_when_no_cached_price() -> None:
    usage = TokenUsage(input_tokens=1000, output_tokens=500, cached_tokens=200)

    assert estimate_cost(usage, "no-cache-price", PRICING) == pytest.approx(0.006)


def test_unknown_model_or_missing_usage_has_no_cost() -> None:
    usage = TokenUsage(input_tokens=1000, output_tokens=500)

    assert estimate_cost(usage, "unknown-model", PRICING) is None
    assert estimate_cost(None, "fake-model", PRICING) is None


def test_load_pricing_missing_or_invalid_file_means_no_prices(tmp_path: Path) -> None:
    bad = tmp_path / "pricing.json"
    bad.write_text("{not json")

    assert load_pricing(tmp_path / "missing.json").models == {}
    assert load_pricing(bad).models == {}


def test_load_pricing_rejects_negative_prices(tmp_path: Path) -> None:
    path = tmp_path / "pricing.json"
    path.write_text(json.dumps({"models": {"m": {"input_per_1m_usd": -1, "output_per_1m_usd": 1}}}))

    assert load_pricing(path).models == {}


def test_repository_pricing_file_is_valid() -> None:
    config = load_pricing(Path(__file__).resolve().parents[3] / "config" / "pricing.json")

    assert config.currency == "USD"


# ------------------------------------------------------------------- gateway
@pytest.fixture
def repo(settings: Settings) -> UsageRepository:
    return UsageRepository(settings.metadata_db_path)


def _gateway(settings: Settings, repo: UsageRepository, client) -> LLMGateway:
    configured = settings.model_copy(update={"openai_model": "fake-model"})
    return LLMGateway(client=client, settings=configured, repo=repo, pricing=PRICING)


def test_success_is_recorded_with_tokens_and_cost(
    settings: Settings, repo: UsageRepository
) -> None:
    fake = FakeLLMClient(
        responses=[Out(answer="ok")],
        usage=TokenUsage(input_tokens=1000, output_tokens=500, cached_tokens=200),
    )

    response, record = _gateway(settings, repo, fake).complete(
        purpose="sql_generation",
        system="s",
        user="secret question text",
        schema=Out,
        dataset_id="d1",
        analysis_id=None,
    )

    stored = repo.list_requests(limit=10)
    assert response.parsed.answer == "ok"
    assert stored == [record]
    assert (record.status, record.purpose, record.model) == (
        "success",
        "sql_generation",
        "fake-model",
    )
    assert (record.input_tokens, record.output_tokens, record.cached_tokens) == (1000, 500, 200)
    assert record.est_cost_usd == pytest.approx(0.0057)
    assert record.cost_status == "estimated"
    assert (record.attempts, record.retry_count) == (1, 0)
    assert record.dataset_id == "d1"
    assert record.created_at is not None
    assert "secret question text" not in record.model_dump_json()


def test_unpriced_model_is_marked_unavailable(settings: Settings, repo: UsageRepository) -> None:
    fake = FakeLLMClient(responses=[Out(answer="ok")], model="unknown-model")

    _, record = _gateway(settings, repo, fake).complete(
        purpose="sql_generation", system="s", user="u", schema=Out
    )

    assert record.est_cost_usd is None
    assert record.cost_status == "unavailable_no_price"


def test_missing_usage_is_marked_unavailable(settings: Settings, repo: UsageRepository) -> None:
    fake = FakeLLMClient(responses=[Out(answer="ok")], usage=None)

    _, record = _gateway(settings, repo, fake).complete(
        purpose="sql_generation", system="s", user="u", schema=Out
    )

    assert record.input_tokens is None
    assert record.cost_status == "unavailable_no_usage"


def test_failure_is_recorded_and_reraised(settings: Settings, repo: UsageRepository) -> None:
    error = LLMRateLimitError("The AI provider is rate limiting requests.", attempts=3)
    fake = FakeLLMClient(responses=[error])

    with pytest.raises(LLMRateLimitError):
        _gateway(settings, repo, fake).complete(
            purpose="sql_repair", system="s", user="u", schema=Out
        )

    record = repo.list_requests(limit=1)[0]
    assert (record.status, record.error_code) == ("error", "llm_rate_limited")
    assert (record.attempts, record.retry_count) == (3, 2)
    assert record.est_cost_usd is None
    assert record.cost_status == "unavailable_no_usage"


def test_not_configured_gateway_raises_without_recording(
    settings: Settings, repo: UsageRepository
) -> None:
    with pytest.raises(LLMNotConfiguredError):
        _gateway(settings, repo, None).complete(
            purpose="sql_generation", system="s", user="u", schema=Out
        )

    assert repo.list_requests(limit=10) == []


def test_summary_separates_known_and_unknown_costs(
    settings: Settings, repo: UsageRepository
) -> None:
    fake = FakeLLMClient(
        responses=[Out(answer="a"), Out(answer="b"), LLMRateLimitError("x", attempts=2)],
        usage=TokenUsage(input_tokens=1000, output_tokens=500, cached_tokens=0),
    )
    gateway = _gateway(settings, repo, fake)
    gateway.complete(purpose="sql_generation", system="s", user="u", schema=Out)
    fake.model = "unknown-model"
    gateway.complete(purpose="insight", system="s", user="u", schema=Out)
    with pytest.raises(LLMRateLimitError):
        gateway.complete(purpose="sql_repair", system="s", user="u", schema=Out)

    summary = repo.summary()

    assert summary.totals.requests == 3
    assert (summary.totals.succeeded, summary.totals.failed) == (2, 1)
    assert summary.totals.input_tokens == 2000
    assert summary.totals.estimated_cost_usd == pytest.approx(0.006)
    assert summary.totals.requests_without_cost == 2
    assert {row.key for row in summary.by_purpose} == {"sql_generation", "insight", "sql_repair"}
    assert {row.key for row in summary.by_model} == {"fake-model", "unknown-model"}


# --- Phase 4 review regressions -------------------------------------------------------
def test_unexpected_exceptions_are_recorded(settings: Settings, repo: UsageRepository) -> None:
    """Review M2: every request is recorded, including non-LLMError failures."""
    fake = FakeLLMClient(responses=[RuntimeError("boom")])

    with pytest.raises(RuntimeError):
        _gateway(settings, repo, fake).complete(
            purpose="sql_generation", system="s", user="u", schema=Out
        )

    record = repo.list_requests(limit=1)[0]
    assert (record.status, record.error_code) == ("error", "internal_error")
    assert "boom" not in (record.error_message or "")


def test_usage_of_unusable_output_is_recorded_with_cost(
    settings: Settings, repo: UsageRepository
) -> None:
    """Review M3."""
    from backend.core.errors import LLMOutputError

    error = LLMOutputError(
        "unusable",
        attempts=1,
        usage=TokenUsage(input_tokens=1000, output_tokens=500, cached_tokens=0),
        model="fake-model",
    )
    fake = FakeLLMClient(responses=[error])

    with pytest.raises(LLMOutputError):
        _gateway(settings, repo, fake).complete(
            purpose="sql_generation", system="s", user="u", schema=Out
        )

    record = repo.list_requests(limit=1)[0]
    assert (record.status, record.input_tokens, record.output_tokens) == ("error", 1000, 500)
    assert record.est_cost_usd == pytest.approx(0.006)
    assert record.cost_status == "estimated"


def test_retry_count_is_serialized(settings: Settings, repo: UsageRepository) -> None:
    fake = FakeLLMClient(responses=[LLMRateLimitError("x", attempts=3)])

    with pytest.raises(LLMRateLimitError):
        _gateway(settings, repo, fake).complete(
            purpose="sql_generation", system="s", user="u", schema=Out
        )

    assert repo.list_requests(limit=1)[0].model_dump()["retry_count"] == 2


def test_price_falls_back_to_the_requested_model_id(
    settings: Settings, repo: UsageRepository
) -> None:
    """Providers often report a dated snapshot id; prices are usually keyed by the alias."""
    fake = FakeLLMClient(
        responses=[Out(answer="ok")],
        model="fake-model-2026-11-01",
        usage=TokenUsage(input_tokens=1000, output_tokens=500, cached_tokens=0),
    )

    _, record = _gateway(settings, repo, fake).complete(
        purpose="sql_generation", system="s", user="u", schema=Out, model="fake-model"
    )

    assert record.model == "fake-model-2026-11-01"
    assert record.est_cost_usd == pytest.approx(0.006)
