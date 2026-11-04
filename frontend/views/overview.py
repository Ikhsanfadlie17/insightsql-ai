"""Overview: workspace status at a glance."""

from __future__ import annotations

import streamlit as st

from frontend.api_client import ApiError
from frontend.components.layout import coming_soon, empty_state, page_header, show_api_error
from frontend.state import get_client, resolve_active


def main() -> None:
    """Render the page."""
    page_header("Overview", "Your datasets and analysis activity.")
    api = get_client()
    try:
        health = api.health()
        datasets = api.list_datasets()
    except ApiError as error:
        show_api_error(error)
        return

    ready = [d for d in datasets if d["status"] == "ready"]
    active = resolve_active(datasets)
    cols = st.columns(4)
    cols[0].metric("Datasets", len(ready))
    cols[1].metric("Active dataset", active["name"] if active else "None")
    cols[2].metric("Total records", f"{active['row_count']:,}" if active else "N/A")
    cols[3].metric("Total columns", f"{active['column_count']:,}" if active else "N/A")

    if not health["llm_configured"]:
        st.warning(
            "AI features are not configured. Set OPENAI_API_KEY and OPENAI_MODEL on the backend.",
            icon=":material/key_off:",
        )
    if not datasets:
        empty_state("Upload a dataset in Data Sources to begin.")
    coming_soon("Unique clients, analysis counts, and recent activity", 3)


main()
