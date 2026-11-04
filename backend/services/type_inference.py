"""Conservative column type inference for text-loaded tabular data.

Design rules (documented in docs/ARCHITECTURE.md §5.1):

* Values are trimmed; blank strings become missing.
* Integer-like values with a leading zero (``00123``) or with 16+ digits (e.g. NIK) are
  identifiers and stay text, so no digit is ever lost.
* A column becomes numeric/date only if at least ``CONVERSION_THRESHOLD`` of its
  non-missing values parse. Values that fail are set to missing and reported in a
  note with examples. Nothing is changed silently.
* Numbers follow the import's ``number_format``. In ``auto`` mode (CSV) one style per
  column is detected; ``1.234`` (dot before exactly 3 digits) is read as Indonesian
  thousands and flagged as ambiguous. ``plain`` is for Excel, whose numeric cells are
  already canonical.
* Slash/dot/dash dates follow ``date_order``. In ``auto`` mode: day-first if any first
  part exceeds 12, month-first if any second part exceeds 12, and day-first (flagged as
  ambiguous) otherwise. Target users are in Indonesia.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Literal

import pandas as pd

from backend.schemas.datasets import ColumnNote, DateOrder, NumberFormat

LogicalType = Literal["integer", "numeric", "date", "datetime", "boolean", "text"]

CONVERSION_THRESHOLD = 0.95
MIXED_TYPE_THRESHOLD = 0.5
_MAX_EXAMPLES = 3
_EXAMPLE_CHARS = 30

_LEADING_ZERO = re.compile(r"^0\d+$")
_LONG_DIGITS = re.compile(r"^[+-]?\d{16,}$")
_SCIENTIFIC = re.compile(r"^[+-]?\d(\.\d+)?[eE]\+?\d+$")
_CURRENCY = re.compile(r"^(rp\.?|idr|usd|\$)\s*", re.IGNORECASE)
_INTEGRAL = re.compile(r"^[+-]?\d+(\.0+)?$")
# A thousands group never starts with 0 ("0.125" is a decimal, not 125).
_DOT_THOUSANDS = re.compile(r"^[+-]?[1-9]\d{0,2}(\.\d{3})+(,\d+)?$")
_COMMA_THOUSANDS = re.compile(r"^[+-]?[1-9]\d{0,2}(,\d{3})+(\.\d+)?$")
_ID_STYLE, _EN_STYLE, _PLAIN_STYLE = (".", ","), (",", "."), ("", ".")
# Full-value pattern per (thousands, decimal) style. Values written in another style
# are rejected and reported instead of being rewritten (e.g. "1.5" under "1.234,5").
_STYLE_PATTERNS: dict[tuple[str, str], re.Pattern[str]] = {
    _ID_STYLE: re.compile(r"^[+-]?([1-9]\d{0,2}(\.\d{3})+|\d+)(,\d+)?$"),
    _EN_STYLE: re.compile(r"^[+-]?([1-9]\d{0,2}(,\d{3})+|\d+)(\.\d+)?$"),
    _PLAIN_STYLE: re.compile(r"^[+-]?\d+(\.\d+)?([eE][+-]?\d+)?$"),
}
_EXPLICIT_STYLES = {"id": _ID_STYLE, "en": _EN_STYLE, "plain": _PLAIN_STYLE}
_INT64_LIMIT = 2**63
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}(:\d{2}(\.\d+)?)?)?$")
_PARTS_DATE = re.compile(r"^(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})$")
_TRUE = {"true", "yes", "ya"}
_FALSE = {"false", "no", "tidak"}
_TYPE_WORDS = {"integer": "whole numbers", "numeric": "numbers", "date": "dates",
               "datetime": "dates", "boolean": "yes/no values"}  # fmt: skip


@dataclass
class InferenceResult:
    """Converted frame, logical types, unconverted-value counts, and notes per column."""

    dataframe: pd.DataFrame
    types: dict[str, LogicalType] = field(default_factory=dict)
    failed_counts: dict[str, int] = field(default_factory=dict)
    notes: dict[str, list[ColumnNote]] = field(default_factory=dict)


@dataclass
class _Attempt:
    """Outcome of trying one type on a column."""

    series: pd.Series  # converted values; NA where a value did not parse
    logical: LogicalType
    ratio: float  # share of non-missing values that parsed
    notes: list[ColumnNote] = field(default_factory=list)


def _quote(value: object) -> str:
    text = str(value)
    return f"'{text[:_EXAMPLE_CHARS]}…'" if len(text) > _EXAMPLE_CHARS else f"'{text}'"


def _examples(values: pd.Series) -> str:
    return ", ".join(_quote(v) for v in values.drop_duplicates().head(_MAX_EXAMPLES))


def _clean_text(series: pd.Series) -> pd.Series:
    text = series.astype("string").str.strip()
    return text.mask(text == "")


# ------------------------------------------------------------------------- numbers
def _detect_style(values: pd.Series) -> tuple[tuple[str, str], str | None]:
    """Pick the column's number style; also return an example when it is a guess."""
    has_dot = values.str.contains(".", regex=False)
    has_comma = values.str.contains(",", regex=False)
    if (has_dot & has_comma).any():
        both = values[has_dot & has_comma]
        comma_last = (both.str.rfind(",") > both.str.rfind(".")).mean() >= 0.5
        return (_ID_STYLE if comma_last else _EN_STYLE), None
    if has_comma.any():
        commas = values[has_comma]
        if commas.str.match(_COMMA_THOUSANDS).all():
            # "1,234" could also be the Indonesian decimal 1,234 unless a value has 2+ groups.
            ambiguous = commas.str.count(",").max() == 1
            return _EN_STYLE, (commas.iloc[0] if ambiguous else None)
        return _ID_STYLE, None
    if has_dot.any():
        dots = values[has_dot]
        if dots.str.match(_DOT_THOUSANDS).all():
            # "1.234" could also be the decimal 1.234 unless a value has 2+ groups.
            ambiguous = dots.str.count(r"\.").max() == 1
            return _ID_STYLE, (dots.iloc[0] if ambiguous else None)
    return _EN_STYLE, None


