"""Shared page chrome: CSS, headers, empty states, errors."""

from __future__ import annotations

import re
from pathlib import Path

import streamlit as st

from frontend.api_client import ApiError
from frontend.components.formatting import fmt_int

_CSS_PATH = Path(__file__).resolve().parent.parent / "styles" / "theme.css"
_MARKDOWN_SPECIAL = re.compile(r"([\\`*_{}\[\]()#+\-.!|<>~:$])")


def md_escape(text: object) -> str:
    """Escape text from uploaded files before it is interpolated into Markdown.

    Prevents headers or cell values from rendering as links, remote images, emphasis,
    LaTeX, or icon shortcodes.
    """
    return _MARKDOWN_SPECIAL.sub(r"\\\1", str(text))


def fmt_count(value: int | None) -> str:
    """Count in Indonesian format (1.234), or "N/A" when unknown (e.g. a failed import)."""
    return fmt_int(value)


def inject_css() -> None:
    """Load the app stylesheet (design tokens + component polish)."""
    st.markdown(f"<style>{_CSS_PATH.read_text(encoding='utf-8')}</style>", unsafe_allow_html=True)


def page_header(title: str, subtitle: str) -> None:
    """Consistent page title with a one-line description."""
    inject_css()
    st.title(title)
    st.caption(subtitle)


def coming_soon(feature: str, phase: int) -> None:
    """Honest placeholder for features that are not built yet (no fake data)."""
    st.info(f"{feature} will be available after Phase {phase} of the implementation plan.")


def empty_state(message: str) -> None:
    """Shown when there is nothing to display yet."""
    st.info(message, icon=":material/inbox:")


def show_api_error(error: ApiError) -> None:
    """Render a backend error with a next step for the user."""
    if error.code == "backend_unreachable":
        st.error(
            f"{error.message} Start it with `uvicorn backend.main:app --port 8000`.",
            icon=":material/cloud_off:",
        )
    else:
        st.error(error.message or "Something went wrong.", icon=":material/error:")


STATUS_LABELS = {
    "ready": ":green[:material/check_circle: Ready]",
    "processing": ":orange[:material/hourglass_top: Processing]",
    "failed": ":red[:material/error: Failed]",
}
