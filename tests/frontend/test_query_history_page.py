"""Task 19: Query History page and Overview recent activity against a fake backend."""

from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

from tests.frontend.fakes import READY, AnalystFakeApi

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
OTHER = {**READY, "id": "d3", "name": "Data lain"}


def _api_with_history() -> AnalystFakeApi:
    """c1 (d1, two turns), c2 (d1, newest of d1), c3 (d3)."""
    api = AnalystFakeApi(datasets=[READY, OTHER])
    first = api.ask("d1", "Klien per departemen?", None)["conversation_id"]
    api.ask("d1", "Bagaimana per tahun?", first)
    api.ask("d1", "Pendapatan per layanan?", None)
    api.ask("d3", "Pertanyaan dataset lain?", None)
    return api


def _page(api: AnalystFakeApi, name: str = "query_history") -> AppTest:
    at = AppTest.from_file(str(FRONTEND / "views" / f"{name}.py"), default_timeout=20)
    at.session_state["api_client"] = api
    return at.run()


def _text(at: AppTest) -> str:
    parts = [str(e.value) for e in (*at.markdown, *at.warning, *at.info, *at.caption, *at.error)]
    parts += [str(e.value) for e in (*at.success, *at.subheader)]
    return "\n".join(parts).replace("\\", "")


def _picker(at: AppTest):
    return next(s for s in at.selectbox if s.label == "Open conversation")


def test_empty_history_explains_where_questions_come_from() -> None:
    at = _page(AnalystFakeApi())

    assert not at.exception, at.exception
    assert "AI Analyst" in _text(at)
    assert len(at.metric) == 0 and len(at.dataframe) == 0


def test_lists_only_the_selected_datasets_conversations() -> None:
    at = _page(_api_with_history())

    assert not at.exception, at.exception
    table = at.dataframe[0].value
    assert list(table["Title"]) == ["Pendapatan per layanan?", "Klien per departemen?"]
    assert list(table["Questions"]) == [1, 2]
    assert "Pertanyaan dataset lain?" not in _text(at)
    assert "Pendapatan per layanan?" in _text(at)  # the newest one is opened


def test_open_another_conversation_shows_all_its_turns() -> None:
    at = _page(_api_with_history())

    _picker(at).set_value("c1").run()

    text = _text(at)
    assert "Klien per departemen?" in text and "Bagaimana per tahun?" in text


def test_search_filters_by_any_question() -> None:
    at = _page(_api_with_history())

    at.text_input(key="qh_search").set_value("TAHUN").run()

    assert list(at.dataframe[0].value["Title"]) == ["Klien per departemen?"]


def test_search_without_matches() -> None:
    at = _page(_api_with_history())

    at.text_input(key="qh_search").set_value("tidak ada").run()

    assert not at.exception
    assert "No conversation matches" in _text(at)


def test_delete_asks_for_confirmation_first() -> None:
    api = _api_with_history()
    at = _page(api)

    at.button(key="qh_delete").click().run()
    assert api.delete_calls == []
    assert "cannot be undone" in _text(at)

    at.button(key="qh_delete_cancel").click().run()
    assert api.delete_calls == [] and "cannot be undone" not in _text(at)

    at.button(key="qh_delete").click().run()
    at.button(key="qh_delete_confirm").click().run()

    assert not at.exception, at.exception
    assert api.delete_calls == ["c2"]
    assert "Conversation deleted" in _text(at)
    assert list(at.dataframe[0].value["Title"]) == ["Klien per departemen?"]


def test_confirmation_does_not_carry_over_to_another_conversation() -> None:
    api = _api_with_history()
    at = _page(api)

    at.button(key="qh_delete").click().run()
    _picker(at).set_value("c1").run()

    assert "cannot be undone" not in _text(at)


def test_delete_failure_is_shown() -> None:
    api = _api_with_history()
    api.failing = {"delete_conversation"}
    at = _page(api)

    at.button(key="qh_delete").click().run()
    at.button(key="qh_delete_confirm").click().run()

    assert not at.exception
    assert "delete_conversation failed" in _text(at)


def test_continue_opens_the_conversation_in_ai_analyst() -> None:
    api = _api_with_history()
    at = AppTest.from_file(str(FRONTEND / "app.py"), default_timeout=20)
    at.session_state["api_client"] = api
    at.run()
    at.switch_page("views/query_history.py").run()
    _picker(at).set_value("c1").run()

    at.button(key="qh_continue").click().run()

    assert not at.exception, at.exception
    assert at.session_state["aa_conversation_by_dataset"]["d1"] == "c1"
    assert at.chat_input(key="aa_input") is not None  # now on the AI Analyst page
    assert "Bagaimana per tahun?" in _text(at)


def test_backend_down() -> None:
    at = _page(AnalystFakeApi(down=True))

    assert not at.exception
    assert "not reachable" in _text(at)


def test_overview_lists_recent_activity_across_datasets() -> None:
    at = _page(_api_with_history(), "overview")

    assert not at.exception, at.exception
    text = _text(at)
    assert "Recent activity" in text
    assert "Pertanyaan dataset lain?" in text and "Data lain" in text
    assert "Phase 6" not in text


def test_overview_recent_activity_empty_and_failing() -> None:
    empty = _page(AnalystFakeApi(), "overview")
    failing_api = _api_with_history()
    failing_api.failing = {"recent_analyses"}
    failing = _page(failing_api, "overview")

    assert "No questions asked yet" in _text(empty)
    assert not failing.exception
    assert "Recent activity is unavailable" in _text(failing)
    assert len(failing.metric) > 0  # the rest of the page still renders


def test_continue_a_conversation_of_another_dataset() -> None:
    api = _api_with_history()
    at = AppTest.from_file(str(FRONTEND / "app.py"), default_timeout=20)
    at.session_state["api_client"] = api
    at.run()
    at.switch_page("views/ai_analyst.py").run()  # the AI Analyst remembers dataset d1
    at.switch_page("views/query_history.py").run()
    at.selectbox(key="qh_dataset").set_value("d3").run()

    at.button(key="qh_continue").click().run()

    assert not at.exception, at.exception
    assert at.selectbox(key="aa_dataset").value == "d3"
    assert "Pertanyaan dataset lain?" in _text(at)
