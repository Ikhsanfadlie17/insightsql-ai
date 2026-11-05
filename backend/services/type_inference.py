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

_LEADING_ZERO = re.compile(r"^[+-]?0\d+$")
_LONG_DIGITS = re.compile(r"^[+-]?\d{16,}$")
_SCIENTIFIC = re.compile(r"^[+-]?\d(\.\d+)?[eE]\+?\d+$")
_CURRENCY = re.compile(r"^(?P<cur>rp\.?|idr|us\$|usd|\$)\s*", re.IGNORECASE)
_CURRENCY_CODES = {"rp": "IDR", "rp.": "IDR", "idr": "IDR", "usd": "USD", "us$": "USD", "$": "USD"}
_DASH_SUFFIX = re.compile(r"[,.]-+$")  # "Rp 1.500.000,-" (no cents)
_SPACE_GROUPS = re.compile(r"^[+-]?\d{1,3}( \d{3})+([.,]\d+)?$")  # "1 000 000"
_INTEGRAL = re.compile(r"^[+-]?\d+(\.0+)?$")
# A thousands group never starts with 0 ("0.125" is a decimal, not 125).
_DOT_THOUSANDS = re.compile(r"^[+-]?[1-9]\d{0,2}(\.\d{3})+(,\d+)?$")
_COMMA_THOUSANDS = re.compile(r"^[+-]?[1-9]\d{0,2}(,\d{3})+(\.\d+)?$")
_DOT_GROUPS_ONLY = re.compile(r"^[+-]?[1-9]\d{0,2}(\.\d{3})+$")
_COMMA_GROUPS_ONLY = re.compile(r"^[+-]?[1-9]\d{0,2}(,\d{3})+$")
_ID_STYLE, _EN_STYLE, _PLAIN_STYLE = (".", ","), (",", "."), ("", ".")
# Full-value pattern per (thousands, decimal) style. Values written in another style
# are rejected and reported instead of being rewritten (e.g. "1.5" under "1.234,5").
_STYLE_PATTERNS: dict[tuple[str, str], re.Pattern[str]] = {
    _ID_STYLE: re.compile(r"^[+-]?([1-9]\d{0,2}(\.\d{3})+|\d+)(,\d+)?$"),
    _EN_STYLE: re.compile(r"^[+-]?([1-9]\d{0,2}(,\d{3})+|\d+)(\.\d+)?$"),
    # Excel cells: canonical numbers. Only negative exponents ("1e-05" for tiny floats);
    # "9.17E+15" / "1E5" in an Excel sheet are text cells (codes or already-rounded IDs).
    _PLAIN_STYLE: re.compile(r"^[+-]?\d+(\.\d+)?([eE]-\d+)?$"),
}
_EXPLICIT_STYLES = {"id": _ID_STYLE, "en": _EN_STYLE, "plain": _PLAIN_STYLE}
# Header words of identifier columns (IDs, codes, phone numbers): never converted to numbers.
_IDENTIFIER_WORDS = {
    "id", "kode", "code", "no", "nomor", "nomer", "nik", "npwp", "nip", "nis", "telepon",
    "telp", "phone", "hp", "handphone", "whatsapp", "wa", "rekening", "sku", "kodepos",
    "zip", "postal",
}  # fmt: skip
_INT64_LIMIT = 2**63
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}(:\d{2}(\.\d+)?)?)?$")
_PARTS_DATE = re.compile(r"^(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})$")
# Dates written with a month name (Google Sheets / Excel display formats): 22/Jan/2025,
# 22 Dec 2025, 10-Feb-2025, 17 Agustus 2025, Jan 22, 2025. Month names are unambiguous,
# so no day/month order question arises.
_DAY_MONTH_NAME = re.compile(r"^(\d{1,2})[\s/.-]+([A-Za-z]{3,9})\.?[\s/.,-]+(\d{4})$")
_MONTH_NAME_DAY = re.compile(r"^([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})$")
_MONTHS = {
    name: number
    for number, names in enumerate(
        [
            ("jan", "januari", "january"),
            ("feb", "februari", "pebruari", "february", "peb"),
            ("mar", "maret", "march"),
            ("apr", "april"),
            ("may", "mei"),
            ("jun", "juni", "june"),
            ("jul", "juli", "july"),
            ("aug", "agu", "agt", "agustus", "august", "ags"),
            ("sep", "sept", "september"),
            ("oct", "okt", "oktober", "october"),
            ("nov", "nop", "november", "nopember"),
            ("dec", "des", "desember", "december"),
        ],
        start=1,
    )
    for name in names
}
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


