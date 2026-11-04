"""OpenAI implementation of :class:`LLMClient` (Responses API + Structured Outputs).

* Key and model come only from settings (environment); nothing is hard-coded.
* The SDK's own retries are disabled; this client retries timeouts, connection
  errors, rate limits, and 5xx responses at most ``llm_max_retries`` times with
  exponential backoff, within ``llm_total_timeout_seconds`` overall. A
  ``Retry-After`` longer than the backoff cap and ``insufficient_quota`` fail fast.
  Other errors are not retried.
* The answer is parsed here (not by the SDK) so token usage is kept even when the
  answer is incomplete, refused, or invalid: those tokens are billed. The strict
  JSON schema comes from the SDK helper ``type_to_text_format_param`` (private
  module; the SDK version is pinned and tests cover the request shape).
* Requests are sent with ``store=False``.
* Prompts, responses, provider error bodies, and the API key are never logged or
  put into exception messages: provider error text can echo the prompt.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable

import httpx
import openai
import pydantic
from openai.lib._parsing._responses import type_to_text_format_param

from backend.core.config import Settings
from backend.core.errors import (
    LLMError,
    LLMNotConfiguredError,
    LLMOutputError,
    LLMQuotaError,
    LLMRateLimitError,
    LLMTimeoutError,
)
from backend.services.llm.client import LLMResponse, T, TokenUsage

logger = logging.getLogger(__name__)

BACKOFF_BASE_SECONDS = 1.0
BACKOFF_CAP_SECONDS = 20.0
_RETRYABLE = (
    openai.APITimeoutError,
    openai.APIConnectionError,
    openai.RateLimitError,
    openai.InternalServerError,
)


def _retry_after(error: Exception) -> float | None:
    response = getattr(error, "response", None)
    if response is None:
        return None
    value = response.headers.get("retry-after")
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None


def _backoff(attempt: int, error: Exception) -> float:
    hinted = _retry_after(error)
    if hinted is not None:
        return min(max(hinted, 0.0), BACKOFF_CAP_SECONDS)
    delay = BACKOFF_BASE_SECONDS * (2**attempt)
    return min(delay + random.uniform(0, delay / 2), BACKOFF_CAP_SECONDS)


def _is_quota_error(error: Exception) -> bool:
    body = getattr(error, "body", None)
    code = body.get("code") or body.get("type") if isinstance(body, dict) else None
    return isinstance(error, openai.RateLimitError) and code == "insufficient_quota"


def _usage(response: object) -> TokenUsage | None:
    usage = getattr(response, "usage", None)
    if usage is None:
        return None
    details = getattr(usage, "input_tokens_details", None)
    return TokenUsage(
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cached_tokens=getattr(details, "cached_tokens", None) if details is not None else None,
    )


def _refused(response: object) -> bool:
    for item in getattr(response, "output", None) or []:
        for content in getattr(item, "content", None) or []:
            if getattr(content, "type", None) == "refusal":
                return True
    return False


def _final_error(error: Exception, attempts: int) -> LLMError:
    if isinstance(error, openai.APITimeoutError | openai.APIConnectionError):
        return LLMTimeoutError(
            "The AI provider did not respond in time. Please try again.", attempts=attempts
        )
    if _is_quota_error(error):
        return LLMQuotaError(
            "The AI provider account has no remaining quota or credits.", attempts=attempts
        )
    if isinstance(error, openai.RateLimitError):
        return LLMRateLimitError(
            "The AI provider is rate limiting requests. Please wait and try again.",
            attempts=attempts,
        )
    status = getattr(error, "status_code", None)
    return LLMError(f"The AI provider returned an error (HTTP {status}).", attempts=attempts)


class OpenAIClient:
    """Structured-output calls to OpenAI with bounded retries."""

    def __init__(
        self,
        settings: Settings,
        http_client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not settings.llm_configured or settings.openai_api_key is None:
            raise LLMNotConfiguredError(
                "AI features are not configured: set OPENAI_API_KEY and OPENAI_MODEL."
            )
        self._settings = settings
        self._sleep = sleep
        self._client = openai.OpenAI(
            api_key=settings.openai_api_key.get_secret_value(),
            timeout=settings.llm_timeout_seconds,
            max_retries=0,
            http_client=http_client,
        )

    def complete_structured(
        self, *, system: str, user: str, schema: type[T], model: str | None = None
    ) -> LLMResponse[T]:
        """Ask for an answer that matches ``schema`` (Structured Outputs, strict)."""
        model_id = model or self._settings.openai_model
        max_attempts = self._settings.llm_max_retries + 1
        started = time.perf_counter()
        text_config = {"format": type_to_text_format_param(schema)}
        for attempt in range(max_attempts):
            attempts = attempt + 1
            try:
                response = self._client.responses.create(
                    model=model_id,
                    instructions=system,
                    input=user,
                    text=text_config,
                    store=False,
                    max_output_tokens=self._settings.llm_max_output_tokens,
                )
            except _RETRYABLE as error:
                delay = _backoff(attempt, error)
                hinted = _retry_after(error)
                elapsed = time.perf_counter() - started
                give_up = (
                    attempts >= max_attempts
                    or _is_quota_error(error)
                    or (hinted is not None and hinted > BACKOFF_CAP_SECONDS)
                    or elapsed + delay > self._settings.llm_total_timeout_seconds
                )
                if give_up:
                    raise _final_error(error, attempts) from None
                logger.warning(
                    "LLM request failed (%s); retry %d/%d in %.1fs",
                    type(error).__name__,
                    attempts,
                    max_attempts - 1,
                    delay,
                )
                self._sleep(delay)
                continue
            except openai.APIStatusError as error:
                logger.warning("LLM request rejected: HTTP %s", error.status_code)
                raise _final_error(error, attempts) from None
            except openai.OpenAIError as error:
                logger.warning("LLM request failed: %s", type(error).__name__)
                raise LLMError("The AI provider request failed.", attempts=attempts) from None
            return self._parse(response, schema, model_id, started, attempts)
        raise AssertionError("unreachable")  # pragma: no cover

    @staticmethod
    def _parse(
        response: object, schema: type[T], model_id: str, started: float, attempts: int
    ) -> LLMResponse[T]:
        usage = _usage(response)
        model_name = getattr(response, "model", None) or model_id

        def unusable(message: str) -> LLMOutputError:
            return LLMOutputError(message, attempts=attempts, usage=usage, model=model_name)

        if getattr(response, "status", None) == "incomplete":
            raise unusable("The AI answer was cut off before it was complete.")
        if _refused(response):
            raise unusable("The AI provider declined to answer this request.")
        try:
            parsed = schema.model_validate_json(getattr(response, "output_text", "") or "")
        except (pydantic.ValidationError, ValueError):
            raise unusable("The AI provider returned an answer in an unexpected format.") from None
        return LLMResponse(
            parsed=parsed,
            usage=usage,
            model=model_name,
            latency_ms=(time.perf_counter() - started) * 1000,
            attempts=attempts,
        )
