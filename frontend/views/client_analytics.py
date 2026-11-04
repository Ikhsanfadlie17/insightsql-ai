"""Client Analytics: who is new, returning, reactivated, or did not return.

Every number comes from the backend's deterministic SQL templates (no AI). Nothing is
shown until the user has confirmed a client identity and a transaction date column.
"""

from __future__ import annotations

from typing import Any

import streamlit as st

from frontend.api_client import ApiClient, ApiError
from frontend.components.charts import NEUTRAL, SERIES_COLORS, render_chart
from frontend.components.data_table import render_result
from frontend.components.formatting import fmt_int, fmt_pct, fmt_signed_pct
from frontend.components.layout import empty_state, md_escape, page_header, show_api_error
from frontend.components.notes import render_notes
from frontend.components.period_picker import period_picker
from frontend.state import get_client, resolve_active, set_active_dataset_id

CATEGORY_LABELS = {
    "new": "New",
    "returning": "Returning",
    "reactivated": "Reactivated",
    "between_period": "Between-period",
    "not_returned": "Not returned",
    "existing_active": "Existing active",
}
# Color follows the category everywhere (fixed categorical order).
CATEGORY_COLORS = {
    "Returning": SERIES_COLORS[0],
    "New": SERIES_COLORS[1],
    "Reactivated": SERIES_COLORS[2],
    "Between-period": SERIES_COLORS[3],
    "Not returned": NEUTRAL,
}
TREND_LABELS = {
    "period": "Period",
    "active_clients": "Active clients",
    "new_clients": "New clients",
    "transactions": "Transactions",
}
DEPARTMENT_LABELS = {
    "department": "Department",
    "previous_active": "Active (previous)",
    "current_active": "Active (current)",
    "returning": "Returning",
    "retention_rate": "Retention rate",
    "transactions_current": "Transactions (current)",
    "transactions_previous": "Transactions (previous)",
}
CLIENT_LABELS = {
    "client": "Client",
    "client_name": "Client name",
    "first_seen": "First transaction in data",
    "last_seen": "Last transaction in data",
    "transactions_previous": "Transactions (previous)",
    "transactions_current": "Transactions (current)",
}
PAGE_SIZE = 50


def _dataset_selector(api: ApiClient) -> dict[str, Any] | None:
    datasets = api.list_datasets()
    active = resolve_active(datasets)
    ready = [d for d in datasets if d["status"] == "ready"]
    if not ready or active is None:
        empty_state("Import a dataset in Data Sources to analyse clients.")
        return None
    names = {d["id"]: d["name"] for d in ready}
    chosen = st.selectbox(
        "Dataset",
        list(names),
        index=list(names).index(active["id"]),
        format_func=lambda i: names[i],
        key="ca_dataset",
    )
    if chosen != active["id"]:
        set_active_dataset_id(chosen)
        active = next(d for d in ready if d["id"] == chosen)
    return active


def _coverage_line(coverage: dict[str, Any]) -> None:
    identity = "client ID" if coverage["identity_kind"] == "client_id" else "client name (no ID)"
    st.caption(
        f"Data from **{coverage['data_start']}** to **{coverage['data_end']}** · "
        f"{fmt_int(coverage['unique_clients'])} unique clients in "
        f"{fmt_int(coverage['rows'])} rows · clients identified by {identity} "
        f"({md_escape(coverage['identity_column'])})."
    )


def _short(label: str) -> str:
    """'2025 (1 Jan–31 Oct)' -> '2025' for compact card labels."""
    return label.split(" (")[0]


def _kpis(r: dict[str, Any]) -> None:
    p, c = r["previous"]["label"], r["current"]["label"]
    defs = r.get("definitions", {})
    st.subheader("Clients", anchor=False)
    st.caption(f"Previous period: **{md_escape(p)}** · current period: **{md_escape(c)}**")
    row = st.columns(5)
    row[0].metric(
        f"Active in {_short(p)}", fmt_int(r["previous_active"]), help=defs.get("previous_active")
    )
    row[1].metric(
        f"Active in {_short(c)}", fmt_int(r["current_active"]), help=defs.get("current_active")
    )
    row[2].metric("Retention rate", fmt_pct(r["retention_rate"]), help=defs.get("retention_rate"))
    row[3].metric("Client growth", fmt_signed_pct(r["growth_rate"]), help=defs.get("growth_rate"))
    row[4].metric(
        "New client rate", fmt_pct(r["new_client_rate"]), help=defs.get("new_client_rate")
    )
    row[2].caption(
        f"{fmt_int(r['returning'])} returning ÷ {fmt_int(r['previous_active'])} active in {p}"
    )
    row[4].caption(f"{fmt_int(r['new'])} new ÷ {fmt_int(r['current_active'])} active in {c}")

    st.markdown(f"**{c}: where the {fmt_int(r['current_active'])} active clients come from**")
    row = st.columns(4)
    for col, key in zip(row, ("new", "returning", "reactivated", "between_period"), strict=True):
        col.metric(CATEGORY_LABELS[key], fmt_int(r[key]), help=defs.get(key))
    st.caption(
        f"New + Returning + Reactivated + Between-period = {fmt_int(r['current_active'])} "
        f"(each client is in exactly one category)."
    )
    left, right = st.columns(2)
    with left:
        st.metric(
            "Not returned",
            fmt_int(r["not_returned"]),
            help=defs.get("not_returned"),
        )
        st.caption(
            f"Active in {p} but not in {c}. Returning + Not returned = "
            f"{fmt_int(r['previous_active'])} active in {p}. An observation, not proof of churn."
        )
    with right:
        st.metric(
            "Existing active", fmt_int(r["existing_active"]), help=defs.get("existing_active")
        )
        st.caption(
            "Aggregate KPI: clients seen before the current period (returning + reactivated "
            "+ between-period). It overlaps those categories and is never added to them."
        )


