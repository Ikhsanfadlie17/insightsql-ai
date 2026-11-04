"""Data Sources: upload CSV/XLSX, pick sheets, track ingestion, preview data."""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from frontend.api_client import ApiClient, ApiError
from frontend.components.data_table import render_result
from frontend.components.layout import (
    STATUS_LABELS,
    empty_state,
    fmt_count,
    md_escape,
    page_header,
    show_api_error,
)
from frontend.state import get_client, resolve_active, set_active_dataset_id, wait_for_datasets

TYPE_LABELS = {
    "integer": "Integer",
    "numeric": "Number",
    "date": "Date",
    "datetime": "Date & time",
    "boolean": "Yes/No",
    "text": "Text",
}


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
        if inspection["file_type"] == "xlsx":
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

        if st.button("Import", type="primary", disabled=not chosen, icon=":material/upload:"):
            _import(api, inspection["upload_id"], chosen, name)


def _import(api: ApiClient, upload_id: str, sheets: list[str | None], name: str | None) -> None:
    try:
        created = [api.create_dataset(upload_id, sheet, name) for sheet in sheets]
        with st.spinner("Importing and profiling data…"):
            records = wait_for_datasets(api, [c["id"] for c in created])
    except ApiError as error:
        show_api_error(error)
        return
    for record in records:
        if record["status"] == "ready":
            st.success(
                f"Imported **{md_escape(record['name'])}** ({fmt_count(record['row_count'])} rows)."
            )
        elif record["status"] == "failed":
            st.error(
                f"**{md_escape(record['name'])}** could not be imported: "
                f"{md_escape(record['error'])}"
            )
        else:
            st.warning(f"**{md_escape(record['name'])}** is still processing. Refresh in a moment.")
    ready = [r for r in records if r["status"] == "ready"]
    if ready:
        set_active_dataset_id(ready[0]["id"])


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


def _dataset_detail(api: ApiClient, dataset: dict[str, Any]) -> None:
    st.markdown(f"#### {md_escape(dataset['name'])}  \n{STATUS_LABELS[dataset['status']]}")
    if dataset["status"] == "failed":
        st.error(md_escape(dataset["error"] or "Import failed."))
        return
    if dataset["status"] == "processing":
        st.info("This dataset is still being imported.")
        return

    left, right, _ = st.columns([1, 1, 2])
    left.metric("Rows", fmt_count(dataset["row_count"]))
    right.metric("Columns", fmt_count(dataset["column_count"]))

    try:
        columns = api.columns(dataset["id"])
        preview = api.preview(dataset["id"], limit=50)
    except ApiError as error:
        show_api_error(error)
        return

    for column in columns:
        if column["failed_conversions"]:
            st.warning(
                f"{column['failed_conversions']:,} value(s) in "
                f"**{md_escape(column['original_name'])}** "
                f"could not be converted to {TYPE_LABELS[column['logical_type']].lower()} "
                "and are stored as empty.",
                icon=":material/warning:",
            )

    preview_tab, columns_tab = st.tabs(["Preview", "Columns"])
    with preview_tab:
        labels = {c["name"]: c["original_name"] for c in columns}
        render_result(preview, labels=labels)
        st.caption(f"First {preview['row_count']} rows.")
    with columns_tab:
        st.dataframe(
            pd.DataFrame(
                {
                    "Column": [c["original_name"] for c in columns],
                    "SQL name": [c["name"] for c in columns],
                    "Type": [TYPE_LABELS[c["logical_type"]] for c in columns],
                    "Unconverted values": [c["failed_conversions"] for c in columns],
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
