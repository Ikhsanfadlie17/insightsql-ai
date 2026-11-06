"""AI Analyst: questions in plain language, answered with validated SQL on the dataset.

A conversation belongs to one dataset. Switching the dataset switches to that dataset's
own conversation, so earlier questions are never used as context for another dataset.
"""

from __future__ import annotations

from typing import Any

import streamlit as st

from frontend.api_client import ApiClient, ApiError
from frontend.components.chat import render_analysis
from frontend.components.layout import empty_state, md_escape, page_header, show_api_error
from frontend.components.suggested_questions import suggested_questions
from frontend.state import (
    AA_CONVERSATIONS,
    AA_PENDING,
    AA_SELECT,
    get_client,
    open_in_ai_analyst,
    resolve_active,
    set_active_dataset_id,
)


def _conversation_for(dataset_id: str) -> str | None:
    return st.session_state.setdefault(AA_CONVERSATIONS, {}).get(dataset_id)


def _set_conversation(dataset_id: str, conversation_id: str | None) -> None:
    st.session_state.setdefault(AA_CONVERSATIONS, {})[dataset_id] = conversation_id


def _select_next_run(dataset_id: str, conversation_id: str | None) -> None:
    """Show this conversation after the rerun (see ``open_in_ai_analyst``)."""
    open_in_ai_analyst(dataset_id, conversation_id)


def _dataset(datasets: list[dict[str, Any]], active: dict[str, Any]) -> dict[str, Any]:
    ready = [d for d in datasets if d["status"] == "ready"]
    names = {d["id"]: d["name"] for d in ready}
    chosen = st.selectbox(
        "Dataset",
        list(names),
        index=list(names).index(active["id"]),
        format_func=lambda i: names[i],
        key="aa_dataset",
        help="Each dataset has its own conversations.",
    )
    if chosen != active["id"]:
        set_active_dataset_id(chosen)
        active = next(d for d in ready if d["id"] == chosen)
    return active


def _conversation_picker(api: ApiClient, dataset_id: str) -> str | None:
    current = _conversation_for(dataset_id)
    try:
        conversations = api.list_conversations(dataset_id)
    except ApiError as error:
        show_api_error(error)
        return current
    widget = f"aa_conversation_{dataset_id}"
    if conversations:
        options = [None] + [c["id"] for c in conversations]
        # Labels must be unique: Streamlit matches a selectbox value by its label, and two
        # conversations can start with the same question.
        titles = {
            c["id"]: f"{c['title'][:70]} · {str(c['created_at'])[:16].replace('T', ' ')} "
            f"· #{c['id'][:6]}"
            for c in conversations
        }
        selected = st.session_state.pop(AA_SELECT, None)
        if selected is not None and selected[0] == dataset_id:
            st.session_state[widget] = selected[1] if selected[1] in options else None
        elif st.session_state.get(widget, current) not in options:
            st.session_state[widget] = current if current in options else None
        if widget not in st.session_state:
            st.session_state[widget] = current if current in options else None
        chosen = st.selectbox(
            "Conversation",
            options,
            format_func=lambda i: "New conversation" if i is None else titles[i],
            key=widget,
        )
        if chosen != current:
            _set_conversation(dataset_id, chosen)
            current = chosen
    return current


def _ask(api: ApiClient, dataset_id: str, question: str) -> None:
    with st.spinner("Working out the answer…"):
        try:
            result = api.ask(dataset_id, question, _conversation_for(dataset_id))
        except ApiError as error:
            show_api_error(error)
            return
    _select_next_run(dataset_id, result["conversation_id"])
    st.rerun()


def main() -> None:
    page_header(
        "AI Analyst", "Ask a question about your data in plain language, in Indonesian or English."
    )
    api = get_client()
    try:
        health = api.health()
        datasets = api.list_datasets()
    except ApiError as error:
        show_api_error(error)
        return
    active = resolve_active(datasets)
    if active is None:
        empty_state("Import a dataset in Data Sources to ask questions about it.")
        return
    configured = bool(health.get("llm_configured"))
    if not configured:
        st.info(
            "AI questions are not set up yet, and Settings shows what is missing. Client "
            "Analytics, the data profile, and column mapping work without AI.",
            icon=":material/key_off:",
        )
    with st.expander("Change dataset or conversation", icon=":material/tune:"):
        active = _dataset(datasets, active)
        conversation_id = _conversation_picker(api, active["id"])
    try:
        mapping_state = api.column_mapping(active["id"])
    except ApiError:
        mapping_state = {}
    mapping = mapping_state.get("mapping")
    left, right = st.columns([3, 1], vertical_alignment="center")
    if mapping:
        left.caption(md_escape(active["name"]))
    else:
        left.caption(
            f"{md_escape(active['name'])} · Every column can be used in your questions. "
            "Optional: confirming client roles (Data Sources → Column mapping) tells the AI "
            "which column means *client* or *revenue*."
        )
    if right.button(
        "New conversation", icon=":material/add_comment:", key="aa_new", width="stretch"
    ):
        _select_next_run(active["id"], None)
        st.rerun()

    analyses: list[dict[str, Any]] = []
    if conversation_id:
        try:
            analyses = api.get_conversation(conversation_id)["analyses"]
        except ApiError as error:
            if error.status == 404:
                _set_conversation(active["id"], None)
            else:
                show_api_error(error)
    for analysis in analyses:
        render_analysis(analysis, api=api)

    if not analyses and configured:
        st.markdown("**Try a question**")
        for n, question in enumerate(suggested_questions(mapping)):
            if st.button(question, key=f"aa_suggest_{n}", icon=":material/lightbulb:"):
                st.session_state[AA_PENDING] = question

    question = st.chat_input(
        "Ask about this dataset…" if configured else "AI is not configured",
        disabled=not configured,
        max_chars=1000,
        key="aa_input",
    )
    pending = st.session_state.pop(AA_PENDING, None)
    if configured and (question or pending):
        text = question or pending
        with st.chat_message("user"):
            st.markdown(md_escape(text))
        _ask(api, active["id"], text)


main()
