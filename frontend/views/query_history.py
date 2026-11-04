"""Query History: earlier conversations per dataset — reopen, continue, or delete."""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from frontend.api_client import ApiClient, ApiError
from frontend.components.chat import render_analysis
from frontend.components.layout import empty_state, md_escape, page_header, show_api_error
from frontend.state import (
    AI_ANALYST_PAGE,
    get_client,
    open_in_ai_analyst,
    resolve_active,
    set_active_dataset_id,
)

_CONFIRM = "qh_confirm_delete"  # conversation id awaiting delete confirmation
_FLASH = "qh_flash"


def _when(value: str | None) -> str:
    return str(value)[:16].replace("T", " ") + " UTC" if value else "—"


def _dataset(api: ApiClient) -> dict[str, Any] | None:
    datasets = api.list_datasets()
    active = resolve_active(datasets)
    ready = [d for d in datasets if d["status"] == "ready"]
    if not ready or active is None:
        empty_state("Import a dataset in Data Sources, then ask questions in AI Analyst.")
        return None
    names = {d["id"]: d["name"] for d in ready}
    chosen = st.selectbox(
        "Dataset",
        list(names),
        index=list(names).index(active["id"]),
        format_func=lambda i: names[i],
        key="qh_dataset",
        help="Each dataset has its own conversations.",
    )
    if chosen != active["id"]:
        set_active_dataset_id(chosen)
        active = next(d for d in ready if d["id"] == chosen)
    return active


def _matches(conversation: dict[str, Any], query: str) -> bool:
    text = f"{conversation['title']} {conversation.get('last_question') or ''}"
    return query.casefold() in text.casefold()


def _table(conversations: list[dict[str, Any]]) -> None:
    st.dataframe(
        pd.DataFrame(
            {
                "Title": [c["title"] for c in conversations],
                "Questions": [c["analysis_count"] for c in conversations],
                "Latest question": [c.get("last_question") or "" for c in conversations],
                "Started": [_when(c["created_at"]) for c in conversations],
                "Last activity": [_when(c.get("last_activity_at")) for c in conversations],
            }
        ),
        hide_index=True,
    )


def _delete_controls(api: ApiClient, conversation: dict[str, Any]) -> None:
    st.warning(
        f"Delete this conversation and its {conversation['analysis_count']} question(s) "
        "with their results? This cannot be undone.",
        icon=":material/delete:",
    )
    left, right, _ = st.columns([1, 1, 4])
    if left.button("Delete permanently", type="primary", key="qh_delete_confirm"):
        try:
            api.delete_conversation(conversation["id"])
        except ApiError as error:
            st.session_state.pop(_CONFIRM, None)
            show_api_error(error)
            return
        st.session_state.pop(_CONFIRM, None)
        st.session_state[_FLASH] = "Conversation deleted."
        st.rerun()
    if right.button("Cancel", key="qh_delete_cancel"):
        st.session_state.pop(_CONFIRM, None)
        st.rerun()


def _conversation(api: ApiClient, dataset_id: str, conversation_id: str) -> None:
    try:
        conversation = api.get_conversation(conversation_id)
    except ApiError as error:
        show_api_error(error)
        return
    left, right, _ = st.columns([2, 1, 3])
    if left.button(
        "Continue in AI Analyst", type="primary", icon=":material/forum:", key="qh_continue"
    ):
        open_in_ai_analyst(dataset_id, conversation_id)
        st.switch_page(AI_ANALYST_PAGE)
    if right.button("Delete", icon=":material/delete:", key="qh_delete"):
        st.session_state[_CONFIRM] = conversation_id
    if st.session_state.get(_CONFIRM) == conversation_id:
        _delete_controls(api, conversation)
    for analysis in conversation["analyses"]:
        render_analysis(analysis, api=api)


def main() -> None:
    """Render the page."""
    page_header("Query History", "Revisit, continue, or delete earlier analyses.")
    api = get_client()
    try:
        active = _dataset(api)
        if active is None:
            return
        conversations = api.list_conversations(active["id"])
    except ApiError as error:
        show_api_error(error)
        return
    flash = st.session_state.pop(_FLASH, None)
    if flash:
        st.success(flash, icon=":material/check_circle:")
    if not conversations:
        empty_state(
            "No conversations for this dataset yet. Ask a question in AI Analyst: every "
            "question, its SQL, and its result are saved here."
        )
        return

    query = st.text_input(
        "Search", key="qh_search", placeholder="Search titles and latest questions"
    ).strip()
    shown = [c for c in conversations if _matches(c, query)] if query else conversations
    if not shown:
        st.caption(f"No conversation matches “{md_escape(query)}”.")
        return
    _table(shown)

    ids = [c["id"] for c in shown]
    labels = {c["id"]: f"{c['title'][:70]} · {_when(c['created_at'])} · #{c['id'][:6]}"
              for c in shown}  # fmt: skip
    widget = f"qh_open_{active['id']}"
    if st.session_state.get(widget) not in ids:
        st.session_state[widget] = ids[0]
    chosen = st.selectbox("Open conversation", ids, format_func=labels.__getitem__, key=widget)
    if st.session_state.get(_CONFIRM) not in (None, chosen):
        st.session_state.pop(_CONFIRM)  # a pending confirmation never applies to another one
    with st.container(border=True):
        _conversation(api, active["id"], chosen)


main()
