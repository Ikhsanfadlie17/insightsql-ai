"""InsightSQL AI — Streamlit entrypoint.

Run from the repository root:
    streamlit run frontend/app.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# Streamlit puts only this file's folder on sys.path; the pages import `frontend.*`.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import streamlit as st  # noqa: E402

# Not named "pages/": Streamlit would treat it as a legacy multipage folder and run
# page files directly on deep links, bypassing this entrypoint.
_PAGES = Path(__file__).resolve().parent / "views"

st.set_page_config(page_title="InsightSQL AI", page_icon=":material/insights:", layout="wide")

with st.sidebar:
    st.markdown("### InsightSQL AI")
    st.caption("AI data analyst · validated SQL on DuckDB")

navigation = st.navigation(
    [
        st.Page(
            _PAGES / "overview.py", title="Overview", icon=":material/dashboard:", default=True
        ),
        st.Page(_PAGES / "data_sources.py", title="Data Sources", icon=":material/database:"),
        st.Page(_PAGES / "ai_analyst.py", title="AI Analyst", icon=":material/chat:"),
        st.Page(_PAGES / "client_analytics.py", title="Client Analytics", icon=":material/groups:"),
        st.Page(_PAGES / "query_history.py", title="Query History", icon=":material/history:"),
        st.Page(_PAGES / "settings.py", title="Settings", icon=":material/settings:"),
    ]
)
navigation.run()
