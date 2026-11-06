"""Overview: what the data says in plain language, then the details for those who want them."""

from __future__ import annotations

from typing import Any

import streamlit as st

from frontend.api_client import ApiClient, ApiError
from frontend.components.charts import render_chart
from frontend.components.client_summary import (
    render_headline,
    render_kpis,
    short_period,
    top_department,
)
from frontend.components.formatting import fmt_int, fmt_number
from frontend.components.layout import (
    empty_state,
    fmt_count,
    md_escape,
    page_header,
    show_api_error,
)
from frontend.components.period_picker import default_periods
from frontend.components.suggested_questions import suggested_questions
from frontend.state import (
    CLIENT_ANALYTICS_PAGE,
    DATA_SOURCES_PAGE,
    ask_in_ai_analyst,
    get_client,
    resolve_active,
)

BRAND_BLUE = "#1E40AF"
MAX_QUESTION_CHARS = 1000
MAX_SUGGESTIONS = 3


def _optional(call, *args: Any) -> tuple[Any, ApiError | None]:
    """Call the backend; return (result, error) so one failing panel never breaks the page."""
    try:
        return call(*args), None
    except ApiError as error:
        return None, error


def _date_range(profile: dict[str, Any] | None, state: dict[str, Any] | None) -> tuple[str, str]:
    """First and last date of the confirmed transaction-date column, or N/A."""
    if not profile or not state or not state.get("mapping"):
        return "N/A", "N/A"
    column = state["mapping"].get("transaction_date")
    stats = next((c for c in profile["columns"] if c["name"] == column), None)
    if not stats or not stats.get("date_min"):
        return "N/A", "N/A"
    return stats["date_min"], stats["date_max"]


def _data_details(
    active: dict[str, Any],
    profile: dict[str, Any] | None,
    profile_error: ApiError | None,
    state: dict[str, Any] | None,
) -> None:
    """Size, coverage, and quality of the dataset."""
    confirmed = bool(state and state.get("mapping"))
    cols = st.columns(4)
    cols[0].metric("Total records", fmt_count(active["row_count"]))
    cols[1].metric("Total columns", fmt_count(active["column_count"]))
    clients = state.get("unique_clients") if confirmed else None
    cols[2].metric("Unique clients", fmt_count(clients) if clients is not None else "Not set")
    first, last = _date_range(profile, state)
    cols[3].metric("First transaction", first)

    quality = st.columns(4)
    quality[3].metric("Last transaction", last)
    if profile:
        warnings = sum(n["level"] == "warning" for n in profile["notes"]) + sum(
            n["level"] == "warning" for c in profile["columns"] for n in c["notes"]
        )
        quality[0].metric("Duplicate rows", fmt_count(profile["duplicate_row_count"]))
        quality[1].metric("Missing cells", f"{fmt_number(profile['missing_pct'])}%")
        quality[2].metric("Data-quality warnings", fmt_count(warnings))
    elif profile_error:
        st.warning(f"The data profile is unavailable: {md_escape(profile_error.message)}")


def _load_summary(api: ApiClient, dataset_id: str) -> dict[str, Any] | None:
    """The comparison shown on this page; None (after saying why) when it is unavailable."""
    try:
        coverage = api.analytics_coverage(dataset_id)
        if not coverage.get("data_start"):
            st.info(
                "No rows have a transaction date, so periods cannot be compared.",
                icon=":material/event_busy:",
            )
            return None
        previous, current = default_periods(coverage)
        comparison = api.analytics_compare(dataset_id, previous, current)
    except ApiError as error:
        show_api_error(error)
        return None
    departments = None
    if coverage.get("department_column"):
        departments, _ = _optional(api.analytics_departments, dataset_id, previous, current)
    return {"comparison": comparison, "departments": departments}


def _department_chart(departments: dict[str, Any]) -> None:
    result = departments["result"]
    count = result["columns"].index("current_active")
    rows = [row for row in result["rows"] if row[count]]
    if not rows:
        return
    with st.container(border=True):
        render_chart(
            {"type": "hbar", "x": "department", "y": ["current_active"],
             "title": "Active clients by department"},
            result["columns"],
            rows,
            labels={"current_active": "Active clients"},
            series_colors=(BRAND_BLUE,),
            key="ov_departments",
        )  # fmt: skip
        if departments.get("clients_in_multiple_departments"):
            st.caption(
                "A client can use more than one department, so the bars can add up to more "
                "than the number of active clients."
            )