def _transactions(r: dict[str, Any]) -> None:
    p, c = r["previous"]["label"], r["current"]["label"]
    with st.expander("Transactions (rows, not clients)"):
        st.table(
            {
                "": ["Transactions", "Repeat transactions", "Clients with 2+ transactions"],
                p: [
                    fmt_int(r["transactions_previous"]),
                    fmt_int(r["repeat_transactions_previous"]),
                    fmt_int(r["clients_with_repeat_previous"]),
                ],
                c: [
                    fmt_int(r["transactions_current"]),
                    fmt_int(r["repeat_transactions_current"]),
                    fmt_int(r["clients_with_repeat_current"]),
                ],
            }
        )
        st.caption(
            f"Excluded: {fmt_int(r['excluded_missing_id_rows'])} row(s) without a client "
            f"identity, {fmt_int(r['excluded_missing_date_rows'])} row(s) without a valid date; "
            f"{fmt_int(r['duplicate_rows_excluded'])} exact duplicate row(s) counted once."
        )


def _charts(r: dict[str, Any]) -> None:
    p, c = r["previous"]["label"], r["current"]["label"]
    left, right = st.columns(2)
    with left, st.container(border=True):
        drawn = render_chart(
            {"type": "bar", "x": "period", "y": ["clients"], "title": "Active clients"},
            ["period", "clients"],
            [[p, r["previous_active"]], [c, r["current_active"]]],
            labels={"clients": "Active clients", "period": "Period"},
            colors={p: NEUTRAL, c: SERIES_COLORS[0]},
            key="ca_chart_compare",
        )
        if not drawn:
            st.caption("No clients to compare.")
    with right, st.container(border=True):
        rows = [
            [CATEGORY_LABELS[k], r[k]]
            for k in ("returning", "new", "reactivated", "between_period")
            if r[k] > 0
        ]
        drawn = render_chart(
            {"type": "donut", "x": "category", "y": ["clients"],
             "title": f"Composition of active clients in {c}"},
            ["category", "clients"],
            rows,
            colors=CATEGORY_COLORS,
            key="ca_chart_composition",
        )  # fmt: skip
        if not drawn:
            st.caption(f"No active clients in {md_escape(c)}.")


def _trend(api: ApiClient, dataset_id: str) -> None:
    st.subheader("Client activity over time", anchor=False)
    granularity = st.segmented_control(
        "Granularity",
        ["month", "quarter", "year"],
        default="month",
        format_func=str.capitalize,
        key="ca_granularity",
    )
    try:
        trend = api.analytics_trend(dataset_id, granularity or "month")
    except ApiError as error:
        show_api_error(error)
        return
    result = trend["result"]
    with st.container(border=True):
        drawn = render_chart(
            {"type": "line", "x": "period", "y": ["active_clients", "new_clients"],
             "title": "Active and new clients"},
            result["columns"],
            result["rows"],
            labels=TREND_LABELS,
            key="ca_chart_trend",
        )  # fmt: skip
        if not drawn:
            st.caption("No dated client activity to show.")
    render_notes([n for n in trend["warnings"] if n["code"] != "history_completeness"])
    with st.expander("Trend table and definitions"):
        render_result(result, labels=TREND_LABELS)
        for name, text in trend.get("definitions", {}).items():
            st.markdown(f"- **{TREND_LABELS.get(name, name)}**: {md_escape(text)}")


