"""Overview for non-technical readers: the conclusion first, a few numbers, and a question box."""

from __future__ import annotations

import copy
import tomllib
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from frontend.components.client_summary import headline, top_department
from tests.frontend.fakes import DEPARTMENTS, AnalystFakeApi, retention

REPO = Path(__file__).resolve().parents[2]
FRONTEND = REPO / "frontend"


def _run(api: AnalystFakeApi) -> AppTest:
    at = AppTest.from_file(str(FRONTEND / "views" / "overview.py"), default_timeout=20)
    at.session_state["api_client"] = api
    return at.run()


def _text(at: AppTest) -> str:
    parts = [str(e.value) for e in (*at.markdown, *at.warning, *at.info, *at.caption, *at.error)]
    return "\n".join(parts).replace("\\", "")


def _metric(at: AppTest, label: str):
    return next(m for m in at.metric if m.label == label)


# ----------------------------------------------------------------- the conclusion
@pytest.mark.parametrize(
    ("previous", "current", "expected"),
    [
        (87, 103, "Active clients grew from 87 to 103."),
        (103, 87, "Active clients fell from 103 to 87."),
        (50, 50, "Active clients stayed at 50."),
        (1200, 1500, "Active clients grew from 1.200 to 1.500."),
    ],
)
def test_headline_says_what_changed(previous: int, current: int, expected: str) -> None:
    assert headline(retention(previous_active=previous, current_active=current)) == expected


def test_headline_without_a_previous_period_makes_no_comparison() -> None:
    text = headline(retention(previous_active=0, current_active=4))

    assert text == "4 active clients in 2026."


def test_headline_names_the_department_with_the_most_clients() -> None:
    text = headline(retention(), "Audit")

    assert text == "Active clients grew from 3 to 4. Audit has the most clients."


@pytest.mark.parametrize(
    ("rows", "expected"),
    [
        ([["Audit", 1, 2], ["Pajak", 1, 5]], "Pajak"),
        ([["Audit", 1, 5], ["Pajak", 1, 5]], None),  # a tie has no single leader
        ([["(no department)", 0, 9], ["Audit", 1, 2]], "Audit"),  # a gap in the data is no leader
        ([["Audit", 3, 0]], None),  # no active clients
        ([], None),
    ],
)
def test_top_department(rows: list, expected: str | None) -> None:
    result = {"columns": ["department", "previous_active", "current_active"], "rows": rows}

    assert top_department(result) == expected


# ------------------------------------------------------------------- with a mapping
def test_overview_leads_with_the_conclusion_and_three_numbers() -> None:
    at = _run(AnalystFakeApi())

    assert not at.exception, at.exception
    assert "Active clients grew from 3 to 4. Audit has the most clients." in _text(at)
    assert _metric(at, "Active clients").value == "4"
    assert _metric(at, "Active clients").delta == "+33,3% vs 2025"
    assert _metric(at, "Came back").value == "66,7%"
    assert _metric(at, "Came back").delta == "2 of 3 clients"
    assert _metric(at, "New clients").value == "1"
    assert _metric(at, "New clients").delta == "25,0% of active"


def test_a_fall_is_shown_as_a_fall() -> None:
    """The app writes a typographic minus; Streamlit only reads "-" as going down."""
    at = _run(AnalystFakeApi(result=retention(growth_rate=-0.25)))

    assert _metric(at, "Active clients").delta == "-25,0% vs 2025"


def test_undefined_rates_are_na_never_zero() -> None:
    result = retention(previous_active=0, retention_rate=None, growth_rate=None)

    at = _run(AnalystFakeApi(result=result))

    assert not at.exception, at.exception
    assert _metric(at, "Came back").value == "N/A"
    assert not _metric(at, "Came back").delta
    assert not _metric(at, "Active clients").delta


def test_an_incomplete_year_compares_the_same_part_of_both_years() -> None:
    api = AnalystFakeApi()

    _run(api)

    previous, current, _ = api.compare_calls[0]
    assert previous == {"label": "2025 (1 Jan–8 Aug)", "start": "2025-01-01", "end": "2025-08-09"}
    assert current == {"label": "2026 (1 Jan–8 Aug)", "start": "2026-01-01", "end": "2026-08-09"}


def test_a_complete_year_compares_whole_years() -> None:
    api = AnalystFakeApi()
    api.coverage["data_end"] = "2026-12-31"

    _run(api)

    previous, current, _ = api.compare_calls[0]
    assert (previous["label"], current["label"]) == ("2025", "2026")


def test_data_notes_are_counted_not_hidden() -> None:
    at = _run(AnalystFakeApi())

    assert "2 note(s) about this data" in _text(at)
    assert at.button(key="ov_details") is not None


