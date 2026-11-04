"""Render backend QueryResult payloads as tables."""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st


def result_to_frame(result: dict[str, Any]) -> pd.DataFrame:
    """Build a DataFrame from a ``QueryResult`` JSON payload."""
    return pd.DataFrame(result.get("rows", []), columns=result.get("columns", []))


def render_result(
    result: dict[str, Any], labels: dict[str, str] | None = None, height: int | None = None
) -> None:
    """Show a result table; ``labels`` maps SQL column names to display headers."""
    frame = result_to_frame(result)
    config = {name: st.column_config.Column(label) for name, label in (labels or {}).items()}
    kwargs: dict[str, Any] = {"hide_index": True, "column_config": config}
    if height is not None:
        kwargs["height"] = height
    st.dataframe(frame, **kwargs)
    if result.get("truncated"):
        st.caption("Only the first rows are shown.")
