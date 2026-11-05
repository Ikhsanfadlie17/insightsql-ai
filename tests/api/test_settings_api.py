"""Task 21: ``GET /settings`` shows configuration without ever exposing secrets."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from backend.core.config import Settings
from backend.main import create_app

SECRET = "sk-test-secret-value-1234567890"


def _configured(settings: Settings, **overrides: object) -> Settings:
    return settings.model_copy(
        update={"openai_api_key": SecretStr(SECRET), "openai_model": "model-from-env", **overrides}
    )


def test_settings_without_llm(client: TestClient) -> None:
    body = client.get("/settings").json()

    assert body["llm"]["configured"] is False
    assert body["llm"]["api_key_set"] is False
    assert body["llm"]["model"] is None
    assert body["llm"]["insight_model"] is None
    assert body["llm"]["model_has_price"] is False


def test_settings_reports_models_limits_and_toggles(settings: Settings) -> None:
    configured = _configured(
        settings,
        openai_insight_model="insight-model",
        max_upload_mb=25,
        query_timeout_seconds=7.5,
        max_result_rows=1234,
        max_question_chars=800,
        max_sql_repairs=1,
        send_sample_values=False,
        enable_llm_insights=False,
    )
    with TestClient(create_app(configured)) as client:
        body = client.get("/settings").json()

    assert body["llm"] == {
        "configured": True,
        "api_key_set": True,
        "model": "model-from-env",
        "insight_model": "insight-model",
        "model_has_price": False,
        "timeout_seconds": 30.0,
        "total_timeout_seconds": 90.0,
        "max_retries": 2,
        "max_output_tokens": 1500,
    }
    assert body["limits"]["max_upload_mb"] == 25
    assert body["limits"]["query_timeout_seconds"] == 7.5
    assert body["limits"]["max_result_rows"] == 1234
    assert body["limits"]["max_question_chars"] == 800
    assert body["limits"]["max_sql_repairs"] == 1
    assert body["toggles"] == {"send_sample_values": False, "enable_llm_insights": False}


def test_insight_model_defaults_to_sql_model(settings: Settings) -> None:
    with TestClient(create_app(_configured(settings))) as client:
        body = client.get("/settings").json()

    assert body["llm"]["insight_model"] == "model-from-env"


def test_model_has_price_when_pricing_file_lists_it(settings: Settings, tmp_path: Path) -> None:
    pricing = tmp_path / "pricing.json"
    pricing.write_text(
        json.dumps({"models": {"model-from-env": {"input_per_1m_usd": 1, "output_per_1m_usd": 2}}}),
        encoding="utf-8",
    )
    with TestClient(create_app(_configured(settings, pricing_file=pricing))) as client:
        body = client.get("/settings").json()

    assert body["llm"]["model_has_price"] is True


def test_key_without_model_is_not_configured(settings: Settings) -> None:
    key_only = settings.model_copy(update={"openai_api_key": SecretStr(SECRET)})
    with TestClient(create_app(key_only)) as client:
        body = client.get("/settings").json()

    assert body["llm"]["configured"] is False
    assert body["llm"]["api_key_set"] is True


@pytest.mark.parametrize("fragment", [SECRET, SECRET[-8:], SECRET[:10]])
def test_settings_never_returns_the_key(
    settings: Settings, fragment: str, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG), TestClient(create_app(_configured(settings))) as client:
        response = client.get("/settings")

    assert response.status_code == 200
    assert fragment not in response.text
    assert fragment not in caplog.text


def test_settings_does_not_expose_server_paths(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        text = client.get("/settings").text

    assert str(settings.data_dir) not in text
    assert "pricing.json" not in text
