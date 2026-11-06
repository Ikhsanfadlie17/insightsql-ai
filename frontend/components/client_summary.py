"""Plain-language client summary for the Overview, built only from backend results."""

from __future__ import annotations

from typing import Any

import streamlit as st

from frontend.components.formatting import fmt_int, fmt_pct, fmt_signed_pct
from frontend.components.layout import md_escape

NO_DEPARTMENT = "(no department)"


def short_period(label: str) -> str:
    """'2025 (1 Jan–31 Oct)' -> '2025' for compact labels."""
    return label.split(" (")[0]


def top_department(result: dict[str, Any]) -> str | None:
    """The one department with the most active clients; None when there is no single leader."""
    columns = result.get("columns", [])
    if "department" not in columns or "current_active" not in columns:
        return None
    name, count = columns.index("department"), columns.index("current_active")
    rows = [r for r in result.get("rows", []) if r[name] != NO_DEPARTMENT and r[count]]
    if not rows:
        return None
    most = max(r[count] for r in rows)
    leaders = [r[name] for r in rows if r[count] == most]
    return leaders[0] if len(leaders) == 1 else None


def headline(comparison: dict[str, Any], department: str | None = None) -> str:
    """One sentence (two with a leading department) saying what changed between the periods."""
    previous, current = comparison["previous_active"], comparison["current_active"]
    if previous == 0:
        label = short_period(comparison["current"]["label"])
        text = f"{fmt_int(current)} active clients in {label}."
    elif current == previous:
        text = f"Active clients stayed at {fmt_int(current)}."
    else:
        verb = "grew" if current > previous else "fell"
        text = f"Active clients {verb} from {fmt_int(previous)} to {fmt_int(current)}."
    if department:
        text += f" {department} has the most clients."
    return text


def growth_delta(rate: float | None, previous_label: str) -> str | None:
    """Growth for ``st.metric``, which only reads a leading ASCII minus as going down."""
    if rate is None:
        return None
    return f"{fmt_signed_pct(rate).replace('−', '-')} vs {short_period(previous_label)}"


def render_headline(comparison: dict[str, Any], department: str | None = None) -> None:
    """The conclusion of the comparison, as the most prominent line of the page."""
    with st.container(key="summary_headline"):
        st.markdown(f":material/insights: {md_escape(headline(comparison, department))}")


def render_kpis(r: dict[str, Any], did_not_return: bool = False) -> None:
    """The main client numbers, each with what it is measured against."""
    previous_label = short_period(r["previous"]["label"])
    definitions = r.get("definitions", {})
    rate = r["growth_rate"]
    flat = rate is not None and round(rate * 100, 1) == 0
    cards = st.columns(4 if did_not_return else 3)
    cards[0].metric(
        "Active clients",
        fmt_int(r["current_active"]),
        delta=growth_delta(rate, previous_label),
        delta_color="off" if flat else "normal",
        delta_arrow="off" if flat else "auto",
        help=definitions.get("current_active"),
    )
    cards[1].metric(
        "Came back",
        fmt_pct(r["retention_rate"]),
        delta=(
            f"{fmt_int(r['returning'])} of {fmt_int(r['previous_active'])} clients"
            if r["previous_active"]
            else None
        ),
        delta_color="off",
        delta_arrow="off",
        help=definitions.get("retention_rate"),
    )
    cards[2].metric(
        "New clients",
        fmt_int(r["new"]),
        delta=f"{fmt_pct(r['new_client_rate'])} of active" if r["current_active"] else None,
        delta_color="off",
        delta_arrow="off",
        help=definitions.get("new"),
    )
    if did_not_return:
        cards[3].metric(
            "Did not return",
            fmt_int(r["not_returned"]),
            delta=(
                f"of {fmt_int(r['previous_active'])} clients in {previous_label}"
                if r["previous_active"]
                else None
            ),
            delta_color="off",
            delta_arrow="off",
            help=definitions.get("not_returned"),
        )
