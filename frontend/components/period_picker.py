"""Period selection for client analytics: year vs year, year-to-date, or custom ranges.

Periods are sent to the backend as ``{label, start, end}`` with an **exclusive** end;
the user always sees and picks inclusive dates.
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta
from typing import Any

import streamlit as st

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
MODES = ("Year vs year", "Year-to-date", "Custom ranges")


def _day(value: date) -> str:
    return f"{value.day} {_MONTHS[value.month - 1]} {value.year}"


def year_period(year: int, through: date | None = None) -> dict[str, str]:
    """Calendar year, or 1 Jan up to ``through``'s month/day (29 Feb -> 28 Feb)."""
    if through is None:
        return {"label": str(year), "start": f"{year}-01-01", "end": f"{year + 1}-01-01"}
    day = min(through.day, calendar.monthrange(year, through.month)[1])
    last = date(year, through.month, day)
    return {
        "label": f"{year} (1 Jan–{last.day} {_MONTHS[last.month - 1]})",
        "start": f"{year}-01-01",
        "end": (last + timedelta(days=1)).isoformat(),
    }


def custom_period(start: date, last_day: date) -> dict[str, str]:
    """A user-chosen inclusive range as a backend period (exclusive end)."""
    return {
        "label": f"{_day(start)} – {_day(last_day)}",
        "start": start.isoformat(),
        "end": (last_day + timedelta(days=1)).isoformat(),
    }


def period_problem(previous: dict[str, str], current: dict[str, str]) -> str | None:
    """A message when the two periods cannot be compared, else ``None``."""
    for period in (previous, current):
        if period["start"] >= period["end"]:
            return f"{period['label']}: the start date must be on or before the end date."
    if previous["end"] > current["start"]:
        return (
            "The previous period must end before the current period starts (the periods "
            "may not overlap)."
        )
    return None


def _range(value: Any) -> tuple[date, date] | None:
    if isinstance(value, tuple | list) and len(value) == 2 and all(value):
        return value[0], value[1]
    return None


def period_picker(coverage: dict[str, Any]) -> tuple[dict, dict] | None:
    """Render the picker; return ``(previous, current)`` or ``None`` when invalid."""
    years: list[int] = coverage.get("years") or []
    data_end = date.fromisoformat(coverage["data_end"])
    data_start = date.fromisoformat(coverage["data_start"])
    # A year that is not complete in the data is compared year-to-date by default.
    incomplete_year = (data_end.month, data_end.day) != (12, 31)
    mode = st.radio(
        "Comparison", MODES, index=1 if incomplete_year else 0, horizontal=True, key="ca_mode"
    )
    if incomplete_year and mode == MODES[0]:
        st.caption(
            f":material/info: The data ends on {data_end.isoformat()}, so the latest year is "
            "incomplete. Year-to-date compares the same part of both years."
        )
    if mode in MODES[:2]:
        options = years or [data_end.year]
        left, right, extra = st.columns([1, 1, 2])
        current_year = right.selectbox(
            "Current year", options, index=len(options) - 1, key="ca_cur_year"
        )
        previous_options = [y for y in options if y < current_year] or [current_year - 1]
        previous_year = left.selectbox(
            "Previous year", previous_options, index=len(previous_options) - 1,
            key="ca_prev_year",
        )  # fmt: skip
        through = None
        if mode == "Year-to-date":
            through = extra.date_input(
                "Through (month and day)", value=data_end, key="ca_through",
                help="Both years are cut at this month and day so they cover the same part "
                "of the year.",
            )  # fmt: skip
        previous, current = year_period(previous_year, through), year_period(current_year, through)
    else:
        left, right = st.columns(2)
        default_prev = (date(data_end.year - 1, 1, 1), date(data_end.year - 1, 12, 31))
        default_cur = (date(data_end.year, 1, 1), data_end)
        prev_range = _range(
            left.date_input("Previous period", value=default_prev, key="ca_prev_range")
        )
        cur_range = _range(
            right.date_input("Current period", value=default_cur, key="ca_cur_range")
        )
        if prev_range is None or cur_range is None:
            st.info("Pick a start and an end date for both periods.")
            return None
        previous, current = custom_period(*prev_range), custom_period(*cur_range)
    problem = period_problem(previous, current)
    if problem:
        st.error(problem, icon=":material/event_busy:")
        return None
    if previous["start"] < data_start.isoformat():
        st.caption(
            f":material/info: The data starts on {data_start.isoformat()}; earlier dates have "
            "no rows."
        )
    return previous, current
