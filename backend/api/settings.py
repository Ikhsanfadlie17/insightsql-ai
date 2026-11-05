"""Read-only view of the running configuration. Never returns secrets or server paths."""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

from backend.core.config import Settings
from backend.services.llm.cost import PricingConfig

router = APIRouter(tags=["settings"])


class LLMSettings(BaseModel):
    """AI configuration. ``api_key_set`` says only whether a key exists."""

    configured: bool
    api_key_set: bool
    model: str | None
    insight_model: str | None
    model_has_price: bool
    timeout_seconds: float
    total_timeout_seconds: float
    max_retries: int
    max_output_tokens: int


class LimitSettings(BaseModel):
    """Upload, query, and question limits enforced by the backend."""

    max_upload_mb: float
    query_timeout_seconds: float
    max_result_rows: int
    max_question_chars: int
    max_sql_repairs: int


class ToggleSettings(BaseModel):
    """Privacy and cost switches."""

    send_sample_values: bool
    enable_llm_insights: bool


class SettingsResponse(BaseModel):
    """``GET /settings``. Change values through environment variables (``.env``)."""

    llm: LLMSettings
    limits: LimitSettings
    toggles: ToggleSettings


def build_settings_response(settings: Settings, pricing: PricingConfig) -> SettingsResponse:
    """Copy only whitelisted, non-secret fields."""
    key = settings.openai_api_key
    return SettingsResponse(
        llm=LLMSettings(
            configured=settings.llm_configured,
            api_key_set=key is not None and bool(key.get_secret_value()),
            model=settings.openai_model or None,
            insight_model=settings.insight_model or None,
            model_has_price=bool(settings.openai_model) and settings.openai_model in pricing.models,
            timeout_seconds=settings.llm_timeout_seconds,
            total_timeout_seconds=settings.llm_total_timeout_seconds,
            max_retries=settings.llm_max_retries,
            max_output_tokens=settings.llm_max_output_tokens,
        ),
        limits=LimitSettings(
            max_upload_mb=settings.max_upload_mb,
            query_timeout_seconds=settings.query_timeout_seconds,
            max_result_rows=settings.max_result_rows,
            max_question_chars=settings.max_question_chars,
            max_sql_repairs=settings.max_sql_repairs,
        ),
        toggles=ToggleSettings(
            send_sample_values=settings.send_sample_values,
            enable_llm_insights=settings.enable_llm_insights,
        ),
    )


@router.get("/settings", response_model=SettingsResponse)
def get_settings_view(request: Request) -> SettingsResponse:
    """Models, limits, and toggles as the backend runs them (no secrets)."""
    return build_settings_response(request.app.state.settings, request.app.state.pricing)
