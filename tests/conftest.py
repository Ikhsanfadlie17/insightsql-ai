"""Shared pytest fixtures."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.core.config import Settings
from backend.main import create_app

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_DATA_DIR = REPO_ROOT / "sample_data"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Isolated settings: temp data dir, no LLM credentials, no .env file."""
    return Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        openai_api_key=None,
        openai_model=None,
    )


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    """FastAPI test client bound to the isolated settings."""
    with TestClient(create_app(settings)) as test_client:
        yield test_client
