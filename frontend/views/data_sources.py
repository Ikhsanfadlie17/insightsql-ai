"""Data Sources: upload, import options, profile, and column-mapping confirmation."""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from frontend.api_client import ApiClient, ApiError
from frontend.components.data_table import render_result
from frontend.components.labels import column_labels, suggestion_caption
from frontend.components.layout import (
    STATUS_LABELS,
    empty_state,
    fmt_count,
    md_escape,
    page_header,
    show_api_error,
)
from frontend.components.notes import render_notes
from frontend.state import get_client, resolve_active, set_active_dataset_id, wait_for_datasets

TYPE_LABELS = {
    "integer": "Integer",
    "numeric": "Number",
    "date": "Date",
    "datetime": "Date & time",
    "boolean": "Yes/No",
    "text": "Text",
}
NUMBER_FORMATS = {
    "auto": "Auto-detect (recommended)",
    "id": "Indonesian — 1.234,5",
    "en": "English — 1,234.5",
}
DATE_ORDERS = {
    "auto": "Auto-detect (day/month when ambiguous)",
    "day_first": "Day/Month/Year — 03/04/2026 = 3 April",
    "month_first": "Month/Day/Year — 03/04/2026 = 4 March",
}
ROLE_LABELS = {
    "client_id": "Client ID",
    "client_name": "Client name",
    "transaction_date": "Transaction date",
    "department": "Department",
    "service_type": "Service type",
    "revenue": "Revenue",
    "status": "Status",
}
NOT_USED = "— Not used —"


# ------------------------------------------------------------------- upload
def _upload_section(api: ApiClient) -> None:
    with st.container(border=True):
        st.subheader("Upload a file")
        uploaded = st.file_uploader(
            "Excel (.xlsx) or CSV file",
            type=["xlsx", "csv"],
            help="Each Excel sheet becomes its own dataset.",
        )
        if uploaded is None:
            return

        if st.session_state.get("inspected_file_id") != uploaded.file_id:
            try:
                st.session_state["inspection"] = api.inspect_upload(
                    uploaded.name, uploaded.getvalue()
                )
                st.session_state["inspected_file_id"] = uploaded.file_id
            except ApiError as error:
                show_api_error(error)
                return
        inspection: dict[str, Any] = st.session_state["inspection"]

        sheets = inspection["sheets"]
        is_excel = inspection["file_type"] == "xlsx"
        if is_excel:
            options = [s["name"] for s in sheets]
            rows = {s["name"]: s["row_count"] for s in sheets}
            chosen = st.multiselect(
                "Sheets to import",
                options,
                default=options[:1],
                format_func=lambda name: f"{name} ({rows[name]:,} rows)",
            )
        else:
            chosen = [None]
            st.caption(f"{sheets[0]['row_count']:,} data rows detected.")

        name = None
        if len(chosen) == 1:
            name = st.text_input("Dataset name (optional)", max_chars=120) or None

        left, right = st.columns(2)
        if is_excel:
            number_format = "auto"
            left.caption(
                "Numbers stored as numbers in Excel are read exactly as stored, so no "
                "number format is needed."
            )
        else:
            number_format = left.selectbox(
                "Number format",
                list(NUMBER_FORMATS),
                format_func=NUMBER_FORMATS.__getitem__,
                help="Auto-detect warns you when a value such as 1.234 could be read both ways.",
            )
        date_order = right.selectbox(
            "Date order for dates written as text",
            list(DATE_ORDERS),
            format_func=DATE_ORDERS.__getitem__,
        )
        headers = list(
            dict.fromkeys(
                h for s in sheets if s["name"] in chosen or not is_excel for h in s["columns"] if h
            )
        )
        text_columns = st.multiselect(
            "Keep as text",
            headers,
            help="Columns that must never be converted to numbers or dates, such as IDs, "
            "codes, or phone numbers. Headers like 'ID', 'Kode', 'No', 'NIK' or 'Telepon' "
            "are already kept as text automatically.",
        )

        if st.button("Import", type="primary", disabled=not chosen, icon=":material/upload:"):
            _import(
                api, inspection["upload_id"], chosen, name, number_format, date_order, text_columns
            )


