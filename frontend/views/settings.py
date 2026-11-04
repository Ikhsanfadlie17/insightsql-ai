"""Settings: backend connection and AI configuration status (never secrets)."""

from __future__ import annotations

import streamlit as st

from frontend.api_client import ApiError
from frontend.components.layout import coming_soon, page_header, show_api_error
from frontend.state import get_client


def main() -> None:
    """Render the page."""
    page_header("Settings", "Connection and AI configuration. Secrets are never shown here.")
    try:
        health = get_client().health()
    except ApiError as error:
        show_api_error(error)
        return

    with st.container(border=True):
        st.markdown("**Backend**  \n:green[:material/check_circle: Connected]")
        if health["llm_configured"]:
            st.markdown(f"**AI model**  \n`{health['model']}`")
        else:
            st.markdown("**AI model**  \n:orange[:material/key_off: Not configured]")
            st.caption("Set OPENAI_API_KEY and OPENAI_MODEL in the backend environment (.env).")
    coming_soon("Usage, cost tracking, and limits", 6)


main()
