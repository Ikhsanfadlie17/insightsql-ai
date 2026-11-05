"""Indonesian number formatting for the UI: 1.234.567 and 63,2%. ``None`` -> "N/A"."""

from __future__ import annotations

import math

NA = "N/A"


def _swap(text: str) -> str:
    """Turn Python's 1,234.5 into Indonesian 1.234,5."""
    return text.replace(",", "\0").replace(".", ",").replace("\0", ".")


def fmt_int(value: int | float | None) -> str:
    """Whole number with dots as thousands separators."""
    if value is None:
        return NA
    return _swap(f"{round(value):,}")


def fmt_number(value: int | float | None, decimals: int = 1) -> str:
    """Number with ``decimals`` places (trailing zeros kept for alignment)."""
    if value is None:
        return NA
    return _swap(f"{value:,.{decimals}f}")


def fmt_pct(ratio: float | None, decimals: int = 1) -> str:
    """A ratio (0.632) as a percentage (63,2%)."""
    if ratio is None:
        return NA
    return _swap(f"{ratio * 100:,.{decimals}f}") + "%"


def fmt_signed_pct(ratio: float | None, decimals: int = 1) -> str:
    """A change ratio with its sign (+18,4% / −5,0%)."""
    if ratio is None:
        return NA
    text = fmt_pct(abs(ratio), decimals)
    if round(ratio * 100, decimals) == 0:
        return text
    return ("+" if ratio > 0 else "−") + text


def fmt_value(value: object) -> str:
    """Best-effort display of a table cell value."""
    if value is None:
        return NA
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        return fmt_int(value)
    if isinstance(value, float):
        return fmt_number(value, 2)
    return str(value)


def fraction_decimals(value: float) -> int:
    """2 decimals; small fractions get enough for 3 significant digits (at most 8)."""
    if value == 0 or abs(value) >= 1:
        return 2
    return min(max(2, 2 - math.floor(math.log10(abs(value)))), 8)


def trim_decimals(text: str, keep: int = 2) -> str:
    """Drop trailing zeros after the decimal comma, keeping at least ``keep`` decimals."""
    whole, _, decimals = text.partition(",")
    if not decimals:
        return text
    decimals = decimals.rstrip("0").ljust(keep, "0")
    return f"{whole},{decimals}"


def fmt_stat(value: float | None) -> str:
    """Profile statistic: 4.000.000, 38.247.512,50, or 0,0045 (small fractions keep their
    significant digits); blank when not applicable."""
    if value is None:
        return ""
    if float(value).is_integer():
        return fmt_number(value, 0)
    return trim_decimals(fmt_number(value, fraction_decimals(value)))