def _sheet_headers(sheet: str | None) -> list[str]:
    inspection = st.session_state.get("inspection") or {}
    for info in inspection.get("sheets", []):
        if sheet is None or info["name"] == sheet:
            return info.get("columns", [])
    return []


def _import(
    api: ApiClient,
    upload_id: str,
    sheets: list[str | None],
    name: str | None,
    number_format: str,
    date_order: str,
    text_columns: list[str],
) -> None:
    try:
        created = []
        for sheet in sheets:
            headers = set(_sheet_headers(sheet))
            keep = [h for h in text_columns if h in headers]
            created.append(
                api.create_dataset(upload_id, sheet, name, number_format, date_order, keep)
            )
        with st.spinner("Importing and profiling data…"):
            records = wait_for_datasets(api, [c["id"] for c in created])
    except ApiError as error:
        show_api_error(error)
        return
    for record in records:
        label = md_escape(record["name"])
        if record["status"] == "ready":
            st.success(f"Imported **{label}** ({fmt_count(record['row_count'])} rows).")
        elif record["status"] == "failed":
            st.error(f"**{label}** could not be imported: {md_escape(record['error'])}")
        else:
            st.warning(f"**{label}** is still processing. Refresh in a moment.")
    ready = [r for r in records if r["status"] == "ready"]
    if ready:
        set_active_dataset_id(ready[0]["id"])


# ------------------------------------------------------------------ details
def _datasets_table(datasets: list[dict[str, Any]]) -> None:
    frame = pd.DataFrame(
        {
            "Name": [d["name"] for d in datasets],
            "Status": [d["status"].title() for d in datasets],
            "Rows": [d["row_count"] for d in datasets],
            "Columns": [d["column_count"] for d in datasets],
            "Source": [d["source_filename"] for d in datasets],
            "Created": [d["created_at"][:16].replace("T", " ") for d in datasets],
        }
    )
    st.dataframe(frame, hide_index=True)


def _fmt_number(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value:,.0f}" if float(value).is_integer() else f"{value:,.2f}"


def _profile_tab(profile: dict[str, Any]) -> None:
    cols = st.columns(4)
    cols[0].metric("Rows", fmt_count(profile["row_count"]))
    cols[1].metric("Columns", fmt_count(profile["column_count"]))
    cols[2].metric("Duplicate rows", fmt_count(profile["duplicate_row_count"]))
    cols[3].metric("Missing cells", f"{profile['missing_pct']:.1f}%")

    table = pd.DataFrame(
        {
            "Column": list(column_labels(profile["columns"]).values()),
            "Type": [TYPE_LABELS[c["logical_type"]] for c in profile["columns"]],
            "Missing": [f"{c['null_count']:,} ({c['null_pct']:.1f}%)" for c in profile["columns"]],
            "Unique": [c["unique_count"] for c in profile["columns"]],
            "Min": [_fmt_number(c["min"]) or (c["date_min"] or "") for c in profile["columns"]],
            "Max": [_fmt_number(c["max"]) or (c["date_max"] or "") for c in profile["columns"]],
            "Mean": [_fmt_number(c["mean"]) for c in profile["columns"]],
            "Median": [_fmt_number(c["median"]) for c in profile["columns"]],
            "Most frequent": [
                ", ".join(f"{v['value']} ({v['count']:,})" for v in c["top_values"][:3])
                for c in profile["columns"]
            ],
        }
    )
    st.dataframe(table, hide_index=True)


