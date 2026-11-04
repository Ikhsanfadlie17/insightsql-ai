"""Render one AI Analyst analysis: answer, KPIs, chart, table, insight, SQL, usage."""

from __future__ import annotations

from typing import Any

import streamlit as st

from frontend.components.charts import render_chart
from frontend.components.data_table import render_result
from frontend.components.formatting import fmt_int, fmt_number, fmt_pct, fmt_signed_pct
from frontend.components.layout import md_escape
from frontend.components.notes import render_notes

FALLBACK_REASONS = {
    "ungrounded_numbers": "the AI text contained numbers that are not in the result",
    "causal_claim": "the AI text stated causes that the data cannot show",
    "unknown_reference": "the AI text referred to values that are not in the result",
    "wrong_direction": "the AI text described a change in the wrong direction",
    "empty_answer": "the AI returned no summary",
    "llm_error": "the AI provider could not be reached",
    "llm_insights_disabled": "AI insights are turned off",
    "llm_not_configured": "AI is not configured",
    "empty_result": "there is nothing to explain",
}
CATEGORY_COLORS = {
    "Returning": "#2a78d6",
    "New": "#eb6834",
    "Reactivated": "#1baf7a",
    "Between-period": "#eda100",
}


def format_kpi(value: float | int | None, fmt: str) -> str:
    """KPI value in Indonesian format; ``None`` -> N/A."""
    if fmt == "pct":
        return fmt_pct(value)
    if fmt == "signed_pct":
        return fmt_signed_pct(value)
    if fmt == "int":
        return fmt_int(value)
    if fmt == "plain":
        return "N/A" if value is None else str(value)
    if value is None:
        return "N/A"
    return fmt_int(value) if float(value).is_integer() else fmt_number(value, 2)


def _kpis(kpis: list[dict[str, Any]]) -> None:
    for start in range(0, len(kpis), 5):
        chunk = kpis[start : start + 5]
        for col, kpi in zip(st.columns(len(chunk)), chunk, strict=True):
            col.metric(kpi["label"], format_kpi(kpi["value"], kpi["format"]),
                       help=kpi.get("definition"))  # fmt: skip


def _list(title: str, items: list[str]) -> None:
    if items:
        st.markdown(f"**{title}**")
        st.markdown("\n".join(f"- {md_escape(item)}" for item in items))


def _insight(insight: dict[str, Any], retention: bool = False) -> None:
    with st.container(border=True):
        if insight["source"] == "llm" and retention:
            st.caption(
                ":material/verified: Facts from the analytics engine. The interpretation was "
                "written by AI and checked: no numbers, no unproven causes, direction matches."
            )
        elif insight["source"] == "llm":
            st.caption(
                ":material/verified: AI explanation — each number was checked to exist in, or "
                "be derived from, the query result; causes stated as fact are rejected."
            )
        else:
            reason = FALLBACK_REASONS.get(insight.get("fallback_reason") or "", "")
            st.caption(
                ":material/description: Template summary built from the result"
                + (f" ({reason})." if reason else ".")
            )
        _list("Facts", insight.get("facts", []))
        _list("Interpretation (not proven by the data)", insight.get("interpretations", []))
        _list("Recommendations", insight.get("recommendations", []))
        _list("Limitations", insight.get("limitations", []))


def _usage(usage: dict[str, Any]) -> str:
    if not usage.get("requests"):
        return "No AI requests."
    cost = usage.get("est_cost_usd")
    cost_text = (
        f"≈ US${cost:.4f}" + ("" if usage.get("cost_complete") else " (partial)")
        if cost is not None
        else "cost not available (no price configured)"
    )
    return (
        f"{usage['requests']} AI request(s) · {fmt_int(usage['input_tokens'])} input / "
        f"{fmt_int(usage['output_tokens'])} output tokens · {cost_text}"
    )


def render_analysis(analysis: dict[str, Any]) -> None:
    """One question and its answer as chat messages."""
    with st.chat_message("user"):
        st.markdown(md_escape(analysis["question"]))
    with st.chat_message("assistant"):
        status = analysis["status"]
        answer = md_escape(analysis.get("answer") or "")
        if status == "clarification":
            st.info(answer, icon=":material/help:")
        elif status == "unsupported":
            st.warning(answer, icon=":material/block:")
        elif status == "error":
            st.error(answer, icon=":material/error:")
        else:
            st.markdown(answer)
        if analysis.get("kpis"):
            _kpis(analysis["kpis"])
        table = analysis.get("table")
        if status == "answered" and table:
            if analysis.get("chart") and analysis["chart"]["type"] != "kpi":
                render_chart(
                    analysis["chart"],
                    table["columns"],
                    table["rows"],
                    colors=CATEGORY_COLORS if analysis.get("retention") else None,
                    key=f"chart_{analysis['analysis_id']}",
                )
            with st.expander(f"Result table ({fmt_int(table['row_count'])} row(s))"):
                render_result(table)
        render_notes(analysis.get("warnings") or [])
        if analysis.get("insight") and status == "answered":
            _insight(analysis["insight"], retention=bool(analysis.get("retention")))
        if analysis.get("sql") or analysis.get("explanation") or analysis.get("assumptions"):
            with st.expander("How this was answered"):
                if analysis.get("explanation"):
                    st.markdown(md_escape(analysis["explanation"]))
                _list("Assumptions", analysis.get("assumptions", []))
                if analysis.get("sql"):
                    label = (
                        "Analytics template (fixed SQL)"
                        if analysis.get("retention")
                        else ("Validated SQL")
                    )
                    st.caption(label)
                    st.code(analysis["sql"], language="sql")
        st.caption(_usage(analysis.get("usage") or {}))
