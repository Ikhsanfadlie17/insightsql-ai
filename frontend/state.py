"""Session-scoped state: the API client and the active dataset."""

from __future__ import annotations

import os
import time
from typing import Any

import streamlit as st

from frontend.api_client import ApiClient

_CLIENT_KEY = "api_client"
_ACTIVE_KEY = "active_dataset_id"
DEFAULT_BACKEND_URL = "http://localhost:8000"
# AI Analyst: the open conversation per dataset, and the one to select on the next run.
AA_CONVERSATIONS = "aa_conversation_by_dataset"
AA_SELECT = "aa_select_conversation"
AA_PENDING = "aa_pending_question"  # asked as soon as the AI Analyst runs
# Relative to the entrypoint (frontend/app.py).
AI_ANALYST_PAGE = "views/ai_analyst.py"
CLIENT_ANALYTICS_PAGE = "views/client_analytics.py"
DATA_SOURCES_PAGE = "views/data_sources.py"


def get_client() -> ApiClient:
    """Return the session's API client (tests inject a fake under the same key)."""
    if _CLIENT_KEY not in st.session_state:
        st.session_state[_CLIENT_KEY] = ApiClient(
            os.environ.get("BACKEND_URL", DEFAULT_BACKEND_URL)
        )
    return st.session_state[_CLIENT_KEY]


def get_active_dataset_id() -> str | None:
    """ID of the dataset the user is working with, if any."""
    return st.session_state.get(_ACTIVE_KEY)


def set_active_dataset_id(dataset_id: str | None) -> None:
    """Remember the dataset the user is working with."""
    st.session_state[_ACTIVE_KEY] = dataset_id


def open_in_ai_analyst(dataset_id: str, conversation_id: str | None) -> None:
    """Make the AI Analyst show this dataset and conversation on its next run.

    A keyed selectbox keeps the browser's value, so the choice is written to Session
    State before the AI Analyst creates its widget.
    """
    set_active_dataset_id(dataset_id)
    st.session_state.setdefault(AA_CONVERSATIONS, {})[dataset_id] = conversation_id
    st.session_state[AA_SELECT] = (dataset_id, conversation_id)


def ask_in_ai_analyst(dataset_id: str, question: str) -> None:
    """Go to the AI Analyst and ask this question in a new conversation."""
    open_in_ai_analyst(dataset_id, None)
    st.session_state[AA_PENDING] = question
    st.switch_page(AI_ANALYST_PAGE)


def resolve_active(datasets: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return the active dataset; only a ready dataset can be active.

    Falls back to the newest ready dataset when the remembered one is missing,
    failed, or still processing.
    """
    by_id = {d["id"]: d for d in datasets}
    active = by_id.get(get_active_dataset_id() or "")
    if active is None or active["status"] != "ready":
        active = next((d for d in datasets if d["status"] == "ready"), None)
        set_active_dataset_id(active["id"] if active else None)
    return active


def wait_for_datasets(
    client: ApiClient, dataset_ids: list[str], timeout_s: float = 180, interval_s: float = 0.5
) -> list[dict[str, Any]]:
    """Poll until no dataset is ``processing`` (or the timeout passes)."""
    deadline = time.monotonic() + timeout_s
    while True:
        records = [client.get_dataset(i) for i in dataset_ids]
        if all(r["status"] != "processing" for r in records) or time.monotonic() > deadline:
            return records
        time.sleep(interval_s)
