"""Overview: workspace status and data quality of the active dataset at a glance."""

from __future__ import annotations

from typing import Any

import streamlit as st

from frontend.api_client import ApiClient, ApiError
from frontend.components.formatting import fmt_number
from frontend.components.layout import (
    empty_state,
    fmt_count,
    md_escape,
    page_header,
    show_api_error,
)
from frontend.state import get_client, resolve_active


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


def _active_panel(api: ApiClient, active: dict[str, Any]) -> None:
    profile, profile_error = _optional(api.profile, active["id"])
    state, state_error = _optional(api.column_mapping, active["id"])
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

    if state_error:
        st.warning(f"The column mapping is unavailable: {md_escape(state_error.message)}")
    elif not confirmed:
        st.info(
            "Confirm the column mapping in Data Sources (Client ID, transaction date, …) to "
            "see unique clients and to enable client analytics.",
            icon=":material/rule:",
        )


STATUS_ICONS = {
    "answered": ":green[:material/check_circle:]",
    "clarification": ":blue[:material/help:]",
    "unsupported": ":orange[:material/block:]",
    "error": ":red[:material/error:]",
}


def _recent_activity(api: ApiClient) -> None:
    """The last five questions across all datasets (no results, just what was asked)."""
    st.subheader("Recent activity")
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
    page_header("Overview", "Your datasets and the data quality of the active dataset.")
    api = get_client()
    try:
        datasets = api.list_datasets()
    except ApiError as error:
        show_api_error(error)
        return
    health, _ = _optional(api.health)

    ready = [d for d in datasets if d["status"] == "ready"]
    active = resolve_active(datasets)
    top = st.columns(2)
    top[0].metric("Datasets", len(ready))
    top[1].metric("Active dataset", active["name"] if active else "None")

    if health is not None and not health["llm_configured"]:
        st.warning(
            "AI features are not configured. Set OPENAI_API_KEY and OPENAI_MODEL on the backend.",
            icon=":material/key_off:",
        )
    if not datasets:
        empty_state("Upload a dataset in Data Sources to begin.")
    elif active is None:
        st.info("No dataset is ready yet. Check Data Sources for import errors.")
    else:
        with st.container(border=True):
            st.markdown(f"#### {md_escape(active['name'])}")
            _active_panel(api, active)
    _recent_activity(api)


main()
