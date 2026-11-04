"""Convert DuckDB result rows into JSON-safe values."""

from __future__ import annotations

import math
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any


def to_jsonable(value: Any) -> Any:
    """Map one DuckDB value to a JSON-compatible value.

    Dates/times become ISO strings, decimals become floats, NaN/inf become None,
    and anything unknown becomes its string form.
    """
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return None if math.isnan(value) or math.isinf(value) else value
    if isinstance(value, Decimal):
        return to_jsonable(float(value))
    if isinstance(value, (date, datetime, time)):
        return value.isoformat()
    return str(value)


def rows_to_jsonable(rows: list[tuple[Any, ...]]) -> list[list[Any]]:
    """Convert every value of every row with :func:`to_jsonable`."""
    return [[to_jsonable(v) for v in row] for row in rows]
