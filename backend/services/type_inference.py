"""Conservative column type inference for text-loaded tabular data.

Design rules (documented in docs/ARCHITECTURE.md):

* Values are trimmed; blank strings become missing.
* Integer-like values with a leading zero (``00123``) are IDs and stay text.
* A column becomes numeric/date only if at least ``CONVERSION_THRESHOLD`` of its
  non-missing values parse. Values that fail are set to missing and *counted*
  in ``failed_counts`` so the UI can report them instead of hiding them.
* Integer-like values with 16+ digits (e.g. NIK) stay text: float64 cannot hold them.
* CSV numbers: when every dot separates 3-digit groups (``1.234``, ``1.000.000``) the
  dots are thousands separators (Indonesian convention); otherwise the dot is decimal.
  Values written in a different style than the column's are rejected and counted,
  never rewritten. Excel numbers (``locale_numbers=False``) are already canonical.
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
# 16+ digit integers (e.g. Indonesian NIK) exceed float64 precision: treat as IDs.
_LONG_DIGITS = re.compile(r"^[+-]?\d{16,}$")
_CURRENCY = re.compile(r"^(rp\.?|idr|usd|\$)\s*", re.IGNORECASE)
_INTEGRAL = re.compile(r"^[+-]?\d+(\.0+)?$")
# A thousands group never starts with 0 ("0.125" is a decimal, not 125).
_DOT_THOUSANDS = re.compile(r"^[+-]?[1-9]\d{0,2}(\.\d{3})+(,\d+)?$")
_COMMA_THOUSANDS = re.compile(r"^[+-]?[1-9]\d{0,2}(,\d{3})+(\.\d+)?$")
# Full-value pattern per (thousands, decimal) style. Values written in another style
# are rejected and counted instead of being rewritten (e.g. "1.5" under "1.234,5").
_STYLE_PATTERNS: dict[tuple[str, str], re.Pattern[str]] = {
    (".", ","): re.compile(r"^[+-]?([1-9]\d{0,2}(\.\d{3})+|\d+)(,\d+)?$"),
    (",", "."): re.compile(r"^[+-]?([1-9]\d{0,2}(,\d{3})+|\d+)(\.\d+)?$"),
    ("", "."): re.compile(r"^[+-]?\d+(\.\d+)?([eE][+-]?\d+)?$"),
}
_INT64_LIMIT = 2**63
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


def _try_numeric(text: pd.Series, locale_numbers: bool) -> tuple[pd.Series, LogicalType] | None:
    values = text.dropna()
    if values.empty:
        return None
    if values.str.match(_LEADING_ZERO).any() or values.str.match(_LONG_DIGITS).any():
        return None  # ID-like
    stripped = values.str.replace(_CURRENCY, "", regex=True).str.replace(" ", "", regex=False)
    style = _numeric_style(stripped) if locale_numbers else ("", ".")
    valid = stripped.str.fullmatch(_STYLE_PATTERNS[style]).fillna(False).astype(bool)
    if valid.mean() < CONVERSION_THRESHOLD:
        return None

    thousands, decimal = style
    canonical = stripped[valid]
    if thousands:
        canonical = canonical.str.replace(thousands, "", regex=False)
    if decimal == ",":
        canonical = canonical.str.replace(",", ".", regex=False)

    if canonical.str.fullmatch(_INTEGRAL).all():
        # Exact integer conversion; going through float64 would lose precision.
        integers = [int(v.split(".")[0]) for v in canonical]
        if any(abs(i) >= _INT64_LIMIT for i in integers):
            return None
        full = pd.Series(pd.NA, index=text.index, dtype="Int64")
        full.loc[canonical.index] = integers
        return full, "integer"
    full = pd.Series(pd.NA, index=text.index, dtype="Float64")
    full.loc[canonical.index] = pd.to_numeric(canonical).astype("float64")
    return full, "numeric"


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


def infer_types(df: pd.DataFrame, locale_numbers: bool = True) -> InferenceResult:
    """Infer and apply a logical type for every column.

    Args:
        df: Frame whose values are strings (or missing), as produced by the loader.
        locale_numbers: Detect ID/EN thousands and decimal separators (CSV). Use False
            for Excel, whose numeric cells are already canonical ("4.125" is 4.125).

    Returns:
        InferenceResult with converted columns, logical types, and the number of
        non-missing values per column that could not be converted.
    """
    out = pd.DataFrame(index=df.index)
    result = InferenceResult(dataframe=out)
    for column in df.columns:
        text = _clean_text(df[column])
        converted: tuple[pd.Series, LogicalType] | None = (
            _try_boolean(text) or _try_numeric(text, locale_numbers) or _try_date(text)
        )
        if converted is None:
            out[column], result.types[column] = text, "text"
            continue
        series, logical = converted
        failed = int(text.notna().sum() - series.notna().sum())
        if failed:
            result.failed_counts[column] = failed
        out[column], result.types[column] = series, logical
    return result
