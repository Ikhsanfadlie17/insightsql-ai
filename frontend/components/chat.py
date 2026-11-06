"""Render one AI Analyst analysis: answer, KPIs, chart, table, insight, SQL, usage."""

from __future__ import annotations

from typing import Any

import streamlit as st

from frontend.components.charts import render_chart
from frontend.components.data_table import render_result
from frontend.components.downloads import download_buttons
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


def column_labels(columns: list[str]) -> dict[str, str]:
    """Result columns as words for charts and tables: klien_unik -> Klien unik."""
    return {c: c.replace("_", " ").capitalize() if c.islower() else c for c in columns}


def _caveats(analysis: dict[str, Any], insight: dict[str, Any] | None) -> None:
    """What the answer assumes or cannot show: visible, because it changes what it means."""
    for item in analysis.get("assumptions") or []:
        st.caption(f":material/info: Assumed: {md_escape(item)}")
    for item in (insight or {}).get("limitations", []):
        st.caption(f":material/info: {md_escape(item)}")


def _check_line(insight: dict[str, Any], retention: bool) -> None:
    """Say how far the written explanation can be trusted."""
    if insight["source"] == "llm" and retention:
        st.caption(
            ":material/verified: Facts come from the analytics engine. The interpretation was "
            "written by AI and checked: no numbers, no unproven causes, direction matches."
        )
    elif insight["source"] == "llm":
        st.caption(
            ":material/verified: Every number was checked against your data, and causes stated "
            "as fact are rejected."
        )
    else:
        reason = FALLBACK_REASONS.get(insight.get("fallback_reason") or "", "")
        st.caption(
            ":material/description: Summary built directly from the result"
            + (f" ({reason})." if reason else ".")
        )


def _insight(insight: dict[str, Any]) -> None:
    facts = insight.get("facts", [])
    interpretations = insight.get("interpretations", [])
    recommendations = insight.get("recommendations", [])
    if facts or interpretations or recommendations:
        with st.expander("Explanation and recommendations"):
            _list("Facts", facts)
            _list("Interpretation (not proven by the data)", interpretations)
            _list("Recommendations", recommendations)


def _how_answered(analysis: dict[str, Any]) -> None:
    usage = analysis.get("usage") or {}
    if not (analysis.get("sql") or analysis.get("explanation") or usage.get("requests")):
        return
    with st.expander("How this was answered"):
        if analysis.get("explanation"):
            st.markdown(md_escape(analysis["explanation"]))
        if analysis.get("sql"):
            label = (
                "Fixed analytics template"
                if analysis.get("retention")
                else "The query that ran, checked before running"
            )
            st.caption(label)
            st.code(analysis["sql"], language="sql")
        if usage.get("requests"):
            st.caption(_usage(usage))


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


def _downloads(api: Any, analysis: dict[str, Any]) -> None:
    formats = ["csv", "xlsx", "pdf"] if analysis.get("table") else []
    if analysis.get("sql"):
        formats.append("sql")
    if formats:
        download_buttons(
            api,
            analysis["question"],
            key=f"dl_{analysis['analysis_id']}",
            formats=formats,
            analysis_id=analysis["analysis_id"],
        )


def render_analysis(analysis: dict[str, Any], api: Any | None = None) -> None:
    """One question and its answer as chat messages; ``api`` enables download buttons.

    The answer and its chart come first. What changes its meaning (assumptions, limitations,
    warnings) stays visible; the table, the explanation, and the SQL are one click away.
    """
    with st.chat_message("user", avatar=":material/person:"):
        st.markdown(md_escape(analysis["question"]))
    with st.chat_message("assistant", avatar=":material/insights:"):
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
        insight = analysis.get("insight") if status == "answered" else None
        labels = column_labels(table["columns"]) if table else {}
        chart = analysis.get("chart")
        if status == "answered" and table and chart and chart["type"] != "kpi":
            render_chart(
                chart,
                table["columns"],
                table["rows"],
                labels=labels,
                colors=CATEGORY_COLORS if analysis.get("retention") else None,
                key=f"chart_{analysis['analysis_id']}",
            )
        if insight:
            _check_line(insight, retention=bool(analysis.get("retention")))
        _caveats(analysis, insight)
        render_notes(analysis.get("warnings") or [])
        if status == "answered" and table:
            rows = table["row_count"]
            with st.expander(f"See the numbers ({fmt_int(rows)} row{'s' if rows != 1 else ''})"):
                render_result(table, labels=labels)
        if insight:
            _insight(insight)
        _how_answered(analysis)
        if api is not None and status == "answered":
            _downloads(api, analysis)
