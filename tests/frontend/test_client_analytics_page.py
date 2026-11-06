"""Task 15: Client Analytics page — honest KPIs, definitions, warnings, and N/A."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from streamlit.testing.v1 import AppTest

from frontend.components.period_picker import (
    custom_period,
    period_problem,
    year_period,
)
from tests.frontend.fakes import AnalyticsFakeApi, retention

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"


def _run(api: AnalyticsFakeApi, **state) -> AppTest:
    at = AppTest.from_file(str(FRONTEND / "views" / "client_analytics.py"), default_timeout=20)
    at.session_state["api_client"] = api
    for key, value in state.items():
        at.session_state[key] = value
    return at.run()


def _text(at: AppTest) -> str:
    parts = [str(e.value) for e in (*at.markdown, *at.warning, *at.info, *at.caption, *at.error)]
    parts += [f"{m.label} {m.value}" for m in at.metric]
    return "\n".join(parts).replace("\\", "")


def _metrics(at: AppTest) -> dict[str, str]:
    return {m.label: m.value for m in at.metric}


# --- period helpers -----------------------------------------------------------------------
def test_year_period_and_ytd() -> None:
    assert year_period(2025) == {"label": "2025", "start": "2025-01-01", "end": "2026-01-01"}
    ytd = year_period(2025, through=date(2024, 2, 29))
    assert ytd["end"] == "2025-03-01" and "28 Feb" in ytd["label"]


def test_custom_period_end_is_inclusive_for_the_user() -> None:
    period = custom_period(date(2026, 1, 1), date(2026, 3, 31))

    assert period == {
        "label": "1 Jan 2026 – 31 Mar 2026", "start": "2026-01-01", "end": "2026-04-01",
    }  # fmt: skip


def test_period_problem() -> None:
    assert period_problem(year_period(2025), year_period(2026)) is None
    assert "before" in period_problem(year_period(2026), year_period(2025))
    assert period_problem(year_period(2025), year_period(2025))
    bad = custom_period(date(2026, 3, 1), date(2026, 2, 1))
    assert "start" in period_problem(bad, year_period(2027))


# --- the page -----------------------------------------------------------------------------
def test_requires_confirmed_mapping() -> None:
    api = AnalyticsFakeApi(mapped=False)

    at = _run(api)

    assert not at.exception
    assert "Confirm the column mapping" in _text(at)
    assert not at.metric
    assert not api.compare_calls


def test_kpis_use_plain_words_indonesian_format_and_definitions() -> None:
    at = _run(AnalyticsFakeApi())
    metrics = _metrics(at)
    deltas = {m.label: m.delta for m in at.metric}

    assert not at.exception, at.exception
    assert metrics["Active clients"] == "4" and deltas["Active clients"] == "+33,3% vs 2025"
    assert metrics["Came back"] == "66,7%" and deltas["Came back"] == "2 of 3 clients"
    assert metrics["New clients"] == "1" and deltas["New clients"] == "25,0% of active"
    assert metrics["Did not return"] == "1" and deltas["Did not return"] == "of 3 clients in 2025"
    assert metrics["Returning clients"] == "2" and metrics["Back after a break"] == "1"
    assert metrics["Joined between periods"] == "0"
    assert metrics["Existing clients"] == "3"
    help_texts = {m.label: m.help for m in at.metric}
    assert "previous period" in help_texts["Came back"]
    text = _text(at)
    assert "overlaps" in text.lower()  # existing clients are not part of the partition
    assert "not proof" in text.lower()  # did not return is an observation
    assert "incomplete" in text.lower()  # data notes


def test_the_conclusion_comes_first_and_the_rest_is_in_tabs() -> None:
    at = _run(AnalyticsFakeApi())

    assert "Active clients grew from 3 to 4. Audit has the most clients." in _text(at)
    assert [t.label for t in at.tabs] == [
        "Summary", "Over time", "By department", "Client list", "How it's counted",
    ]  # fmt: skip
    labels = [e.label for e in at.expander]
    assert "Change period or dataset" in labels
    assert "2 notes about this data" in labels
    assert at.radio(key="ca_mode") is not None  # the period is still there, one click away


def test_no_department_role_has_no_department_tab() -> None:
    api = AnalyticsFakeApi()
    api.coverage["department_column"] = None

    at = _run(api)

    assert not at.exception, at.exception
    assert "By department" not in [t.label for t in at.tabs]
    assert "has the most clients" not in _text(at)


def test_departments_failing_keeps_the_rest() -> None:
    at = _run(AnalyticsFakeApi(failing={"analytics_departments"}))

    assert not at.exception, at.exception
    assert "analytics_departments failed" in _text(at)
    assert _metrics(at)["Active clients"] == "4"


def test_an_invalid_period_is_explained_outside_the_collapsed_panel() -> None:
    at = _run(AnalyticsFakeApi())
    at.radio(key="ca_mode").set_value("Custom ranges").run()

    at.date_input(key="ca_prev_range").set_value((date(2025, 1, 1), date(2025, 12, 31)))
    at.date_input(key="ca_cur_range").set_value((date(2025, 6, 1), date(2026, 5, 31))).run()

    assert "Fix the period" in _text(at)
    assert not at.metric


def test_compare_defaults_to_the_last_two_years_to_date_when_the_year_is_incomplete() -> None:
    api = AnalyticsFakeApi()  # data ends 2026-08-08

    at = _run(api)

    previous, current, dedupe = api.compare_calls[0]
    assert (previous["start"], previous["end"]) == ("2025-01-01", "2025-08-09")
    assert (current["start"], current["end"], dedupe) == ("2026-01-01", "2026-08-09", True)
    assert at.radio(key="ca_mode").value == "Year-to-date"


def test_full_years_are_the_default_when_the_data_ends_on_31_december() -> None:
    api = AnalyticsFakeApi()
    api.coverage["data_end"] = "2025-12-31"
    api.coverage["years"] = [2024, 2025]

    _run(api)

    previous, current, _ = api.compare_calls[0]
    assert (previous["label"], current["label"]) == ("2024", "2025")


def test_na_rates_are_shown_as_na() -> None:
    api = AnalyticsFakeApi(
        result=retention(previous_active=0, returning=0, not_returned=0,
                         retention_rate=None, growth_rate=None)
    )  # fmt: skip

    at = _run(api)

    assert _metrics(at)["Came back"] == "N/A"
    assert not next(m for m in at.metric if m.label == "Active clients").delta


def test_no_data_shows_no_charts() -> None:
    zero = {k: 0 for k in ("previous_active", "current_active", "new", "returning",
                           "reactivated", "between_period", "not_returned", "existing_active",
                           "transactions_previous", "transactions_current")}  # fmt: skip
    api = AnalyticsFakeApi(
        result=retention(**zero, retention_rate=None, new_client_rate=None, growth_rate=None,
                         warnings=[{"level": "warning", "code": "no_data",
                                    "message": "No client has a transaction in 2030 or 2031."}])
    )  # fmt: skip

    at = _run(api)

    assert not at.exception
    assert "No client has a transaction" in _text(at)
    assert not [e for e in at.get("plotly_chart")]


def test_overlapping_custom_periods_are_not_sent() -> None:
    api = AnalyticsFakeApi()
    at = _run(api)
    at.radio(key="ca_mode").set_value("Custom ranges").run()
    api.compare_calls.clear()

    at.date_input(key="ca_prev_range").set_value((date(2025, 1, 1), date(2025, 12, 31)))
    at.date_input(key="ca_cur_range").set_value((date(2025, 6, 1), date(2026, 5, 31))).run()

    assert not at.exception
    assert "before" in _text(at)
    assert not api.compare_calls


def test_ytd_mode_sends_cut_periods() -> None:
    api = AnalyticsFakeApi()
    at = _run(api)

    at.radio(key="ca_mode").set_value("Year vs year").run()
    assert api.compare_calls[-1][0]["end"] == "2026-01-01"
    assert "incomplete" in _text(at)
    at.radio(key="ca_mode").set_value("Year-to-date").run()

    previous, current, _ = api.compare_calls[-1]
    assert (previous["end"], current["end"]) == ("2025-08-09", "2026-08-09")


def test_department_breakdown_and_drill_down() -> None:
    api = AnalyticsFakeApi()

    at = _run(api)
    at.selectbox(key="ca_category").set_value("existing_active").run()

    text = _text(at)
    assert not at.exception
    assert "more than one department" in text
    assert api.client_calls[-1]["category"] == "existing_active"
    tables = [t.value for t in at.dataframe]
    assert any(list(t.iloc[:, 0]) == ["A", "D", "E"] for t in tables if len(t))


def test_backend_down_does_not_crash() -> None:
    at = _run(AnalyticsFakeApi(down=True))

    assert not at.exception
    assert at.error
