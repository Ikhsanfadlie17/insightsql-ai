"""Client Analytics: who is new, who came back, and who did not return.

Every number comes from the backend's deterministic SQL templates (no AI). Nothing is
shown until the user has confirmed a client identity and a transaction date column. The
conclusion comes first; periods, notes, and definitions stay one click away.
"""

from __future__ import annotations

from typing import Any

import streamlit as st

from frontend.api_client import ApiClient, ApiError
from frontend.components.charts import NEUTRAL, SERIES_COLORS, render_chart
from frontend.components.client_summary import render_headline, render_kpis, top_department
from frontend.components.data_table import render_result
from frontend.components.downloads import download_buttons
from frontend.components.formatting import fmt_int, fmt_pct
from frontend.components.layout import empty_state, md_escape, page_header, show_api_error
from frontend.components.notes import render_notes
from frontend.components.period_picker import period_picker
from frontend.state import DATA_SOURCES_PAGE, get_client, resolve_active, set_active_dataset_id

CATEGORY_LABELS = {
    "new": "New clients",
    "returning": "Returning clients",
    "reactivated": "Back after a break",
    "between_period": "Joined between periods",
    "not_returned": "Did not return",
    "existing_active": "Existing clients",
}
# Color follows the category everywhere (fixed categorical order).
CATEGORY_COLORS = {
    CATEGORY_LABELS["returning"]: SERIES_COLORS[0],
    CATEGORY_LABELS["new"]: SERIES_COLORS[1],
    CATEGORY_LABELS["reactivated"]: SERIES_COLORS[2],
    CATEGORY_LABELS["between_period"]: SERIES_COLORS[3],
    CATEGORY_LABELS["not_returned"]: NEUTRAL,
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


def _dataset_selector(datasets: list[dict[str, Any]], active: dict[str, Any]) -> dict[str, Any]:
    ready = [d for d in datasets if d["status"] == "ready"]
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


def _composition(r: dict[str, Any]) -> None:
    """Where the active clients of the current period came from."""
    c = r["current"]["label"]
    definitions = r.get("definitions", {})
    st.subheader(f"Where the {fmt_int(r['current_active'])} active clients came from", anchor=False)
    chart, groups = st.columns([3, 2], vertical_alignment="center")
    with chart, st.container(border=True):
        rows = [
            [CATEGORY_LABELS[k], r[k]]
            for k in ("returning", "new", "reactivated", "between_period")
            if r[k] > 0
        ]
        drawn = render_chart(
            {"type": "donut", "x": "category", "y": ["clients"], "title": ""},
            ["category", "clients"],
            rows,
            colors=CATEGORY_COLORS,
            key="ca_chart_composition",
        )
        if not drawn:
            st.caption(f"No active clients in {md_escape(c)}.")
    with groups:
        for key in ("returning", "reactivated", "between_period"):
            st.metric(CATEGORY_LABELS[key], fmt_int(r[key]), help=definitions.get(key))
    st.caption(
        f"New, returning, back after a break, and joined between periods add up to "
        f"{fmt_int(r['current_active'])}. Each client is in exactly one group."
    )


def _data_notes(r: dict[str, Any]) -> None:
    """Notes that change how to read the numbers: counted up front, one click away."""
    notes = r["warnings"]
    if not notes:
        return
    icon = ":material/warning:" if any(n["level"] == "warning" for n in notes) else None
    label = f"{fmt_int(len(notes))} note{'s' if len(notes) > 1 else ''} about this data"
    with st.expander(label, icon=icon):
        render_notes(notes)


def _how_counted(r: dict[str, Any], coverage: dict[str, Any], api: ApiClient, dataset_id) -> None:
    """Coverage, overlapping totals, transactions, identity checks, and definitions."""
    p, c = r["previous"]["label"], r["current"]["label"]
    definitions = r.get("definitions", {})
    _coverage_line(coverage)
    st.markdown(
        f"**Did not return:** active in {md_escape(p)} but not in {md_escape(c)}. Returning "
        f"clients + did not return = {fmt_int(r['previous_active'])} active in {md_escape(p)}. "
        "This is an observation, not proof that a client left."
    )
    st.metric(
        CATEGORY_LABELS["existing_active"],
        fmt_int(r["existing_active"]),
        help=definitions.get("existing_active"),
    )
    st.caption(
        "Clients seen before the current period: returning, back after a break, and joined "
        "between periods. It overlaps those groups and is never added to them."
    )
    _transactions(r)
    _identity(api, dataset_id)
    _definitions(r)


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


def _trend(api: ApiClient, dataset_id: str, dedupe: bool) -> None:
    granularity = st.segmented_control(
        "Granularity",
        ["month", "quarter", "year"],
        default="month",
        format_func=str.capitalize,
        key="ca_granularity",
    )
    try:
        trend = api.analytics_trend(dataset_id, granularity or "month", dedupe)
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


def _departments(out: dict[str, Any], previous: dict, current: dict) -> None:
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
    category = st.selectbox(
        "Show clients who are",
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


def _results(
    api: ApiClient,
    dataset_id: str,
    coverage: dict[str, Any],
    r: dict[str, Any],
    periods: tuple[dict, dict],
    dedupe: bool,
) -> None:
    """The conclusion and the main numbers, then the rest in tabs."""
    previous, current = periods
    departments, department_error = None, None
    if coverage.get("department_column"):
        try:
            departments = api.analytics_departments(dataset_id, previous, current, dedupe)
        except ApiError as error:
            department_error = error
    render_headline(r, top_department(departments["result"]) if departments else None)
    render_kpis(r, did_not_return=True)
    _data_notes(r)

    labels = ["Summary", "Over time"]
    if coverage.get("department_column"):
        labels.append("By department")
    labels += ["Client list", "How it's counted"]
    tabs = dict(zip(labels, st.tabs(labels), strict=True))
    with tabs["Summary"]:
        _composition(r)
        st.caption("Download this comparison with its periods, definitions, and template SQL")
        download_buttons(
            api,
            f"client analytics {previous['label']} vs {current['label']}",
            key="ca_download",
            dataset_id=dataset_id,
            previous=previous,
            current=current,
            dedupe=dedupe,
        )
    with tabs["Over time"]:
        _trend(api, dataset_id, dedupe)
    if "By department" in tabs:
        with tabs["By department"]:
            if department_error:
                show_api_error(department_error)
            elif departments:
                _departments(departments, previous, current)
    with tabs["Client list"]:
        _drill_down(api, dataset_id, previous, current, r, dedupe)
    with tabs["How it's counted"]:
        _how_counted(r, coverage, api, dataset_id)


def main() -> None:
    page_header("Client Analytics", "Who is new, who came back, and who did not return.")
    api = get_client()
    try:
        datasets = api.list_datasets()
        active = resolve_active(datasets)
        if active is None:
            empty_state("Import a dataset in Data Sources to analyse clients.")
            return
        panel = st.expander("Change period or dataset", icon=":material/tune:")
        with panel:
            active = _dataset_selector(datasets, active)
        coverage = api.analytics_coverage(active["id"])
    except ApiError as error:
        if error.code == "mapping_required":
            st.info(
                f"{md_escape(error.message)} Client numbers are only shown after you confirm "
                "which column is the client ID (or name) and which is the transaction date.",
                icon=":material/rule:",
            )
            if st.button("Open Data Sources", key="ca_to_mapping"):
                st.switch_page(DATA_SOURCES_PAGE)
        else:
            show_api_error(error)
        return
    if not coverage.get("data_start"):
        empty_state("No rows have a transaction date, so periods cannot be compared.")
        return

    with panel:
        picked = period_picker(coverage)
        dedupe = st.toggle(
            "Count exact duplicate rows once",
            value=True,
            key="ca_dedupe",
            help="Rows identical in every column are counted once in transaction counts. Client "
            "counts never depend on this.",
        )
    if picked is None:
        st.info("Fix the period under Change period or dataset to see the numbers.")
        return
    previous, current = picked
    try:
        r = api.analytics_compare(active["id"], previous, current, dedupe)
    except ApiError as error:
        show_api_error(error)
        return

    st.caption(
        f"{md_escape(active['name'])} · {md_escape(current['label'])}, compared with "
        f"{md_escape(previous['label'])}"
    )
    if r["previous_active"] == 0 and r["current_active"] == 0:
        render_notes(r["warnings"])
        _definitions(r)
        return
    _results(api, active["id"], coverage, r, picked, dedupe)


main()
