"""Task 6: every page renders without exceptions against a fake backend."""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from tests.frontend.fakes import FAILED
from tests.frontend.fakes import AnalystFakeApi as FakeApi

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
PAGES = ["overview", "data_sources", "ai_analyst", "client_analytics", "query_history", "settings"]


def _run(page: str, api: FakeApi) -> AppTest:
    at = AppTest.from_file(str(FRONTEND / "views" / f"{page}.py"), default_timeout=10)
    at.session_state["api_client"] = api
    return at.run()


@pytest.mark.parametrize("page", PAGES)
def test_page_renders_with_data(page: str) -> None:
    at = _run(page, FakeApi())

    assert not at.exception, at.exception


@pytest.mark.parametrize("page", PAGES)
def test_page_renders_without_datasets(page: str) -> None:
    at = _run(page, FakeApi(datasets=[]))

    assert not at.exception, at.exception


@pytest.mark.parametrize("page", PAGES)
def test_page_survives_backend_down(page: str) -> None:
    at = _run(page, FakeApi(down=True))

    assert not at.exception, at.exception


def test_data_sources_shows_preview_and_conversion_warning() -> None:
    at = _run("data_sources", FakeApi())

    assert len(at.dataframe) >= 1
    shown = [str(e.value).replace("\\", "") for e in [*at.warning, *at.markdown]]
    assert any("could not be read as dates" in text for text in shown)


def test_unbuilt_pages_do_not_show_fake_numbers() -> None:
    at = _run("settings", FakeApi())

    assert len(at.metric) == 0
    assert any("Phase" in i.value for i in at.info)


def test_app_entrypoint_builds_navigation() -> None:
    at = AppTest.from_file(str(FRONTEND / "app.py"), default_timeout=10)
    at.session_state["api_client"] = FakeApi()

    at.run()

    assert not at.exception, at.exception


def test_no_legacy_pages_folder() -> None:
    """Regression: a frontend/pages/ folder makes Streamlit run page files directly on
    deep links (e.g. /data_sources), skipping app.py and breaking `frontend.*` imports."""
    assert not (FRONTEND / "pages").exists()


@pytest.mark.parametrize("page", PAGES)
def test_pages_with_failed_active_dataset(page: str) -> None:
    """H4: a failed (or processing) active dataset has no row/column counts."""
    at = AppTest.from_file(str(FRONTEND / "views" / f"{page}.py"), default_timeout=10)
    at.session_state["api_client"] = FakeApi(datasets=[FAILED])
    at.session_state["active_dataset_id"] = "d2"

    at.run()

    assert not at.exception, at.exception


def test_untrusted_text_is_markdown_escaped() -> None:
    """L6: headers from uploaded files must not render as markdown (e.g. remote images)."""
    from frontend.components.layout import md_escape

    escaped = md_escape("![x](http://evil/x.png) **bold** :material/home:")

    assert "![" not in escaped
    assert "**" not in escaped
    assert ":material/home:" not in escaped
