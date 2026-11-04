"""Single entry point for LLM calls: every request is recorded, success or failure."""

from __future__ import annotations

import logging
import time
import uuid
from datetime import UTC, datetime

from backend.core.config import Settings
from backend.core.errors import LLMError, LLMNotConfiguredError
from backend.repositories.usage_repo import UsageRepository
from backend.schemas.usage import UsageRecord
from backend.services.llm.client import LLMClient, LLMResponse, T
from backend.services.llm.cost import PricingConfig, cost_status, estimate_cost

logger = logging.getLogger(__name__)


class LLMGateway:
    """Wraps an :class:`LLMClient` with usage and cost tracking.

    Records contain model, purpose, tokens (incl. cached), estimated cost or the
    reason it is unavailable, status, attempts, latency, and a short error code or
    message. They never contain the prompt or the answer.
    """

    def __init__(
        self,
        client: LLMClient | None,
        settings: Settings,
        repo: UsageRepository,
        pricing: PricingConfig,
        prompt_version: str | None = None,
    ) -> None:
        self._client = client
        self._settings = settings
        self._repo = repo
        self._pricing = pricing
        self._prompt_version = prompt_version

    @property
    def configured(self) -> bool:
        """True when a provider client is available."""
        return self._client is not None

    def complete(
        self,
        *,
        purpose: str,
        system: str,
        user: str,
        schema: type[T],
        dataset_id: str | None = None,
        analysis_id: str | None = None,
        model: str | None = None,
    ) -> tuple[LLMResponse[T], UsageRecord]:
        """Run one structured request and record it.

        Raises:
            LLMNotConfiguredError: No client is configured (nothing is sent or recorded).
            LLMError: Provider failure, after the failure has been recorded.
        """
        if self._client is None:
            raise LLMNotConfiguredError(
                "AI features are not configured: set OPENAI_API_KEY and OPENAI_MODEL."
            )
        model_id = model or self._settings.openai_model or "unknown"
        started = time.perf_counter()
        base = {
            "id": uuid.uuid4().hex,
            "created_at": datetime.now(UTC),
            "purpose": purpose,
            "dataset_id": dataset_id,
            "analysis_id": analysis_id,
            "prompt_version": self._prompt_version,
        }
        try:
            response = self._client.complete_structured(
                system=system, user=user, schema=schema, model=model
            )
        except LLMError as error:
            record = UsageRecord(
                **base,
                model=model_id,
                status="error",
                attempts=error.attempts,
                cost_status="unavailable_no_usage",
                latency_ms=(time.perf_counter() - started) * 1000,
                error_code=error.code,
                error_message=error.message[:200],
            )
            self._repo.add(record)
            logger.warning("LLM %s request failed: %s", purpose, error.code)
            raise

        record = UsageRecord(
            **base,
            model=response.model,
            status="success",
            attempts=response.attempts,
            input_tokens=response.usage.input_tokens if response.usage else None,
            output_tokens=response.usage.output_tokens if response.usage else None,
            cached_tokens=response.usage.cached_tokens if response.usage else None,
            est_cost_usd=estimate_cost(response.usage, response.model, self._pricing),
            cost_status=cost_status(response.usage, response.model, self._pricing),
            latency_ms=response.latency_ms,
        )
        self._repo.add(record)
        logger.info(
            "LLM %s request ok: model=%s attempts=%d", purpose, response.model, response.attempts
        )
        return response, record
