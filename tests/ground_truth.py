"""Independent reference implementation of the client-retention metrics (pandas + sets).

Used only by tests to check the SQL templates in ``analytics_service``. It shares no
code with the service: it loads the raw table and recomputes every metric with plain
Python sets, following the definitions in ARCHITECTURE §8 literally.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import duckdb
import pandas as pd


@dataclass
class Truth:
    previous_active: int
    current_active: int
    returning: int
    new: int
    reactivated: int
    between_period: int
    not_returned: int
    existing_active: int
    transactions_previous: int
    transactions_current: int
    clients_with_repeat_previous: int
    clients_with_repeat_current: int
    excluded_missing_id_rows: int
    excluded_missing_date_rows: int
    duplicate_rows_excluded: int
    retention_rate: float | None
    new_client_rate: float | None
    growth_rate: float | None
    categories: dict[str, set[str]] = field(default_factory=dict)


def _key(value: object, by_name: bool) -> str | None:
    if value is None or (isinstance(value, float) and pd.isna(value)) or value is pd.NA:
        return None
    text = str(value)
    text = re.sub(r"\s+", " ", text).strip().lower() if by_name else text.strip()
    return text or None


def load(duckdb_path: Path) -> pd.DataFrame:
    with duckdb.connect(str(duckdb_path), read_only=True) as conn:
        return conn.execute("SELECT * FROM dataset").df()


def _day(value: object) -> date | None:
    if value is None or pd.isna(value):
        return None
    return pd.Timestamp(value).date()


def _ratio(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else numerator / denominator


def compute(
    frame: pd.DataFrame,
    identity: str,
    date_column: str,
    previous: tuple[date, date],
    current: tuple[date, date],
    *,
    by_name: bool = False,
    dedupe: bool = True,
) -> Truth:
    """``previous`` and ``current`` are ``[start, end)`` date pairs."""
    raw = [
        (_key(k, by_name), _day(d))
        for k, d in zip(frame[identity], frame[date_column], strict=True)
    ]
    rows = frame.drop_duplicates() if dedupe else frame
    tx = [
        (_key(k, by_name), _day(d)) for k, d in zip(rows[identity], rows[date_column], strict=True)
    ]

    def inside(day: date | None, period: tuple[date, date]) -> bool:
        return day is not None and period[0] <= day < period[1]

    def in_any(day: date | None) -> bool:
        return inside(day, previous) or inside(day, current)

    valid = [(k, d) for k, d in tx if k is not None and d is not None]
    first_seen: dict[str, date] = {}
    for k, d in valid:
        first_seen[k] = min(d, first_seen.get(k, d))

    prev_tx = [(k, d) for k, d in valid if inside(d, previous)]
    cur_tx = [(k, d) for k, d in valid if inside(d, current)]
    prev = {k for k, _ in prev_tx}
    cur = {k for k, _ in cur_tx}

    returning = prev & cur
    new = {k for k in cur if first_seen[k] >= current[0]}
    not_in_prev = cur - prev
    reactivated = {k for k in not_in_prev if first_seen[k] < previous[0]}
    between = {k for k in not_in_prev if previous[1] <= first_seen[k] < current[0]}
    not_returned = prev - cur
    existing = {k for k in cur if first_seen[k] < current[0]}

    def repeaters(items: list[tuple[str, date]]) -> int:
        counts: dict[str, int] = {}
        for k, _ in items:
            counts[k] = counts.get(k, 0) + 1
        return sum(1 for n in counts.values() if n >= 2)

    raw_in_periods_with_key = sum(1 for k, d in raw if k is not None and in_any(d))
    return Truth(
        previous_active=len(prev),
        current_active=len(cur),
        returning=len(returning),
        new=len(new),
        reactivated=len(reactivated),
        between_period=len(between),
        not_returned=len(not_returned),
        existing_active=len(existing),
        transactions_previous=len(prev_tx),
        transactions_current=len(cur_tx),
        clients_with_repeat_previous=repeaters(prev_tx),
        clients_with_repeat_current=repeaters(cur_tx),
        excluded_missing_id_rows=sum(1 for k, d in raw if k is None and in_any(d)),
        excluded_missing_date_rows=sum(1 for k, d in raw if k is not None and d is None),
        duplicate_rows_excluded=raw_in_periods_with_key - len(prev_tx) - len(cur_tx),
        retention_rate=_ratio(len(returning), len(prev)),
        new_client_rate=_ratio(len(new), len(cur)),
        growth_rate=_ratio(len(cur) - len(prev), len(prev)),
        categories={
            "new": new,
            "returning": returning,
            "reactivated": reactivated,
            "between_period": between,
            "not_returned": not_returned,
            "existing_active": existing,
        },
    )


def monthly_trend(
    frame: pd.DataFrame, identity: str, date_column: str, *, dedupe: bool = True
) -> dict[str, tuple[int, int, int]]:
    """``{"YYYY-MM": (active_clients, new_clients, transactions)}``."""
    rows = frame.drop_duplicates() if dedupe else frame
    valid = [
        (k, d)
        for k, d in (
            (_key(k, False), _day(d))
            for k, d in zip(rows[identity], rows[date_column], strict=True)
        )
        if k is not None and d is not None
    ]
    first_seen: dict[str, date] = {}
    for k, d in valid:
        first_seen[k] = min(d, first_seen.get(k, d))
    out: dict[str, tuple[set[str], set[str], int]] = {}
    for k, d in valid:
        bucket = f"{d.year:04d}-{d.month:02d}"
        active, new, count = out.get(bucket, (set(), set(), 0))
        active.add(k)
        if (first_seen[k].year, first_seen[k].month) == (d.year, d.month):
            new.add(k)
        out[bucket] = (active, new, count + 1)
    return {b: (len(a), len(n), c) for b, (a, n, c) in out.items()}
