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