def _departments(api: ApiClient, dataset_id: str, previous: dict, current: dict, dedupe) -> None:
    st.subheader("By department", anchor=False)
    try:
        out = api.analytics_departments(dataset_id, previous, current, dedupe)
    except ApiError as error:
        show_api_error(error)
        return
    result = out["result"]
    if not result["rows"]:
        st.caption("No department activity in these periods.")
        return
    with st.container(border=True):
        render_chart(
            {"type": "grouped_bar", "x": "department", "y": ["previous_active", "current_active"],
             "title": "Active clients per department"},
            result["columns"],
            result["rows"],
            labels={**DEPARTMENT_LABELS, "previous_active": previous["label"],
                    "current_active": current["label"]},
            series_colors=(NEUTRAL, SERIES_COLORS[0]),
            key="ca_chart_departments",
        )  # fmt: skip
    render_notes(out["warnings"])
    rate = result["columns"].index("retention_rate")
    rows = [[*row[:rate], fmt_pct(row[rate]), *row[rate + 1 :]] for row in result["rows"]]
    render_result({**result, "rows": rows}, labels=DEPARTMENT_LABELS)
    st.caption(
        "Retention rate per department = clients active in the department in both periods ÷ "
        "clients active in it in the previous period (N/A when there were none)."
    )


def _drill_down(api: ApiClient, dataset_id: str, previous, current, r, dedupe) -> None:
    st.subheader("Clients in a category", anchor=False)
    category = st.selectbox(
        "Category",
        list(CATEGORY_LABELS),
        format_func=lambda k: f"{CATEGORY_LABELS[k]} ({fmt_int(r[k])})",
        key="ca_category",
    )
    pages = max(1, -(-r[category] // PAGE_SIZE))
    page = 1
    if pages > 1:
        page = st.number_input("Page", 1, pages, 1, key="ca_page")
    try:
        out = api.analytics_clients(
            dataset_id, previous, current, category, (page - 1) * PAGE_SIZE, PAGE_SIZE, dedupe
        )
    except ApiError as error:
        show_api_error(error)
        return
    if not out["result"]["rows"]:
        st.caption("No clients in this category.")
        return
    render_result(out["result"], labels=CLIENT_LABELS)
    st.caption(f"Page {page} of {pages} · {fmt_int(out['total'])} client(s).")


def _identity(api: ApiClient, dataset_id: str) -> None:
    try:
        report = api.analytics_identity(dataset_id)
    except ApiError as error:
        show_api_error(error)
        return
    issues = len(report["warnings"])
    with st.expander(f"Client identity checks ({issues} note(s))"):
        if not report["warnings"]:
            st.caption("No identity problems found.")
        render_notes(report["warnings"])
        if report["examples"]["rows"]:
            render_result(
                report["examples"],
                labels={"issue": "Issue", "value": "ID or name", "variants": "Variants"},
            )


def _definitions(r: dict[str, Any]) -> None:
    with st.expander("Definitions and how the numbers were computed"):
        for key, text in r.get("definitions", {}).items():
            label = CATEGORY_LABELS.get(key, key.replace("_", " ").capitalize())
            st.markdown(f"- **{label}**: {md_escape(text)}")
        st.caption(
            "Clients are counted as unique identities (COUNT DISTINCT), never as rows. "
            "Periods include their start date and end before the next period's first day."
        )
        for sql in r.get("sql", []):
            st.code(sql, language="sql")


def main() -> None:
    page_header(
        "Client Analytics",
        "New, returning, reactivated, and not-returned clients between two periods.",
    )
    api = get_client()
    try:
        active = _dataset_selector(api)
        if active is None:
            return
        coverage = api.analytics_coverage(active["id"])
    except ApiError as error:
        if error.code == "mapping_required":
            st.info(
                f"{md_escape(error.message)} Client metrics are only shown after you confirm "
                "which column is the client ID (or name) and which is the transaction date.",
                icon=":material/rule:",
            )
        else:
            show_api_error(error)
        return
    if not coverage.get("data_start"):
        empty_state("No rows have a transaction date, so periods cannot be compared.")
        return
    _coverage_line(coverage)

    picked = period_picker(coverage)
    dedupe = st.toggle(
        "Count exact duplicate rows once",
        value=True,
        key="ca_dedupe",
        help="Rows identical in every column are counted once in transaction counts. Client "
        "counts never depend on this.",
    )
    if picked is None:
        return
    previous, current = picked
    try:
        r = api.analytics_compare(active["id"], previous, current, dedupe)
    except ApiError as error:
        show_api_error(error)
        return

    render_notes(r["warnings"])
    if r["previous_active"] == 0 and r["current_active"] == 0:
        _definitions(r)
        return
    _kpis(r)
    _charts(r)
    _transactions(r)
    _definitions(r)
    st.divider()
    _trend(api, active["id"])
    if coverage.get("department_column"):
        st.divider()
        _departments(api, active["id"], previous, current, dedupe)
    st.divider()
    _drill_down(api, active["id"], previous, current, r, dedupe)
    _identity(api, active["id"])


main()