def _strip_decorations(values: pd.Series) -> pd.Series:
    """Remove a currency prefix, a ",-" suffix, and spaces that separate 3-digit groups.

    Spaces anywhere else are kept, so "1 2" or "0812 3456" never become numbers.
    """
    stripped = values.str.replace(_CURRENCY, "", regex=True).str.replace(
        _DASH_SUFFIX, "", regex=True
    )
    grouped = stripped.str.fullmatch(_SPACE_GROUPS).fillna(False).astype(bool)
    return stripped.where(~grouped, stripped.str.replace(" ", "", regex=False))


def _currencies(values: pd.Series) -> set[str]:
    found = values.str.extract(_CURRENCY)["cur"].dropna().str.lower()
    return {_CURRENCY_CODES[c] for c in found}


def _looks_like_identifier(values: pd.Series) -> bool:
    """Leading zeros or 16+ digits, also when written with spaces (phones, NIK)."""
    compact = values.str.replace(" ", "", regex=False)
    return bool(compact.str.match(_LEADING_ZERO).any() or compact.str.match(_LONG_DIGITS).any())


def _numeric_blockers(text: pd.Series, number_format: NumberFormat) -> list[ColumnNote]:
    """Reasons a column must not be converted to numbers even if values parse."""
    values = text.dropna()
    notes: list[ColumnNote] = []
    currencies = _currencies(values)
    if len(currencies) > 1:
        notes.append(
            ColumnNote(
                level="warning",
                code="mixed_currency",
                message=f"Values use more than one currency ({', '.join(sorted(currencies))}), "
                "so the column was kept as text: adding them up would mix currencies.",
            )
        )
    if number_format == "auto":
        stripped = _strip_decorations(values)
        dots = stripped[stripped.str.fullmatch(_DOT_GROUPS_ONLY).fillna(False).astype(bool)]
        commas = stripped[stripped.str.fullmatch(_COMMA_GROUPS_ONLY).fillna(False).astype(bool)]
        if len(dots) and len(commas):
            notes.append(
                ColumnNote(
                    level="warning",
                    code="mixed_number_format",
                    message=f"Values such as {_quote(commas.iloc[0])} and {_quote(dots.iloc[0])} "
                    "use different thousands separators, so a value like "
                    f"{_quote(dots.iloc[0])} could mean "
                    f"{float(_to_number(dots.iloc[0], _EN_STYLE)):g} or "
                    f"{float(_to_number(dots.iloc[0], _ID_STYLE)):g}. The column was kept as text. "
                    "Re-import choosing the Indonesian or English number format to convert it.",
                )
            )
    return notes


def _try_numeric(text: pd.Series, number_format: NumberFormat) -> _Attempt | None:
    values = text.dropna()
    if _looks_like_identifier(values):
        return None  # identifiers: handled by _identifier_notes
    stripped = _strip_decorations(values)
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
    currencies = _currencies(values)
    if currencies:
        notes.append(
            ColumnNote(
                level="info",
                code="currency_removed",
                message=f"The currency symbol was removed; values are amounts in "
                f"{next(iter(currencies))}.",
            )
        )
    return _Attempt(series, logical, ratio, notes)


# --------------------------------------------------------------------------- dates
def _format_day(value: date) -> str:
    return f"{value.day} {value.strftime('%B')} {value.year}"


def _named_month_dates(values: pd.Series) -> pd.Series | None:
    """Parse dates written with a month name; None when they are not that kind of column.
    Unknown month names (or impossible dates) become NaT and are reported by the caller."""
    day_first = values.str.extract(_DAY_MONTH_NAME)
    month_first = values.str.extract(_MONTH_NAME_DAY)
    if day_first.notna().all(axis=1).mean() >= MIXED_TYPE_THRESHOLD:
        day, month, year = day_first[0], day_first[1], day_first[2]
    elif month_first.notna().all(axis=1).mean() >= MIXED_TYPE_THRESHOLD:
        month, day, year = month_first[0], month_first[1], month_first[2]
    else:
        return None
    month_number = month.str.lower().map(_MONTHS)
    iso = (
        year
        + "-"
        + month_number.astype("Int64").astype("string").str.zfill(2)
        + "-"
        + day.str.zfill(2)
    )
    return pd.to_datetime(iso, format="%Y-%m-%d", errors="coerce")


