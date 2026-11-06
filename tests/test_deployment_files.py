"""Task 25: static checks on the Docker files (Docker itself is not available on every
development machine, so these guard the properties that matter without building)."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from tests.conftest import REPO_ROOT

DOCKERFILE = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
COMPOSE = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
IGNORE = (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()


def _final_stage() -> str:
    return DOCKERFILE.split("FROM python:3.13-slim AS runtime", 1)[1]


def test_multi_stage_python_313_slim_non_root() -> None:
    assert DOCKERFILE.count("FROM python:3.13-slim") == 2
    final = _final_stage()
    assert re.search(r"^USER app$", final, re.MULTILINE)
    assert final.index("USER app") > final.index("chown app:app /app/data")
    assert "pip install" not in final  # dependencies come from the build stage only


def test_secrets_and_local_data_never_reach_the_image() -> None:
    for pattern in (".env", ".env.*", ".venv/", "data/", ".git/", "*.duckdb", ".claude/"):
        assert pattern in IGNORE, pattern
    assert ".streamlit/secrets.toml" in IGNORE  # the Dockerfile copies .streamlit/
    assert "!.env.example" in IGNORE
    copied = re.findall(r"^COPY (?!--from)(.+?) \S+$", _final_stage(), re.MULTILINE)
    assert not any(".env " in f"{c} " or c.startswith("data") for c in copied)


def test_compose_services_ports_volume_and_healthchecks() -> None:
    services = COMPOSE["services"]
    assert set(services) == {"backend", "frontend"}
    # The app has no login, so nothing is published beyond this machine by default.
    assert services["backend"]["ports"] == ["127.0.0.1:8000:8000"]
    assert services["frontend"]["ports"] == ["127.0.0.1:8501:8501"]
    assert "insightsql-data:/app/data" in services["backend"]["volumes"]
    # A fixed name: Compose would otherwise prefix it and the documented backup finds nothing.
    assert COMPOSE["volumes"]["insightsql-data"]["name"] == "insightsql-data"
    assert services["frontend"]["environment"]["BACKEND_URL"] == "http://backend:8000"
    assert services["frontend"]["depends_on"]["backend"]["condition"] == "service_healthy"
    for name, path in (("backend", "/health"), ("frontend", "/_stcore/health")):
        test = " ".join(services[name]["healthcheck"]["test"])
        assert path in test, name


def test_env_file_is_optional_and_holds_the_secrets() -> None:
    env_file = COMPOSE["services"]["backend"]["env_file"]
    assert env_file == [{"path": ".env", "required": False}]
    assert "OPENAI_API_KEY" not in str(COMPOSE)  # never written into the compose file
    assert "env_file" not in COMPOSE["services"]["frontend"]  # the frontend holds no secrets


def test_files_copied_into_the_image_exist() -> None:
    for source in re.findall(r"^COPY (?!--from)(\S+)", _final_stage(), re.MULTILINE):
        assert (REPO_ROOT / source.rstrip("/")).exists(), source
    assert Path(REPO_ROOT / "requirements.txt").exists()