def _ask_box(active: dict[str, Any], mapping: dict[str, Any] | None, configured: bool) -> None:
    """Questions open a new conversation on the AI Analyst page."""
    if not configured:
        st.info(
            "AI questions are not set up yet. Everything else on this page still works, and "
            "Settings shows what is missing.",
            icon=":material/key_off:",
        )
        return
    with st.container(key="overview_ask"), st.form("ov_ask_form", border=False):
        left, right = st.columns([6, 1], vertical_alignment="center")
        question = left.text_input(
            "Ask anything about your data",
            key="ov_question",
            max_chars=MAX_QUESTION_CHARS,
            placeholder="Ask anything about your data",
            label_visibility="collapsed",
        )
        sent = right.form_submit_button(
            "Ask", key="ov_ask", type="primary", icon=":material/send:", width="stretch"
        )
    if sent:
        if question.strip():
            ask_in_ai_analyst(active["id"], question.strip())
        st.caption("Type a question first.")
    questions = suggested_questions(mapping)[:MAX_SUGGESTIONS]
    for n, (column, text) in enumerate(zip(st.columns(len(questions)), questions, strict=True)):
        if column.button(text, key=f"ov_suggest_{n}", icon=":material/lightbulb:", width="stretch"):
            ask_in_ai_analyst(active["id"], text)


def _client_view(
    api: ApiClient, active: dict[str, Any], state: dict[str, Any], configured: bool
) -> None:
    """The conclusion, the question box, three numbers, and one chart."""
    summary = _load_summary(api, active["id"])
    r = summary["comparison"] if summary else None
    departments = summary["departments"] if summary else None
    if r and (r["previous_active"] or r["current_active"]):
        st.caption(
            f"{md_escape(active['name'])} · {md_escape(r['current']['label'])}, compared with "
            f"{md_escape(r['previous']['label'])}"
        )
        leader = top_department(departments["result"]) if departments else None
        render_headline(r, leader)
    elif r:
        st.info(
            f"No client activity in {md_escape(short_period(r['previous']['label']))} or "
            f"{md_escape(short_period(r['current']['label']))}.",
            icon=":material/inbox:",
        )
    _ask_box(active, state["mapping"], configured)
    if not r or not (r["previous_active"] or r["current_active"]):
        return
    render_kpis(r)
    if departments:
        _department_chart(departments)
    notes = sum(n["level"] == "warning" for n in r["warnings"])
    left, right = st.columns([3, 1], vertical_alignment="center")
    if notes:
        left.caption(
            f":material/info: {fmt_int(notes)} note(s) about this data affect how to read these "
            "numbers. Client details explains them."
        )
    if right.button("Client details", key="ov_details", width="stretch"):
        st.switch_page(CLIENT_ANALYTICS_PAGE)


def _unmapped_view(active: dict[str, Any], state: dict[str, Any] | None, configured: bool) -> None:
    """Without client roles only the dataset itself can be summarized."""
    st.caption(md_escape(active["name"]))
    _ask_box(active, None, configured)
    if state is not None:
        left, right = st.columns([3, 1], vertical_alignment="center")
        left.info(
            "Confirm the column mapping in Data Sources (Client ID, transaction date, …) to "
            "see unique clients and to enable client analytics.",
            icon=":material/rule:",
        )
        if right.button("Open Data Sources", key="ov_to_mapping", width="stretch"):
            st.switch_page(DATA_SOURCES_PAGE)


STATUS_ICONS = {
    "answered": ":green[:material/check_circle:]",
    "clarification": ":blue[:material/help:]",
    "unsupported": ":orange[:material/block:]",
    "error": ":red[:material/error:]",
}


def _recent_activity(api: ApiClient) -> None:
    """The last five questions across all datasets (no results, just what was asked)."""
    st.subheader("Recent activity", anchor=False)
    recent, error = _optional(api.recent_analyses, 5)
    if error is not None:
        st.caption(f"Recent activity is unavailable: {md_escape(error.message)}")
        return
    if not recent:
        st.caption("No questions asked yet. Questions asked in AI Analyst appear here.")
        return
    with st.container(border=True):
        for item in recent:
            when = str(item["created_at"])[:16].replace("T", " ")
            st.markdown(
                f"{STATUS_ICONS.get(item['status'], '')} {md_escape(item['question'])}  \n"
                f":gray[{md_escape(item['dataset_name'])} · {when} UTC]"
            )
        st.caption("Open Query History to reopen or continue a conversation.")


def main() -> None:
    """Render the page."""
    page_header("Overview", "What your data says, in plain language.")
    api = get_client()
    try:
        datasets = api.list_datasets()
    except ApiError as error:
        show_api_error(error)
        return
    if not datasets:
        empty_state("Upload an Excel or CSV file to begin.")
        if st.button("Open Data Sources", key="ov_to_data", type="primary"):
            st.switch_page(DATA_SOURCES_PAGE)
        return
    active = resolve_active(datasets)
    if active is None:
        st.info("No dataset is ready yet. Check Data Sources for import errors.")
        _recent_activity(api)
        return

    health, _ = _optional(api.health)
    configured = bool(health and health["llm_configured"])
    profile, profile_error = _optional(api.profile, active["id"])
    state, state_error = _optional(api.column_mapping, active["id"])
    if state and state.get("mapping"):
        _client_view(api, active, state, configured)
        with st.expander("Data details", icon=":material/table_chart:"):
            _data_details(active, profile, profile_error, state)
    else:
        _unmapped_view(active, state, configured)
        _data_details(active, profile, profile_error, state)
    if state_error:
        st.warning(f"The column mapping is unavailable: {md_escape(state_error.message)}")
    _recent_activity(api)


main()
