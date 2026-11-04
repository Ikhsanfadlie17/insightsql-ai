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
from frontend.state import get_client, resolve_active, set_active_dataset_id

_CONVERSATIONS = "aa_conversation_by_dataset"
_PENDING = "aa_pending_question"
_SELECT = "aa_select_conversation"  # (dataset_id, conversation_id) to show on the next run


def _conversation_for(dataset_id: str) -> str | None:
    return st.session_state.setdefault(_CONVERSATIONS, {}).get(dataset_id)


def _set_conversation(dataset_id: str, conversation_id: str | None) -> None:
    st.session_state.setdefault(_CONVERSATIONS, {})[dataset_id] = conversation_id


def _select_next_run(dataset_id: str, conversation_id: str | None) -> None:
    """Show this conversation after the rerun. A keyed selectbox keeps the browser's value,
    so the choice is written to Session State before the widget is created."""
    _set_conversation(dataset_id, conversation_id)
    st.session_state[_SELECT] = (dataset_id, conversation_id)


def _dataset(api: ApiClient) -> dict[str, Any] | None:
    datasets = api.list_datasets()
    active = resolve_active(datasets)
    ready = [d for d in datasets if d["status"] == "ready"]
    if not ready or active is None:
        empty_state("Import a dataset in Data Sources to ask questions about it.")
        return None
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
    left, right = st.columns([4, 1], vertical_alignment="bottom")
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
        selected = st.session_state.pop(_SELECT, None)
        if selected is not None and selected[0] == dataset_id:
            st.session_state[widget] = selected[1] if selected[1] in options else None
        elif st.session_state.get(widget, current) not in options:
            st.session_state[widget] = current if current in options else None
        if widget not in st.session_state:
            st.session_state[widget] = current if current in options else None
        chosen = left.selectbox(
            "Conversation",
            options,
            format_func=lambda i: "New conversation" if i is None else titles[i],
            key=widget,
        )
        if chosen != current:
            _set_conversation(dataset_id, chosen)
            current = chosen
    if right.button("New conversation", icon=":material/add_comment:", key="aa_new"):
        _select_next_run(dataset_id, None)
        st.rerun()
    return current


def _ask(api: ApiClient, dataset_id: str, question: str) -> None:
    with st.spinner("Writing and checking the query…"):
        try:
            result = api.ask(dataset_id, question, _conversation_for(dataset_id))
        except ApiError as error:
            show_api_error(error)
            return
    _select_next_run(dataset_id, result["conversation_id"])
    st.rerun()


def main() -> None:
    page_header(
        "AI Analyst",
        "Ask questions in Indonesian or English. Answers come from validated, read-only SQL.",
    )
    api = get_client()
    try:
        health = api.health()
        active = _dataset(api)
    except ApiError as error:
        show_api_error(error)
        return
    if active is None:
        return
    configured = bool(health.get("llm_configured"))
    if not configured:
        st.info(
            "The AI Analyst needs an OpenAI API key and model on the backend (OPENAI_API_KEY "
            "and OPENAI_MODEL). Client Analytics, profiling, and column mapping work without "
            "AI.",
            icon=":material/key_off:",
        )
    try:
        mapping_state = api.column_mapping(active["id"])
    except ApiError:
        mapping_state = {}
    mapping = mapping_state.get("mapping")
    if not mapping:
        st.caption(
            ":material/info: The column mapping is not confirmed: questions about clients, "
            "dates, or revenue may need clarification. Confirm it in Data Sources."
        )

    conversation_id = _conversation_picker(api, active["id"])
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
        render_analysis(analysis)

    if not analyses and configured:
        st.markdown("**Try a question**")
        for n, question in enumerate(suggested_questions(mapping)):
            if st.button(question, key=f"aa_suggest_{n}", icon=":material/lightbulb:"):
                st.session_state[_PENDING] = question

    question = st.chat_input(
        "Ask about this dataset…" if configured else "AI is not configured",
        disabled=not configured,
        max_chars=1000,
        key="aa_input",
    )
    pending = st.session_state.pop(_PENDING, None)
    if configured and (question or pending):
        text = question or pending
        with st.chat_message("user"):
            st.markdown(md_escape(text))
        _ask(api, active["id"], text)


main()
