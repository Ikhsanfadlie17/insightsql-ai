"""Task 6: every page renders without exceptions against a fake backend."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from streamlit.testing.v1 import AppTest

from frontend.api_client import ApiError

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
PAGES = ["overview", "data_sources", "ai_analyst", "client_analytics", "query_history", "settings"]

READY = {
    "id": "d1",
    "name": "client_services",
    "source_filename": "client_services.csv",
    "sheet_name": None,
    "status": "ready",
    "error": None,
    "row_count": 3,
    "column_count": 2,
    "created_at": "2026-11-04T03:00:00+00:00",
}


class FakeApi:
    """Minimal stand-in for ApiClient."""

    def __init__(self, datasets: list[dict[str, Any]] | None = None, down: bool = False) -> None:
        self.datasets = datasets if datasets is not None else [READY]
        self.down = down

    def _check(self) -> None:
        if self.down:
            raise ApiError("backend_unreachable", "The backend is not reachable.", 0)

    def health(self) -> dict[str, Any]:
        self._check()
        return {"status": "ok", "llm_configured": False, "model": None}

    def list_datasets(self) -> list[dict[str, Any]]:
        self._check()
        return self.datasets

    def get_dataset(self, dataset_id: str) -> dict[str, Any]:
        self._check()
        return next(d for d in self.datasets if d["id"] == dataset_id)

    def columns(self, dataset_id: str) -> list[dict[str, Any]]:
        return [
            {"position": 0, "name": "id_klien", "original_name": "ID Klien",
             "logical_type": "text", "failed_conversions": 0},
            {"position": 1, "name": "tanggal", "original_name": "Tanggal",
             "logical_type": "date", "failed_conversions": 2},
        ]  # fmt: skip

    def preview(self, dataset_id: str, limit: int = 50) -> dict[str, Any]:
        return {
            "columns": ["id_klien", "tanggal"],
            "rows": [["KL-0001", "2026-01-15"], ["KL-0002", None], ["KL-0003", "2026-02-01"]],
            "row_count": 3,
            "truncated": False,
            "elapsed_ms": 1.0,
        }


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
    assert any("could not be converted" in w.value for w in at.warning)


def test_unbuilt_pages_do_not_show_fake_numbers() -> None:
    at = _run("client_analytics", FakeApi())

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