def _to_number(text: str, style: tuple[str, str]) -> str:
    thousands, decimal = style
    if thousands:
        text = text.replace(thousands, "")
    return text.replace(",", ".") if decimal == "," else text


def _try_numeric(text: pd.Series, number_format: NumberFormat) -> _Attempt | None:
    values = text.dropna()
    if values.str.match(_LEADING_ZERO).any() or values.str.match(_LONG_DIGITS).any():
        return None  # identifiers: handled by _identifier_notes
    stripped = values.str.replace(_CURRENCY, "", regex=True).str.replace(" ", "", regex=False)
    if number_format == "auto":
        style, ambiguous_example = _detect_style(stripped)
    else:
        style, ambiguous_example = _EXPLICIT_STYLES[number_format], None
    valid = stripped.str.fullmatch(_STYLE_PATTERNS[style]).fillna(False).astype(bool)
    ratio = float(valid.mean())
    canonical = stripped[valid].map(lambda v: _to_number(v, style))

    if len(canonical) and canonical.str.fullmatch(_INTEGRAL).all():
        integers = [int(v.split(".")[0]) for v in canonical]
        if any(abs(i) >= _INT64_LIMIT for i in integers):
            return None
        series = pd.Series(pd.NA, index=text.index, dtype="Int64")
        series.loc[canonical.index] = integers
        logical: LogicalType = "integer"
    else:
        series = pd.Series(pd.NA, index=text.index, dtype="Float64")
        series.loc[canonical.index] = pd.to_numeric(canonical).astype("float64")
        logical = "numeric"

    notes: list[ColumnNote] = []
    if ambiguous_example is not None:
        read_as = _to_number(ambiguous_example, style)
        if style == _ID_STYLE:
            meaning = "a thousands separator (Indonesian format)"
            fix = "If they are decimals, re-import with the English number format (1,234.5)."
        else:
            meaning = "a thousands separator (English format)"
            fix = "If they are decimals, re-import with the Indonesian number format (1.234,5)."
        notes.append(
            ColumnNote(
                level="warning",
                code="ambiguous_number_format",
                message=f"Numbers like {_quote(ambiguous_example)} were read as {read_as}: "
                f"the separator was treated as {meaning}. {fix}",
            )
        )
    return _Attempt(series, logical, ratio, notes)


# --------------------------------------------------------------------------- dates
def _format_day(value: date) -> str:
    return f"{value.day} {value.strftime('%B')} {value.year}"


def _try_date(text: pd.Series, date_order: DateOrder) -> _Attempt | None:
    values = text.dropna()
    notes: list[ColumnNote] = []
    if values.str.match(_ISO_DATE).mean() >= MIXED_TYPE_THRESHOLD:
        parsed = pd.to_datetime(values, format="ISO8601", errors="coerce")
    else:
        parts = values.str.extract(_PARTS_DATE).dropna()
        if len(parts) < MIXED_TYPE_THRESHOLD * len(values):
            return None
        first, second = parts[0].astype(int), parts[1].astype(int)
        if date_order == "day_first":
            day_first = True
        elif date_order == "month_first":
            day_first = False
        else:
            day_first = bool((first > 12).any() or not (second > 12).any())
        normalized = values.str.replace(r"[.-]", "/", regex=True)
        fmt = "%d/%m/%Y" if day_first else "%m/%d/%Y"
        parsed = pd.to_datetime(normalized, format=fmt, errors="coerce")
        if date_order == "auto" and not (first > 12).any() and not (second > 12).any():
            example = values[parsed.notna()].iloc[0] if parsed.notna().any() else None
            if example is not None:
                read_as = _format_day(parsed[parsed.notna()].iloc[0].date())
                notes.append(
                    ColumnNote(
                        level="warning",
                        code="ambiguous_date_order",
                        message=f"Dates like {_quote(example)} could be day/month or "
                        f"month/day; they were read as day/month ({read_as}). If your "
                        "file uses month/day, re-import with the month/day date order.",
                    )
                )

    series = pd.Series(pd.NaT, index=text.index, dtype="datetime64[us]")
    series.loc[parsed.index] = parsed
    valid = series.dropna()
    has_time = bool((valid != valid.dt.normalize()).any())
    return _Attempt(series, "datetime" if has_time else "date", float(parsed.notna().mean()), notes)


