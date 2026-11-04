"""Render backend notes (data-quality explanations) consistently."""

from __future__ import annotations

from typing import Any

import streamlit as st

from frontend.components.layout import md_escape


def render_notes(notes: list[dict[str, Any]], prefix: str = "") -> None:
    """Show warning notes prominently and informational notes as captions.

    Note messages may quote values from the uploaded file, so they are escaped.
    """
    for note in notes:
        message = md_escape(note["message"])
        if prefix:
            message = f"**{md_escape(prefix)}**: {message}"
        if note["level"] == "warning":
            st.warning(message, icon=":material/warning:")
        else:
            st.caption(f":material/info: {message}")
