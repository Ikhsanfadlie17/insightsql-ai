"""Application settings loaded from environment variables (and an optional `.env`)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed runtime configuration.

    No model ID is hard-coded: AI features are enabled only when both
    ``OPENAI_API_KEY`` and ``OPENAI_MODEL`` are provided.
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    openai_api_key: SecretStr | None = None
    openai_model: str | None = None
    openai_insight_model: str | None = None
    llm_timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    llm_max_retries: int = Field(default=2, ge=0, le=5)
    llm_total_timeout_seconds: float = Field(default=90.0, gt=0, le=900)  # incl. retries
    llm_max_output_tokens: int = Field(default=1500, gt=0, le=16000)

    data_dir: Path = Path("data")
    max_upload_mb: float = Field(default=50, gt=0)
    # Raw uploads older than 24 h are removed at startup and then every interval.
    upload_cleanup_interval_minutes: float = Field(default=60, gt=0)
    query_timeout_seconds: float = Field(default=10.0, gt=0)
    max_result_rows: int = Field(default=5000, gt=0)
    query_threads: int = Field(default=2, gt=0, le=16)
    query_memory_limit: str = "512MB"  # DuckDB's own (soft) limit
    # Hard limits for the query worker process (it is killed when exceeded).
    query_process_memory_mb: int = Field(default=1024, ge=128)
    query_address_space_mb: int = Field(default=4096, ge=0)  # RLIMIT_AS; 0 disables
    max_result_bytes: int = Field(default=20_000_000, gt=0)
    max_sql_repairs: int = Field(default=2, ge=0, le=2)
    max_question_chars: int = Field(default=1000, gt=0, le=5000)
    send_sample_values: bool = True
    enable_llm_insights: bool = True
    pricing_file: Path = Path("config/pricing.json")
    log_level: str = "INFO"

    @property
    def llm_configured(self) -> bool:
        """True when an API key and a model ID are both available."""
        has_key = self.openai_api_key is not None and bool(self.openai_api_key.get_secret_value())
        return has_key and bool(self.openai_model)

    @property
    def insight_model(self) -> str | None:
        """Model used for insight generation; defaults to the SQL model."""
        return self.openai_insight_model or self.openai_model

    @property
    def max_upload_bytes(self) -> int:
        """Upload size limit in bytes."""
        return int(self.max_upload_mb * 1024 * 1024)

    @property
    def uploads_dir(self) -> Path:
        """Directory for raw uploads awaiting ingestion."""
        return self.data_dir / "uploads"

    @property
    def datasets_dir(self) -> Path:
        """Directory holding one DuckDB file per dataset."""
        return self.data_dir / "datasets"

    @property
    def metadata_db_path(self) -> Path:
        """SQLite database for metadata, history, and LLM usage."""
        return self.data_dir / "app.db"


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings instance."""
    return Settings()
