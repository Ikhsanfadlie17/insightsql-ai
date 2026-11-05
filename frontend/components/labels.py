"""Display labels for columns and honest captions for role suggestions."""

from __future__ import annotations

from collections import Counter
from typing import Any

from frontend.components.layout import md_escape


def column_labels(columns: list[dict[str, Any]]) -> dict[str, str]:
    """Map SQL column names to labels users recognise.

    Duplicate or blank headers get their position so every option is distinguishable.
    """
    counts = Counter(c["original_name"] for c in columns)
    labels: dict[str, str] = {}
    for position, column in enumerate(columns, start=1):
        original = column["original_name"]
        if not original:
            labels[column["name"]] = f"(no header, column {position})"
        elif counts[original] > 1:
            labels[column["name"]] = f"{original} (column {position})"
        else:
            labels[column["name"]] = original
    return labels


def suggestion_caption(
    candidates: list[dict[str, Any]], selected: str | None, labels: dict[str, str]
) -> str:
    """Explain the suggestion that matches the current selection (Markdown, escaped)."""
    if not candidates:
        return "No suggestion: choose a column if one fits."
    chosen = next((c for c in candidates if c["column"] == selected), None)
    if chosen is None:
        best = candidates[0]
        name = md_escape(labels.get(best["column"], best["original_name"]))
        return (
            f"Your selection differs from the suggestions. Top suggestion: **{name}** "
            f"({best['confidence']:.0%})."
        )
    name = md_escape(labels.get(chosen["column"], chosen["original_name"]))
    reasons = "; ".join(md_escape(r) for r in chosen["reasons"])
    return f"Suggested: **{name}** ({chosen['confidence']:.0%}) — {reasons}"


_DATE_TYPES = {"date", "datetime"}
_NUMBER_TYPES = {"integer", "numeric"}


def keep_as_text_conflicts(
    sheets: list[dict[str, Any]], chosen: list[str | None], text_columns: list[str]
) -> tuple[list[str], list[str]]:
    """Columns chosen under "Keep as text" that look like dates, and that look like numbers.

    ``chosen`` holds sheet names (``[None]`` for a CSV). Types come from the inspection
    sample (``detected_types``); identifier headers are already reported as text there.
    """
    detected: dict[str, str] = {}
    for sheet in sheets:
        if None in chosen or sheet["name"] in chosen:
            for header, logical in sheet.get("detected_types", {}).items():
                detected.setdefault(header, logical)
    dates = [c for c in text_columns if detected.get(c) in _DATE_TYPES]
    numbers = [c for c in text_columns if detected.get(c) in _NUMBER_TYPES]
    return dates, numbers


def _ignored_text(columns: list[str]) -> str:
    shown = ", ".join(md_escape(c) for c in columns[:3])
    more = f" and {len(columns) - 3} more" if len(columns) > 3 else ""
    return f"{len(columns)} column(s) beside the table ignored ({shown}{more})"


def layout_summary(sheet: dict[str, Any]) -> str | None:
    """What smart import found in an uploaded sheet; None for a plain table."""
    parts = []
    if sheet.get("header_row", 1) != 1:
        how = "found" if sheet.get("header_detected", True) else "chosen"
        parts.append(f"Header {how} in row {sheet['header_row']} (rows above it are skipped)")
    if sheet.get("ignored_columns"):
        parts.append(_ignored_text(sheet["ignored_columns"]))
    if sheet.get("template_rows"):
        parts.append(f"{sheet['template_rows']:,} empty template row(s) at the end will be skipped")
    return " · ".join(parts) if parts else None


def import_layout_caption(layout: dict[str, Any] | None) -> str | None:
    """How a dataset's table was read from its file; None when nothing was adjusted."""
    if not layout:
        return None
    adjusted = (
        layout["header_row"] != 1
        or layout.get("header_chosen")
        or layout.get("ignored_columns")
        or layout.get("template_rows_skipped")
    )
    if not adjusted:
        return None
    how = "chosen" if layout.get("header_chosen") else "detected"
    parts = [f"Header row {layout['header_row']} ({how})"]
    if layout.get("ignored_columns"):
        parts.append(_ignored_text(layout["ignored_columns"]))
    if layout.get("template_rows_skipped"):
        parts.append(f"{layout['template_rows_skipped']:,} empty template row(s) skipped")
    return " · ".join(parts)


_TYPE_GROUPS = (
    ("date", {"date", "datetime"}, "date", "dates"),
    ("number", {"integer", "numeric"}, "number", "numbers"),
    ("boolean", {"boolean"}, "yes/no", "yes/no"),
    ("text", {"text"}, "text", "text"),
)


def columns_summary(columns: list[dict[str, Any]]) -> str:
    """'54 columns read: 11 dates, 2 numbers, 41 text' — proof that every column came in."""
    total = len(columns)
    parts = []
    for _, types, singular, plural in _TYPE_GROUPS:
        count = sum(1 for c in columns if c.get("logical_type") in types)
        if count:
            parts.append(f"{count} {singular if count == 1 else plural}")
    noun = "column" if total == 1 else "columns"
    return f"{total} {noun} read: " + ", ".join(parts)
