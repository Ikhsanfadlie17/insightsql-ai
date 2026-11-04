"""Task 11: OpenAI client — configuration, bounded retries, error mapping, no secret leaks.

These tests use a fake HTTP transport. They prove our request/response handling,
not that the real OpenAI API accepts our requests.
"""

from __future__ import annotations

import json
import logging

import httpx
import pytest
from pydantic import BaseModel, SecretStr

from backend.core.config import Settings
from backend.core.errors import (
    LLMError,
    LLMNotConfiguredError,
    LLMOutputError,
    LLMRateLimitError,
    LLMTimeoutError,
)
from backend.services.llm.openai_client import OpenAIClient

SECRET_KEY = "sk-test-SECRET-123456"
PROMPT_SENTINEL = "NIK-3201234567890123-SENTINEL"


class Answer(BaseModel):
    intent: str
    sql: str | None


def _ok_body(text: str | None = None, cached: int | None = 20) -> dict:
    payload = text if text is not None else json.dumps({"intent": "sql", "sql": "SELECT 1"})
    usage = {
        "input_tokens": 120,
        "input_tokens_details": {"cached_tokens": cached},
        "output_tokens": 30,
        "output_tokens_details": {"reasoning_tokens": 0},
        "total_tokens": 150,
    }
    return {
        "id": "resp_1",
        "object": "response",
        "created_at": 1,
        "model": "model-snapshot-2026",
        "status": "completed",
        "output": [
            {
                "type": "message",
                "id": "msg_1",
                "status": "completed",
                "role": "assistant",
                "content": [{"type": "output_text", "text": payload, "annotations": []}],
            }
        ],
        "usage": usage,
        "parallel_tool_calls": True,
        "tool_choice": "auto",
        "tools": [],
    }


@pytest.fixture
def llm_settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={
            "openai_api_key": SecretStr(SECRET_KEY),
            "openai_model": "configured-model",
            "llm_max_retries": 2,
            "llm_timeout_seconds": 5.0,
        }
    )


def _client(settings: Settings, handler, sleeps: list[float] | None = None) -> OpenAIClient:
    recorder = sleeps if sleeps is not None else []
    return OpenAIClient(
        settings,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=recorder.append,
    )


def _call(client: OpenAIClient):
    return client.complete_structured(
        system="sys", user=f"question {PROMPT_SENTINEL}", schema=Answer
    )


def test_success_parses_output_and_usage(llm_settings: Settings) -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=_ok_body())

    response = _call(_client(llm_settings, handler))

    assert response.parsed == Answer(intent="sql", sql="SELECT 1")
    assert (response.usage.input_tokens, response.usage.output_tokens) == (120, 30)
    assert response.usage.cached_tokens == 20
    assert response.model == "model-snapshot-2026"
    assert response.attempts == 1
    assert seen["body"]["model"] == "configured-model"  # from OPENAI_MODEL, not hard-coded
    assert seen["body"]["store"] is False
    assert seen["body"]["text"]["format"]["type"] == "json_schema"
    assert seen["body"]["text"]["format"]["strict"] is True


def test_model_override(llm_settings: Settings) -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["model"] = json.loads(request.content)["model"]
        return httpx.Response(200, json=_ok_body())

    _client(llm_settings, handler).complete_structured(
        system="s", user="u", schema=Answer, model="cheaper-model"
    )

    assert seen["model"] == "cheaper-model"


def test_missing_cached_token_details(llm_settings: Settings) -> None:
    response = _call(
        _client(llm_settings, lambda r: httpx.Response(200, json=_ok_body(cached=None)))
    )

    assert response.usage.cached_tokens is None


def test_not_configured_makes_no_request(settings: Settings) -> None:
    with pytest.raises(LLMNotConfiguredError):
        OpenAIClient(settings)


def test_rate_limit_is_retried_with_retry_after(llm_settings: Settings) -> None:
    responses = iter(
        [httpx.Response(429, headers={"retry-after": "2"}, json={"error": {"message": "slow"}})]
        + [httpx.Response(200, json=_ok_body())]
    )
    sleeps: list[float] = []

    response = _call(_client(llm_settings, lambda r: next(responses), sleeps))

    assert response.attempts == 2
    assert sleeps == [2.0]


def test_rate_limit_retries_are_bounded(llm_settings: Settings) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(429, json={"error": {"message": "slow down"}})

    with pytest.raises(LLMRateLimitError) as caught:
        _call(_client(llm_settings, handler))

    assert calls["n"] == 3  # 1 + llm_max_retries
    assert caught.value.attempts == 3


def test_timeout_is_retried_then_reported(llm_settings: Settings) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ReadTimeout("timed out", request=request)

    sleeps: list[float] = []
    with pytest.raises(LLMTimeoutError) as caught:
        _call(_client(llm_settings, handler, sleeps))

    assert calls["n"] == 3
    assert caught.value.attempts == 3
    assert len(sleeps) == 2
    assert all(0 < s <= 30 for s in sleeps)


def test_server_error_then_success(llm_settings: Settings) -> None:
    responses = iter(
        [
            httpx.Response(503, json={"error": {"message": "x"}}),
            httpx.Response(200, json=_ok_body()),
        ]
    )

    assert _call(_client(llm_settings, lambda r: next(responses))).attempts == 2


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_client_errors_are_not_retried(llm_settings: Settings, status: int) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(status, json={"error": {"message": f"echo {PROMPT_SENTINEL}"}})

    with pytest.raises(LLMError) as caught:
        _call(_client(llm_settings, handler))

    assert calls["n"] == 1
    assert PROMPT_SENTINEL not in caught.value.message  # provider text may echo input
    assert str(status) in caught.value.message


def test_invalid_structured_output(llm_settings: Settings) -> None:
    client = _client(llm_settings, lambda r: httpx.Response(200, json=_ok_body(text="not json")))

    with pytest.raises(LLMOutputError):
        _call(client)


def test_no_secrets_or_prompts_in_logs_or_errors(
    llm_settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": {"message": PROMPT_SENTINEL}})

    with pytest.raises(LLMError) as caught:
        _call(_client(llm_settings, handler))

    combined = caught.value.message + "\n" + caplog.text
    assert SECRET_KEY not in combined
    assert PROMPT_SENTINEL not in combined
    assert "question" not in caught.value.message