def _try_boolean(text: pd.Series) -> _Attempt | None:
    values = text.dropna().str.lower()
    if not set(values) <= (_TRUE | _FALSE):
        return None
    series = text.str.lower().map(lambda v: None if pd.isna(v) else v in _TRUE)
    return _Attempt(series.astype("boolean"), "boolean", 1.0)


# --------------------------------------------------------------------- identifiers
def _identifier_notes(text: pd.Series, number_format: NumberFormat) -> list[ColumnNote]:
    values = text.dropna()
    notes: list[ColumnNote] = []
    long_values = values[values.str.match(_LONG_DIGITS)]
    if len(long_values):
        if number_format == "plain":
            notes.append(
                ColumnNote(
                    level="warning",
                    code="long_identifier",
                    message=f"{len(long_values):,} value(s) have 16 or more digits (e.g. "
                    f"{_examples(long_values)}). They are kept as text. Excel keeps only 15 "
                    "significant digits for numbers: if these were typed as numbers in "
                    "Excel, the last digits may already be wrong in the file and cannot be "
                    "recovered. Format ID columns as Text in Excel to keep every digit.",
                )
            )
        else:
            notes.append(
                ColumnNote(
                    level="info",
                    code="long_identifier",
                    message=f"{len(long_values):,} value(s) have 16 or more digits (e.g. "
                    f"{_examples(long_values)}). They are kept as text so no digit is lost.",
                )
            )
    scientific = values[values.str.match(_SCIENTIFIC)]
    if len(scientific):
        notes.append(
            ColumnNote(
                level="warning",
                code="scientific_notation",
                message=f"{len(scientific):,} value(s) are in scientific notation (e.g. "
                f"{_examples(scientific)}), usually because Excel displayed a long number. "
                "If they are IDs, the original digits were lost before the file was saved "
                "and cannot be recovered. Re-export the source with the column formatted as Text.",
            )
        )
    return notes


# ---------------------------------------------------------------------------- main
def _infer_column(
    text: pd.Series, number_format: NumberFormat, date_order: DateOrder
) -> tuple[pd.Series, LogicalType, int, list[ColumnNote]]:
    notes = _identifier_notes(text, number_format)
    if text.notna().sum() == 0:
        return text, "text", 0, notes

    candidates = [
        a
        for a in (
            _try_boolean(text),
            _try_numeric(text, number_format),
            _try_date(text, date_order),
        )
        if a is not None
    ]
    accepted = next((a for a in candidates if a.ratio >= CONVERSION_THRESHOLD), None)
    if accepted is None:
        best = max(candidates, key=lambda a: a.ratio, default=None)
        if best is not None and best.ratio >= MIXED_TYPE_THRESHOLD:
            bad = text[text.notna() & best.series.isna()]
            notes.append(
                ColumnNote(
                    level="warning",
                    code="mixed_types",
                    message=f"{best.ratio:.0%} of values look like {_TYPE_WORDS[best.logical]}, "
                    f"but others do not (e.g. {_examples(bad)}), so the column was kept as "
                    "text. Clean those values in the source to use it in calculations.",
                )
            )
        return text, "text", 0, notes

    notes.extend(accepted.notes)
    bad = text[text.notna() & accepted.series.isna()]
    if len(bad):
        notes.append(
            ColumnNote(
                level="warning",
                code="unconverted_values",
                message=f"{len(bad):,} value(s) could not be read as "
                f"{_TYPE_WORDS[accepted.logical]} and are stored as empty (e.g. {_examples(bad)}).",
            )
        )
    return accepted.series, accepted.logical, len(bad), notes


def infer_types(
    df: pd.DataFrame,
    number_format: NumberFormat = "auto",
    date_order: DateOrder = "auto",
) -> InferenceResult:
    """Infer and apply a logical type for every column.

    Args:
        df: Frame whose values are strings (or missing), as produced by the loader.
            Column labels must be unique.
        number_format: ``auto`` (detect per column; CSV default), ``id`` (1.234,5),
            ``en`` (1,234.5), or ``plain`` (canonical numbers, used for Excel cells).
        date_order: ``auto``, ``day_first``, or ``month_first`` for d/m/y-style dates.

    Returns:
        InferenceResult with converted columns, logical types, unconverted-value
        counts, and user-facing notes per column.
    """
    out = pd.DataFrame(index=df.index)
    result = InferenceResult(dataframe=out)
    for column in df.columns:
        series, logical, failed, notes = _infer_column(
            _clean_text(df[column]), number_format, date_order
        )
        out[column], result.types[column] = series, logical
        if failed:
            result.failed_counts[column] = failed
        if notes:
            result.notes[column] = notes
    return result
