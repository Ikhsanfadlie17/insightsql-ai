"""Task 20: download buttons on the AI Analyst, Query History, and Client Analytics pages."""

from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

from frontend.components.downloads import download_name, export_fetcher
from tests.frontend.fakes import AnalystFakeApi, analysis

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"


def _labels(at: AppTest) -> list[str]:
    return [b.proto.label for b in at.get("download_button")]


def _run(view: str, api) -> AppTest:
    at = AppTest.from_file(str(FRONTEND / "views" / f"{view}.py"), default_timeout=20)
    at.session_state["api_client"] = api
    return at.run()


def test_an_answer_offers_four_downloads() -> None:
    api = AnalystFakeApi()
    at = _run("ai_analyst", api)
    at.chat_input(key="aa_input").set_value("Klien per departemen?").run()

    assert not at.exception, at.exception
    assert _labels(at) == ["CSV", "Excel", "PDF", "SQL"]
    assert all(b.proto.deferred_file_id for b in at.get("download_button"))  # lazy


def test_a_clarification_offers_no_downloads() -> None:
    api = AnalystFakeApi()
    api.next_answer = {"status": "clarification", "table": None, "sql": None, "chart": None,
                       "insight": None, "answer": "Periode mana?"}  # fmt: skip
    at = _run("ai_analyst", api)
    at.chat_input(key="aa_input").set_value("Klien?").run()

    assert not at.exception
    assert _labels(at) == []


def test_an_answer_without_sql_offers_only_table_downloads() -> None:
    api = AnalystFakeApi()
    api.next_answer = {"sql": None}
    at = _run("ai_analyst", api)
    at.chat_input(key="aa_input").set_value("Klien?").run()

    assert _labels(at) == ["CSV", "Excel", "PDF"]


def test_query_history_answers_have_downloads() -> None:
    api = AnalystFakeApi()
    api.ask("d1", "Klien per departemen?", None)

    at = _run("query_history", api)

    assert not at.exception, at.exception
    assert _labels(at) == ["CSV", "Excel", "PDF", "SQL"]


def test_client_analytics_offers_downloads() -> None:
    at = _run("client_analytics", AnalystFakeApi())

    assert not at.exception, at.exception
    assert _labels(at) == ["CSV", "Excel", "PDF", "SQL"]


def test_fetchers_call_the_export_endpoints() -> None:
    class Recorder:
        def __init__(self) -> None:
            self.calls = []

        def export_analysis(self, analysis_id, fmt):
            self.calls.append(("analysis", analysis_id, fmt))
            return b"a"

        def export_client_analytics(self, dataset_id, previous, current, dedupe, fmt):
            self.calls.append(("clients", dataset_id, previous["label"], dedupe, fmt))
            return b"c"

    api = Recorder()
    by_analysis = export_fetcher(api, "pdf", analysis_id="a1")
    by_period = export_fetcher(
        api, "xlsx", dataset_id="d1", previous={"label": "2025"}, current={"label": "2026"},
        dedupe=False,
    )  # fmt: skip

    assert api.calls == []  # nothing is generated until the user clicks
    assert by_analysis() == b"a" and by_period() == b"c"
    assert api.calls == [("analysis", "a1", "pdf"), ("clients", "d1", "2025", False, "xlsx")]


def test_download_name() -> None:
    assert download_name("Klien per departemen?", "csv") == "klien-per-departemen.csv"
    assert download_name("日本", "pdf") == "insightsql-export.pdf"
    assert analysis()["question"]  # fixture sanity
