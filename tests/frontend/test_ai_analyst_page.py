"""Task 18: AI Analyst page against a fake backend."""

from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

from frontend.components.chat import format_kpi
from frontend.components.suggested_questions import suggested_questions
from tests.frontend.fakes import READY, AnalystFakeApi

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
SECOND = {**READY, "id": "d3", "name": "other"}


def _run(api: AnalystFakeApi, at: AppTest | None = None) -> AppTest:
    if at is None:
        at = AppTest.from_file(str(FRONTEND / "views" / "ai_analyst.py"), default_timeout=20)
        at.session_state["api_client"] = api
    return at.run()


def _text(at: AppTest) -> str:
    parts = [str(e.value) for e in (*at.markdown, *at.warning, *at.info, *at.caption, *at.error)]
    parts += [f"{m.label} {m.value}" for m in at.metric]
    return "\n".join(parts).replace("\\", "")


def test_format_kpi() -> None:
    assert format_kpi(None, "pct") == "N/A"
    assert format_kpi(0.632, "pct") == "63,2%"
    assert format_kpi(1234, "int") == "1.234"
    assert format_kpi(2.5, "number") == "2,50"


def test_suggestions_follow_the_mapping() -> None:
    assert len(suggested_questions(None)) == 1
    full = {"client_id": "a", "transaction_date": "b", "department": "c", "revenue": "d"}
    questions = suggested_questions(full)
    assert any("Bandingkan klien" in q for q in questions) and len(questions) == 4


def test_not_configured_disables_the_chat() -> None:
    at = _run(AnalystFakeApi(llm=False))

    assert not at.exception
    assert "OPENAI_API_KEY" in _text(at)
    assert at.chat_input(key="aa_input").disabled


def test_ask_renders_the_answer_sql_and_insight() -> None:
    api = AnalystFakeApi()
    at = _run(api)

    at.chat_input(key="aa_input").set_value("Klien per departemen?").run()

    assert not at.exception, at.exception
    assert api.ask_calls == [("d1", "Klien per departemen?", None)]
    text = _text(at)
    assert "Audit memiliki klien aktif terbanyak (34)." in text
    assert "each number was checked" in text
    assert any("COUNT(DISTINCT id_klien)" in c.value for c in at.code)
    assert "2 AI request(s)" in text and "cost not available" in text


def test_follow_up_reuses_the_conversation_of_the_same_dataset() -> None:
    api = AnalystFakeApi()
    at = _run(api)
    at.chat_input(key="aa_input").set_value("Pertama?").run()
    at.chat_input(key="aa_input").set_value("Lalu 2025?").run()

    assert [c[2] for c in api.ask_calls] == [None, "c1"]


def test_switching_dataset_starts_a_separate_conversation() -> None:
    api = AnalystFakeApi(datasets=[READY, SECOND])
    at = _run(api)
    at.chat_input(key="aa_input").set_value("Pertama?").run()

    at.selectbox(key="aa_dataset").set_value("d3").run()
    at.chat_input(key="aa_input").set_value("Dataset lain?").run()

    assert api.ask_calls[-1] == ("d3", "Dataset lain?", None)
    assert "Pertama?" not in _text(at)


def test_template_insight_and_na_kpis() -> None:
    api = AnalystFakeApi()
    api.next_answer = {
        "kpis": [{"label": "Retention rate", "value": None, "format": "pct", "definition": "x"}],
        "insight": {"source": "deterministic", "language": "id", "summary": "Ringkasan.",
                    "facts": ["Fakta."], "interpretations": [], "recommendations": [],
                    "limitations": ["Histori terbatas."], "ungrounded_numbers": ["999"],
                    "fallback_reason": "ungrounded_numbers", "llm_request_id": "r1"},
    }  # fmt: skip
    at = _run(api)

    at.chat_input(key="aa_input").set_value("Retensi?").run()

    assert {m.label: m.value for m in at.metric}["Retention rate"] == "N/A"
    assert "numbers that are not in the result" in _text(at)


def test_clarification_and_errors_are_shown() -> None:
    api = AnalystFakeApi()
    api.next_answer = {"status": "clarification", "answer": "Tahun berapa?", "table": None,
                       "chart": None, "insight": None, "sql": None}  # fmt: skip
    at = _run(api)

    at.chat_input(key="aa_input").set_value("Klien?").run()

    assert "Tahun berapa?" in _text(at)
    assert not at.exception


def test_suggested_question_button_asks_it() -> None:
    api = AnalystFakeApi(mapped=True)
    at = _run(api)

    at.button(key="aa_suggest_0").click().run()

    assert api.ask_calls and api.ask_calls[0][1].startswith("Bandingkan klien")


def test_follow_up_with_existing_conversations_reuses_the_new_one() -> None:
    """E2E regression: with older conversations listed, a follow-up must stay in the
    conversation that the first question created."""
    api = AnalystFakeApi()
    api.ask("d1", "Lama?", None)  # an older conversation c1
    api.ask_calls.clear()
    at = _run(api)

    at.chat_input(key="aa_input").set_value("Pertama?").run()
    at.chat_input(key="aa_input").set_value("Lanjutan?").run()

    assert [c[2] for c in api.ask_calls] == [None, "c2"]
    assert "Pertama?" in _text(at) and "Lanjutan?" in _text(at)


def test_conversation_labels_are_unique_for_identical_titles() -> None:
    api = AnalystFakeApi()
    api.ask("d1", "Sama?", None)
    api.ask("d1", "Sama?", None)
    at = _run(api)

    options = at.selectbox(key="aa_conversation_d1").options
    assert len(set(options)) == len(options) == 3