def test_text_from_the_file_cannot_format_the_page() -> None:
    api = AnalystFakeApi()
    departments = copy.deepcopy(DEPARTMENTS)
    departments["result"]["rows"][0][0] = "**Audit** ![x](http://evil.test/a.png)"
    api.analytics_departments = lambda *args, **kwargs: departments

    at = _run(api)

    raw = "\n".join(str(m.value) for m in at.markdown)
    assert "\\*\\*Audit\\*\\*" in raw and "**Audit**" not in raw


def test_technical_details_stay_available() -> None:
    at = _run(AnalystFakeApi())

    assert _metric(at, "Total records").value == "3"
    assert any(e.label == "Data details" for e in at.expander)


# ---------------------------------------------------------------- failure and empties
@pytest.mark.parametrize("endpoint", ["analytics_coverage", "analytics_compare"])
def test_client_numbers_failing_keeps_the_page(endpoint: str) -> None:
    at = _run(AnalystFakeApi(failing={endpoint}))

    assert not at.exception, at.exception
    assert f"{endpoint} failed" in _text(at)
    assert _metric(at, "Total records").value == "3"


def test_departments_failing_keeps_the_numbers() -> None:
    at = _run(AnalystFakeApi(failing={"analytics_departments"}))

    assert not at.exception, at.exception
    assert "Active clients grew from 3 to 4." in _text(at)
    assert "has the most clients" not in _text(at)


def test_no_department_role_skips_the_chart_and_its_sentence() -> None:
    api = AnalystFakeApi()
    api.coverage["department_column"] = None

    at = _run(api)

    assert not at.exception, at.exception
    assert "has the most clients" not in _text(at)


def test_no_dated_rows() -> None:
    api = AnalystFakeApi()
    api.coverage["data_start"] = None

    at = _run(api)

    assert not at.exception, at.exception
    assert not api.compare_calls
    assert "No rows have a transaction date" in _text(at)


def test_no_activity_in_the_periods() -> None:
    at = _run(AnalystFakeApi(result=retention(previous_active=0, current_active=0)))

    assert not at.exception, at.exception
    assert "No client activity" in _text(at)


def test_without_a_mapping_the_page_explains_the_next_step() -> None:
    api = AnalystFakeApi(mapped=False)

    at = _run(api)

    assert not at.exception, at.exception
    assert not api.compare_calls
    assert "Confirm the column mapping" in _text(at)
    assert at.button(key="ov_to_mapping") is not None


def test_without_datasets_the_page_offers_the_upload() -> None:
    at = _run(AnalystFakeApi(datasets=[]))

    assert not at.exception, at.exception
    assert at.button(key="ov_to_data") is not None


# -------------------------------------------------------------------- the question box
def _app(api: AnalystFakeApi) -> AppTest:
    at = AppTest.from_file(str(FRONTEND / "app.py"), default_timeout=20)
    at.session_state["api_client"] = api
    return at.run()


def test_a_question_opens_a_new_conversation_with_the_answer() -> None:
    api = AnalystFakeApi()
    api.ask("d1", "Pertanyaan lama?", None)
    at = _app(api)

    at.text_input(key="ov_question").input("  Klien per departemen?  ")
    at.button(key="ov_ask").click().run()

    assert not at.exception, at.exception
    assert api.ask_calls[-1] == ("d1", "Klien per departemen?", None)
    assert at.chat_input(key="aa_input") is not None  # now on the AI Analyst page


def test_a_blank_question_goes_nowhere() -> None:
    api = AnalystFakeApi()
    at = _app(api)

    at.text_input(key="ov_question").input("   ")
    at.button(key="ov_ask").click().run()

    assert not at.exception, at.exception
    assert not api.ask_calls
    assert "Type a question" in _text(at)


def test_a_suggested_question_is_asked() -> None:
    api = AnalystFakeApi()
    at = _app(api)

    at.button(key="ov_suggest_0").click().run()

    assert not at.exception, at.exception
    assert len(api.ask_calls) == 1


def test_without_ai_the_rest_of_the_page_still_works() -> None:
    at = _run(AnalystFakeApi(llm=False))

    assert not at.exception, at.exception
    assert not [t for t in at.text_input if t.key == "ov_question"]
    assert "AI questions are not set up yet" in _text(at)
    assert _metric(at, "Active clients").value == "4"


# ------------------------------------------------------------------------- appearance
def test_the_theme_is_always_light_with_a_white_sidebar() -> None:
    config = tomllib.loads((REPO / ".streamlit" / "config.toml").read_text(encoding="utf-8"))

    assert config["theme"]["base"] == "light"
    assert config["theme"]["sidebar"]["backgroundColor"] == "#FFFFFF"
    assert (FRONTEND / "assets" / "logo.svg").exists()
