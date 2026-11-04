"""Fake LLM client for unit and integration tests.

Passing tests that use this fake do NOT show that the real OpenAI integration
works; they only exercise our own logic around the LLM.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from backend.services.llm.client import LLMResponse, TokenUsage


@dataclass
class FakeCall:
    """One recorded call."""

    system: str
    user: str
    schema: type[BaseModel]
    model: str | None


@dataclass
class FakeLLMClient:
    """Returns queued responses in order; an Exception in the queue is raised."""

    responses: list[BaseModel | Exception | Callable[[FakeCall], BaseModel]] = field(
        default_factory=list
    )
    usage: TokenUsage | None = field(
        default_factory=lambda: TokenUsage(input_tokens=100, output_tokens=20, cached_tokens=0)
    )
    model: str = "fake-model"
    calls: list[FakeCall] = field(default_factory=list)

    def complete_structured(
        self, *, system: str, user: str, schema: type[BaseModel], model: str | None = None
    ) -> LLMResponse[Any]:
        call = FakeCall(system=system, user=user, schema=schema, model=model)
        self.calls.append(call)
        if not self.responses:
            raise AssertionError("FakeLLMClient: no response queued for this call")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        parsed = item(call) if callable(item) and not isinstance(item, BaseModel) else item
        return LLMResponse(
            # Like a provider, report the model that answered (may differ from the alias asked).
            parsed=parsed,
            usage=self.usage,
            model=self.model,
            latency_ms=1.0,
            attempts=1,
        )
