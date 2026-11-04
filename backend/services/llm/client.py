"""Provider-agnostic LLM interface used by the rest of the backend."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class TokenUsage(BaseModel):
    """Token counts reported by the provider (``cached_tokens`` is None if not reported)."""

    input_tokens: int
    output_tokens: int
    cached_tokens: int | None = None


@dataclass
class LLMResponse[ResponseT: BaseModel]:
    """A parsed structured response plus what is needed to account for it."""

    parsed: ResponseT
    usage: TokenUsage | None
    model: str
    latency_ms: float
    attempts: int


class LLMClient(Protocol):
    """Anything that can return a structured (Pydantic) answer to a prompt.

    Implementations must not log prompts or responses and must raise
    ``backend.core.errors.LLMError`` subclasses for provider failures.
    """

    def complete_structured(
        self, *, system: str, user: str, schema: type[T], model: str | None = None
    ) -> LLMResponse[T]: ...
