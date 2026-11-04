"""OpenAI implementation of :class:`LLMClient` (Responses API + Structured Outputs).

* Key and model come only from settings (environment); nothing is hard-coded.
* The SDK's own retries are disabled; this client retries timeouts, connection
  errors, rate limits, and 5xx responses at most ``llm_max_retries`` times with
  exponential backoff (honouring ``Retry-After`` up to a cap). Other errors are
  not retried.
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

from backend.core.config import Settings
from backend.core.errors import (
    LLMError,
    LLMNotConfiguredError,
    LLMOutputError,
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


def _final_error(error: Exception, attempts: int) -> LLMError:
    if isinstance(error, openai.APITimeoutError | openai.APIConnectionError):
        return LLMTimeoutError(
            "The AI provider did not respond in time. Please try again.", attempts=attempts
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
        for attempt in range(max_attempts):
            try:
                response = self._client.responses.parse(
                    model=model_id,
                    instructions=system,
                    input=user,
                    text_format=schema,
                    store=False,
                    max_output_tokens=self._settings.llm_max_output_tokens,
                )
            except _RETRYABLE as error:
                if attempt + 1 < max_attempts:
                    delay = _backoff(attempt, error)
                    logger.warning(
                        "LLM request failed (%s); retry %d/%d in %.1fs",
                        type(error).__name__,
                        attempt + 1,
                        max_attempts - 1,
                        delay,
                    )
                    self._sleep(delay)
                    continue
                raise _final_error(error, attempt + 1) from None
            except openai.APIStatusError as error:
                logger.warning("LLM request rejected: HTTP %s", error.status_code)
                raise _final_error(error, attempt + 1) from None
            except (openai.APIError, pydantic.ValidationError, ValueError) as error:
                logger.warning("LLM response unusable: %s", type(error).__name__)
                raise LLMOutputError(
                    "The AI provider returned an answer in an unexpected format.",
                    attempts=attempt + 1,
                ) from None

            parsed = response.output_parsed
            if parsed is None:
                raise LLMOutputError(
                    "The AI provider did not return a usable answer (it may have refused).",
                    attempts=attempt + 1,
                )
            usage = None
            if response.usage is not None:
                details = response.usage.input_tokens_details
                usage = TokenUsage(
                    input_tokens=response.usage.input_tokens,
                    output_tokens=response.usage.output_tokens,
                    cached_tokens=details.cached_tokens if details is not None else None,
                )
            return LLMResponse(
                parsed=parsed,
                usage=usage,
                model=response.model or model_id,
                latency_ms=(time.perf_counter() - started) * 1000,
                attempts=attempt + 1,
            )
        raise AssertionError("unreachable")  # pragma: no cover
