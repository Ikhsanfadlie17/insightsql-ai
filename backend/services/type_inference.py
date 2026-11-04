"""Conservative column type inference for text-loaded tabular data.

Design rules (documented in docs/ARCHITECTURE.md):

* Values are trimmed; blank strings become missing.
* Integer-like values with a leading zero (``00123``) are IDs and stay text.
* A column becomes numeric/date only if at least ``CONVERSION_THRESHOLD`` of its
  non-missing values parse. Values that fail are set to missing and *counted*
  in ``failed_counts`` so the UI can report them instead of hiding them.
* Numbers whose dots always separate 3-digit groups (``1.234``, ``1.000.000``) use
  dots as thousands separators (Indonesian convention); otherwise the dot is decimal.
* Slash/dot/dash dates are parsed day-first when any first part exceeds 12,
  month-first when any second part exceeds 12, and day-first when ambiguous
  (assumption: target users are in Indonesia).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

import pandas as pd

LogicalType = Literal["integer", "numeric", "date", "datetime", "boolean", "text"]

CONVERSION_THRESHOLD = 0.95

_LEADING_ZERO = re.compile(r"^0\d+$")
_CURRENCY = re.compile(r"^(rp\.?|idr|usd|\$)\s*", re.IGNORECASE)
_INTEGRAL = re.compile(r"^[+-]?\d+(\.0+)?$")
_DOT_THOUSANDS = re.compile(r"^[+-]?\d{1,3}(\.\d{3})+(,\d+)?$")
_COMMA_THOUSANDS = re.compile(r"^[+-]?\d{1,3}(,\d{3})+(\.\d+)?$")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}(:\d{2}(\.\d+)?)?)?$")
_PARTS_DATE = re.compile(r"^(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})$")
_TRUE = {"true", "yes", "ya"}
_FALSE = {"false", "no", "tidak"}


@dataclass
class InferenceResult:
    """Converted frame, the logical type per column, and unparseable value counts."""

    dataframe: pd.DataFrame
    types: dict[str, LogicalType] = field(default_factory=dict)
    failed_counts: dict[str, int] = field(default_factory=dict)


def _clean_text(series: pd.Series) -> pd.Series:
    text = series.astype("string").str.strip()
    return text.mask(text == "")


def _numeric_style(values: pd.Series) -> tuple[str, str]:
    """Return (thousands separator, decimal separator) for a column of number strings."""
    has_dot = values.str.contains(".", regex=False)
    has_comma = values.str.contains(",", regex=False)
    if (has_dot & has_comma).any():
        both = values[has_dot & has_comma]
        comma_last = (both.str.rfind(",") > both.str.rfind(".")).mean() >= 0.5
        return (".", ",") if comma_last else (",", ".")
    if has_comma.any():
        if values[has_comma].str.match(_COMMA_THOUSANDS).all():
            return (",", ".")
        return (".", ",")
    if has_dot.any() and values[has_dot].str.match(_DOT_THOUSANDS).all():
        # Every dot separates a 3-digit group ("1.000.000", "1.234"): thousands separators.
        return (".", ",")
    return (",", ".")


def _try_numeric(text: pd.Series) -> tuple[pd.Series, LogicalType] | None:
    values = text.dropna()
    if values.empty or values.str.match(_LEADING_ZERO).any():
        return None
    stripped = values.str.replace(_CURRENCY, "", regex=True).str.replace(" ", "", regex=False)
    thousands, decimal = _numeric_style(stripped)
    canonical = stripped.str.replace(thousands, "", regex=False)
    if decimal == ",":
        canonical = canonical.str.replace(",", ".", regex=False)
    parsed = pd.to_numeric(canonical, errors="coerce")
    if parsed.notna().mean() < CONVERSION_THRESHOLD:
        return None

    full = pd.Series(pd.NA, index=text.index, dtype="Float64")
    full.loc[parsed.index] = parsed
    is_integer = canonical[parsed.notna()].str.match(_INTEGRAL).all()
    if is_integer:
        return full.round().astype("Int64"), "integer"
    return full.astype("Float64"), "numeric"


def _day_first(values: pd.Series) -> bool | None:
    """Decide the order of d/m parts; None when the values are not part-dates."""
    parts = values.str.extract(_PARTS_DATE)
    matched = parts.dropna()
    if len(matched) < CONVERSION_THRESHOLD * len(values):
        return None
    first, second = matched[0].astype(int), matched[1].astype(int)
    if (first > 12).any():
        return True
    # Month-first only when the second part proves it; ambiguous input is day-first.
    return not (second > 12).any()


def _try_date(text: pd.Series) -> tuple[pd.Series, LogicalType] | None:
    values = text.dropna()
    if values.empty:
        return None
    if values.str.match(_ISO_DATE).mean() >= CONVERSION_THRESHOLD:
        parsed = pd.to_datetime(values, format="ISO8601", errors="coerce")
    else:
        day_first = _day_first(values)
        if day_first is None:
            return None
        normalized = values.str.replace(r"[.-]", "/", regex=True)
        fmt = "%d/%m/%Y" if day_first else "%m/%d/%Y"
        parsed = pd.to_datetime(normalized, format=fmt, errors="coerce")
    if parsed.notna().mean() < CONVERSION_THRESHOLD:
        return None

    full = pd.Series(pd.NaT, index=text.index, dtype="datetime64[us]")
    full.loc[parsed.index] = parsed
    valid = full.dropna()
    has_time = bool((valid != valid.dt.normalize()).any())
    return full, ("datetime" if has_time else "date")


def _try_boolean(text: pd.Series) -> tuple[pd.Series, LogicalType] | None:
    values = text.dropna().str.lower()
    if values.empty or not set(values) <= (_TRUE | _FALSE):
        return None
    full = text.str.lower().map(lambda v: None if pd.isna(v) else v in _TRUE)
    return full.astype("boolean"), "boolean"


def infer_types(df: pd.DataFrame) -> InferenceResult:
    """Infer and apply a logical type for every column.

    Args:
        df: Frame whose values are strings (or missing), as produced by the loader.

    Returns:
        InferenceResult with converted columns, logical types, and the number of
        non-missing values per column that could not be converted.
    """
    out = pd.DataFrame(index=df.index)
    result = InferenceResult(dataframe=out)
    for column in df.columns:
        text = _clean_text(df[column])
        converted: tuple[pd.Series, LogicalType] | None = None
        for attempt in (_try_boolean, _try_numeric, _try_date):
            converted = attempt(text)
            if converted is not None:
                break
        if converted is None:
            out[column], result.types[column] = text, "text"
            continue
        series, logical = converted
        failed = int(text.notna().sum() - series.notna().sum())
        if failed:
            result.failed_counts[column] = failed
        out[column], result.types[column] = series, logical
    return result
