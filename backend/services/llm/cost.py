"""Model prices (from config/pricing.json only) and cost estimation.

Costs are always *estimates*. When the provider reports no token usage, or the
model has no configured price, the cost is ``None`` and the reason is recorded;
it is never shown as if it were known.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from backend.services.llm.client import TokenUsage

logger = logging.getLogger(__name__)

CostStatus = Literal["estimated", "unavailable_no_price", "unavailable_no_usage"]


class ModelPrice(BaseModel):
    """USD per one million tokens."""

    input_per_1m_usd: float = Field(ge=0)
    output_per_1m_usd: float = Field(ge=0)
    cached_input_per_1m_usd: float | None = Field(default=None, ge=0)


class PricingConfig(BaseModel):
    """Contents of the pricing file."""

    currency: Literal["USD"] = "USD"
    updated: str | None = None
    source: str | None = None
    models: dict[str, ModelPrice] = Field(default_factory=dict)


def load_pricing(path: Path) -> PricingConfig:
    """Load prices; a missing or invalid file means "no prices" (costs show N/A)."""
    if not path.exists():
        logger.info("No pricing file at %s; cost estimates are unavailable", path.name)
        return PricingConfig()
    try:
        return PricingConfig.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (ValueError, ValidationError):
        logger.error("Pricing file %s is invalid; cost estimates are unavailable", path.name)
        return PricingConfig()


def estimate_cost(usage: TokenUsage | None, model: str, pricing: PricingConfig) -> float | None:
    """Estimated USD cost of one request, or None when it cannot be estimated."""
    price = pricing.models.get(model)
    if usage is None or price is None:
        return None
    cached = usage.cached_tokens or 0
    cached_price = (
        price.cached_input_per_1m_usd
        if price.cached_input_per_1m_usd is not None
        else price.input_per_1m_usd
    )
    total = (
        (usage.input_tokens - cached) * price.input_per_1m_usd
        + cached * cached_price
        + usage.output_tokens * price.output_per_1m_usd
    )
    return total / 1_000_000


def cost_status(usage: TokenUsage | None, model: str, pricing: PricingConfig) -> CostStatus:
    """Why a cost is (or is not) available."""
    if usage is None:
        return "unavailable_no_usage"
    if model not in pricing.models:
        return "unavailable_no_price"
    return "estimated"