def _mapping_tab(
    api: ApiClient, dataset_id: str, state: dict[str, Any], labels: dict[str, str]
) -> None:
    flash_key = f"mapping-confirmed-{dataset_id}"
    if st.session_state.pop(flash_key, False):
        st.success("Column mapping saved.", icon=":material/check_circle:")
    saved = state["mapping"]
    if saved:
        st.markdown(
            f":green[:material/verified: **Column mapping confirmed**] "
            f"on {saved['confirmed_at'][:16].replace('T', ' ')} UTC. "
            "You can change it below at any time."
        )
    else:
        st.markdown(
            ":orange[:material/pending: **Column mapping not confirmed.**] "
            "The selections below are suggestions. They are not used in any analysis "
            "until you review and confirm them."
        )
    if state.get("unique_clients") is not None:
        st.caption(
            f"{state['unique_clients']:,} unique clients (counted by unique client identity, "
            "not by transactions)."
        )

    current = saved or state["draft"]
    suggestions = {s["role"]: s["candidates"] for s in state["suggestions"]}
    with st.form(f"mapping-{dataset_id}"):
        choices: dict[str, str | None] = {}
        for role, label in ROLE_LABELS.items():
            options = [NOT_USED, *state["compatible_columns"].get(role, [])]
            selected = current.get(role)
            index = options.index(selected) if selected in options else 0
            value = st.selectbox(
                label,
                options,
                index=index,
                key=f"role-{dataset_id}-{role}",
                format_func=lambda name: labels.get(name, name),
            )
            choices[role] = None if value == NOT_USED else value
            st.caption(suggestion_caption(suggestions.get(role) or [], choices[role], labels))
        submitted = st.form_submit_button("Confirm column mapping", type="primary")

    if submitted:
        try:
            api.confirm_mapping(dataset_id, choices)
        except ApiError as error:
            show_api_error(error)
        else:
            # Re-render so the status, client count, and warnings reflect the saved mapping.
            st.session_state[flash_key] = True
            st.rerun()
    render_notes(state["warnings"])


def _dataset_detail(api: ApiClient, dataset: dict[str, Any]) -> None:
    st.markdown(f"#### {md_escape(dataset['name'])}  \n{STATUS_LABELS[dataset['status']]}")
    if dataset["status"] == "failed":
        st.error(md_escape(dataset["error"] or "Import failed."))
        return
    if dataset["status"] == "processing":
        st.info("This dataset is still being imported.")
        return
    if dataset.get("number_format") in NUMBER_FORMATS and dataset["number_format"] != "auto":
        st.caption(f"Number format: {NUMBER_FORMATS[dataset['number_format']]}")

    profile_tab, mapping_tab, preview_tab, columns_tab = st.tabs(
        ["Profile", "Column mapping", "Preview", "Columns"]
    )
    columns: list[dict[str, Any]] = []
    try:
        columns = api.columns(dataset["id"])
    except ApiError as error:
        show_api_error(error)

    with profile_tab:
        try:
            profile = api.profile(dataset["id"])
        except ApiError as error:
            show_api_error(error)
        else:
            render_notes(profile["notes"])
            for column in profile["columns"]:
                render_notes(column["notes"], prefix=column["original_name"] or column["name"])
            _profile_tab(profile)

    with mapping_tab:
        try:
            state = api.column_mapping(dataset["id"])
        except ApiError as error:
            show_api_error(error)
        else:
            _mapping_tab(api, dataset["id"], state, column_labels(columns))

    with preview_tab:
        try:
            preview = api.preview(dataset["id"], limit=50)
        except ApiError as error:
            show_api_error(error)
        else:
            render_result(preview, labels={c["name"]: c["original_name"] for c in columns})
            st.caption(f"First {preview['row_count']} rows.")

    with columns_tab:
        if columns:
            st.dataframe(
                pd.DataFrame(
                    {
                        "Column": [c["original_name"] for c in columns],
                        "SQL name": [c["name"] for c in columns],
                        "Type": [TYPE_LABELS[c["logical_type"]] for c in columns],
                        "Unconverted values": [c["failed_conversions"] for c in columns],
                        "Notes": [len(c.get("notes", [])) for c in columns],
                    }
                ),
                hide_index=True,
            )


def main() -> None:
    """Render the page."""
    page_header("Data Sources", "Upload Excel or CSV files and check how they were read.")
    api = get_client()
    _upload_section(api)

    try:
        datasets = api.list_datasets()
    except ApiError as error:
        show_api_error(error)
        return

    st.subheader("Datasets")
    if not datasets:
        empty_state("No datasets yet. Upload a file to get started.")
        return
    _datasets_table(datasets)

    active = resolve_active(datasets)
    ids = [d["id"] for d in datasets]
    names = {d["id"]: d["name"] for d in datasets}
    selected = st.selectbox(
        "Inspect dataset",
        ids,
        index=ids.index(active["id"]) if active else 0,
        format_func=names.__getitem__,
    )
    chosen = next(d for d in datasets if d["id"] == selected)
    if chosen["status"] == "ready":
        set_active_dataset_id(selected)
    with st.container(border=True):
        _dataset_detail(api, chosen)


main()
