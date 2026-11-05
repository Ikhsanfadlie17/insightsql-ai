"""Task 26: the documentation stays true to the code (prompts, schema, settings, API, links)."""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

import pytest

from backend.core.config import Settings
from backend.main import create_app
from backend.repositories.db import SCHEMA
from backend.services.insight_generator import INSIGHT_PROMPT_VERSION, INSIGHT_SYSTEM_PROMPT
from backend.services.llm.prompts import PROMPT_VERSION, SQL_SYSTEM_PROMPT
from tests.conftest import REPO_ROOT

DOCS = REPO_ROOT / "docs"
README = (REPO_ROOT / "README.md").read_text(encoding="utf-8")


def _read(name: str) -> str:
    return (DOCS / name).read_text(encoding="utf-8")


def test_prompts_doc_quotes_the_current_prompts() -> None:
    prompts = _read("PROMPTS.md")
    assert SQL_SYSTEM_PROMPT.rstrip() in prompts
    assert INSIGHT_SYSTEM_PROMPT.rstrip() in prompts
    assert f"`{PROMPT_VERSION}`" in prompts and f"`{INSIGHT_PROMPT_VERSION}`" in prompts


def test_database_doc_quotes_the_current_schema() -> None:
    assert SCHEMA.strip() in _read("DATABASE.md")


@pytest.mark.parametrize("field", sorted(Settings.model_fields))
def test_every_setting_is_documented(field: str) -> None:
    name = field.upper()
    assert f"`{name}`" in README, f"{name} missing from the README configuration table"
    assert f"{name}=" in (REPO_ROOT / ".env.example").read_text(encoding="utf-8")


def test_every_api_operation_is_in_the_architecture_table() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        app = create_app(Settings(_env_file=None, data_dir=Path(tmp), openai_api_key=None))
        paths = app.openapi()["paths"]
    documented = {
        (method, re.sub(r"\{[^}]+\}", "{}", path))
        for method, path in re.findall(r"^\| `(GET|POST|PUT|PATCH|DELETE) (/\S*)`",
                                       _read("ARCHITECTURE.md"), re.MULTILINE)
    }  # fmt: skip
    missing = [
        f"{method.upper()} {path}"
        for path, methods in paths.items()
        for method in methods
        if (method.upper(), re.sub(r"\{[^}]+\}", "{}", path)) not in documented
    ]
    assert not missing, missing


@pytest.mark.parametrize(
    "document",
    ["README.md", "docs/ARCHITECTURE.md", "docs/DATABASE.md", "docs/PROMPTS.md",
     "docs/DEPLOYMENT.md", "docs/EVALUATION.md"],
)  # fmt: skip
def test_relative_links_resolve(document: str) -> None:
    path = REPO_ROOT / document
    text = path.read_text(encoding="utf-8")
    broken = []
    for target in re.findall(r"\]\(([^)\s]+)\)", text):
        if target.startswith(("http://", "https://", "#", "mailto:")):
            continue
        file_part = target.split("#", 1)[0]
        if file_part and not (path.parent / file_part).exists():
            broken.append(target)
    assert not broken, broken
