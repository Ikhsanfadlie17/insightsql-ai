"""Phase 3 UI: import options, profile, column-mapping confirmation, Overview."""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from tests.frontend.fakes import FAILED, READY, AnalyticsFakeApi, FakeApi

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"


def _run(page: str, api: FakeApi, **state) -> AppTest:
    at = AppTest.from_file(str(FRONTEND / "views" / f"{page}.py"), default_timeout=15)
    at.session_state["api_client"] = api
    for key, value in state.items():
        at.session_state[key] = value
    return at.run()


def _text(at: AppTest) -> str:
    parts = [str(e.value) for e in (*at.markdown, *at.warning, *at.info, *at.caption, *at.error)]
    parts += [f"{m.label} {m.value}" for m in at.metric]
    # Messages are markdown-escaped before rendering; compare against the visible text.
    return "\n".join(parts).replace("\\", "")


# ----------------------------------------------------------------- Overview
def test_overview_unique_clients_after_confirmation() -> None:
    at = _run("overview", AnalyticsFakeApi())

    metrics = {m.label: m.value for m in at.metric}
    assert not at.exception
    assert metrics["Unique clients"] == "3"
    assert metrics["Duplicate rows"] == "1"
    assert (metrics["First transaction"], metrics["Last transaction"]) == (
        "2026-01-15",
        "2026-02-01",
    )


def test_overview_without_confirmed_mapping_shows_no_client_count() -> None:
    at = _run("overview", FakeApi(confirmed=False))

    metrics = {m.label: m.value for m in at.metric}
    assert not at.exception
    assert metrics["Unique clients"] == "Not set"
    assert "Confirm the column mapping" in _text(at)


@pytest.mark.parametrize("endpoint", ["profile", "column_mapping", "health"])
def test_overview_survives_partial_backend_failures(endpoint: str) -> None:
    at = _run("overview", FakeApi(failing={endpoint}))

    assert not at.exception, at.exception


def test_overview_with_only_failed_datasets() -> None:
    at = _run("overview", FakeApi(datasets=[FAILED]))

    assert not at.exception
    assert "No dataset is ready yet" in _text(at)


# ------------------------------------------------------------- Data Sources
def test_data_sources_shows_profile_and_quality_notes() -> None:
    at = _run("data_sources", FakeApi())

    text = _text(at)
    assert not at.exception
    assert "1 row(s) are duplicates." in text
    assert "could not be read as dates" in text
    assert any(m.label == "Duplicate rows" for m in at.metric)


def test_mapping_tab_requires_confirmation() -> None:
    at = _run("data_sources", FakeApi(confirmed=False))

    text = _text(at)
    assert "not confirmed" in text.lower()
    assert "New and Reactivated depend on full history." in text


def test_confirming_mapping_sends_the_selection() -> None:
    api = FakeApi(confirmed=False)
    at = _run("data_sources", api)

    next(b for b in at.button if b.label == "Confirm column mapping").click()
    at.run()

    assert not at.exception, at.exception
    assert api.confirm_calls
    sent = api.confirm_calls[0]
    assert (sent["client_id"], sent["transaction_date"]) == ("id_klien", "tanggal")
    assert set(sent) == {
        "client_id",
        "client_name",
        "transaction_date",
        "department",
        "service_type",
        "revenue",
        "status",
    }


def test_confirmed_mapping_is_shown_as_confirmed() -> None:
    at = _run("data_sources", FakeApi(confirmed=True))

    assert "confirmed" in _text(at).lower()
    assert not at.exception


@pytest.mark.parametrize("endpoint", ["profile", "column_mapping", "columns", "preview"])
def test_data_sources_survives_partial_backend_failures(endpoint: str) -> None:
    at = _run("data_sources", FakeApi(failing={endpoint}))

    assert not at.exception, at.exception


def test_ready_dataset_list_is_unchanged_by_failed_ones() -> None:
    at = _run("data_sources", FakeApi(datasets=[FAILED, READY]))

    assert not at.exception


def test_status_is_refreshed_after_confirming() -> None:
    """The header must not keep saying "not confirmed" after a successful confirm."""
    api = FakeApi(confirmed=False)
    at = _run("data_sources", api)

    next(b for b in at.button if b.label == "Confirm column mapping").click()
    at.run()

    text = _text(at).lower()
    assert "column mapping confirmed" in text
    assert "not confirmed" not in text
    assert "3 unique clients" in text


def test_role_options_show_original_headers() -> None:
    at = _run("data_sources", FakeApi())

    client_id = next(s for s in at.selectbox if s.label == "Client ID")
    assert "ID Klien" in client_id.options