def _try_date(text: pd.Series, date_order: DateOrder) -> _Attempt | None:
    values = text.dropna()
    notes: list[ColumnNote] = []
    named = _named_month_dates(values)
    if named is not None:
        parsed = named
    elif values.str.match(_ISO_DATE).mean() >= MIXED_TYPE_THRESHOLD:
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
        if date_order == "auto" and (first > 12).any() and (second > 12).any():
            day_example = values[parts.index[first > 12][0]]
            month_example = values[parts.index[second > 12][0]]
            notes.append(
                ColumnNote(
                    level="warning",
                    code="mixed_date_order",
                    message=f"This column contains both day/month dates (e.g. "
                    f"{_quote(day_example)}) and month/day dates (e.g. {_quote(month_example)}). "
                    "They were read as day/month, so ambiguous dates such as 03/04 may be "
                    "wrong and month/day dates could not be read. Fix the source or "
                    "re-import choosing the date order.",
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
    compact = values.str.replace(" ", "", regex=False)
    long_values = values[compact.str.match(_LONG_DIGITS)]
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
                    level="warning",
                    code="long_identifier",
                    message=f"{len(long_values):,} value(s) have 16 or more digits (e.g. "
                    f"{_examples(long_values)}). They are kept as text, so no digit is lost "
                    "during import. If this file was exported from Excel with the column "
                    "formatted as a number, digits after the 15th may already have been "
                    "changed before export and cannot be recovered.",
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
def _is_identifier_header(name: str) -> bool:
    return bool(set(re.split(r"[^a-z0-9]+", name.lower())) & _IDENTIFIER_WORDS)


_KEPT_DATE = ColumnNote(
    level="warning",
    code="kept_as_text_looks_like_date",
    message='The values look like dates, but the column was chosen under "Keep as text" at '
    "import, so date filters, periods, and trends cannot use it. To use it as a date, "
    "import the file again without choosing this column.",
)
_KEPT_NUMBER = ColumnNote(
    level="info",
    code="kept_as_text_looks_like_number",
    message='The values look like numbers and were kept as text ("Keep as text" at import). '
    "That is right for IDs, codes, and phone numbers. If this is an amount, import the "
    "file again without choosing this column so it can be summed.",
)
# Type the values would have had without "Keep as text" -> the note that says so.
_LOOKS_LIKE: dict[str, ColumnNote] = {
    "date": _KEPT_DATE,
    "datetime": _KEPT_DATE,
    "integer": _KEPT_NUMBER,
    "numeric": _KEPT_NUMBER,
}


def _infer_column(
    name: str,
    text: pd.Series,
    number_format: NumberFormat,
    date_order: DateOrder,
    force_text: bool,
) -> tuple[pd.Series, LogicalType, int, list[ColumnNote]]:
    notes = _identifier_notes(text, number_format)
    if force_text:
        notes.append(
            ColumnNote(
                level="info",
                code="kept_as_text_by_user",
                message="Kept as text, as chosen at import.",
            )
        )
        _, would_be, _, _ = _infer_column(name, text, number_format, date_order, False)
        if would_be in _LOOKS_LIKE:
            notes.append(_LOOKS_LIKE[would_be])
        return text, "text", 0, notes
    if text.notna().sum() == 0:
        return text, "text", 0, notes

    blockers = _numeric_blockers(text, number_format)
    numeric = None if blockers else _try_numeric(text, number_format)
    notes.extend(blockers)
    if (
        numeric is not None
        and numeric.ratio >= CONVERSION_THRESHOLD
        and _is_identifier_header(name)
    ):
        # IDs and codes are never numbers: converting would drop non-numeric IDs
        # (e.g. "KL-77" among "1001", "1002") and invite meaningless sums.
        notes.append(
            ColumnNote(
                level="info",
                code="identifier_kept_as_text",
                message="The values look like numbers, but the header names an identifier "
                "(ID, code, number, phone), so the column was kept as text.",
            )
        )
        numeric = None
    candidates = [
        a for a in (_try_boolean(text), numeric, _try_date(text, date_order)) if a is not None
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
                    "text. If it is an identifier (such as an ID or code), text is the right "
                    "type. Otherwise, clean those values in the source to use the column in "
                    "calculations.",
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
    text_columns: set[str] | None = None,
) -> InferenceResult:
    """Infer and apply a logical type for every column.

    Args:
        df: Frame whose values are strings (or missing), as produced by the loader.
            Column labels must be unique.
        number_format: ``auto`` (detect per column; CSV default), ``id`` (1.234,5),
            ``en`` (1,234.5), or ``plain`` (canonical numbers, used for Excel cells).
        date_order: ``auto``, ``day_first``, or ``month_first`` for d/m/y-style dates.
        text_columns: Column labels the user chose to keep as text (no conversion).

    Returns:
        InferenceResult with converted columns, logical types, unconverted-value
        counts, and user-facing notes per column.
    """
    out = pd.DataFrame(index=df.index)
    result = InferenceResult(dataframe=out)
    for column in df.columns:
        series, logical, failed, notes = _infer_column(
            str(column),
            _clean_text(df[column]),
            number_format,
            date_order,
            column in (text_columns or set()),
        )
        out[column], result.types[column] = series, logical
        if failed:
            result.failed_counts[column] = failed
        if notes:
            result.notes[column] = notes
    return result


def detect_types(
    df: pd.DataFrame, number_format: NumberFormat = "auto", date_order: DateOrder = "auto"
) -> dict[str, LogicalType]:
    """Logical type each column would get at import (used on a sample at inspection)."""
    return infer_types(df, number_format=number_format, date_order=date_order).types
