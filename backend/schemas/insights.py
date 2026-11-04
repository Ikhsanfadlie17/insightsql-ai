"""Insight models. The LLM schema is strict (every field required); the API model adds
where the text came from and why a template was used instead."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class InsightOutput(BaseModel):
    """What the insight model must return (OpenAI strict Structured Outputs)."""

    summary: str
    facts: list[str]
    interpretations: list[str]
    recommendations: list[str]
    limitations: list[str]


class Insight(BaseModel):
    """An explanation of one result, separated into facts, interpretation, and advice.

    ``source == "deterministic"`` means a template built from the result was used,
    because AI insights are off or unavailable, or the AI text could not be verified
    (``fallback_reason``; ``ungrounded_numbers`` lists numbers not found in the result).
    """

    source: Literal["llm", "deterministic"]
    language: Literal["id", "en"]
    summary: str
    facts: list[str] = Field(default_factory=list)
    interpretations: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    ungrounded_numbers: list[str] = Field(default_factory=list)
    fallback_reason: str | None = None
    llm_request_id: str | None = None
