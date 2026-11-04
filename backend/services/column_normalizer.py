"""Convert arbitrary spreadsheet headers into safe, unique SQL identifiers."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

MAX_IDENTIFIER_LENGTH = 63

# Keywords that would be confusing or invalid as bare column names.
_RESERVED = frozenset(
    {
        "all", "alter", "and", "any", "as", "between", "by", "case", "cast", "check",
        "column", "constraint", "create", "default", "delete", "distinct", "drop", "else",
        "end", "false", "from", "group", "having", "in", "insert", "into", "is", "join",
        "like", "limit", "not", "null", "offset", "on", "or", "order", "primary",
        "references", "select", "set", "table", "then", "true", "union", "update", "user",
        "values", "when", "where", "with",
    }
)  # fmt: skip
_UNNAMED = re.compile(r"^unnamed_\d+(_\d+)?$")


@dataclass(frozen=True)
class ColumnName:
    """A normalized column name and the header it came from."""

    original: str
    name: str


def _slug(header: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", header).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", ascii_text.lower()).strip("_")


def normalize_columns(headers: list[str]) -> list[ColumnName]:
    """Normalize headers to lowercase snake_case identifiers.

    Rules: accents are removed, non-alphanumerics become ``_``, blank or pandas
    ``Unnamed: n`` headers become ``column_<position>`` (1-based), names starting
    with a digit get a ``col_`` prefix, reserved words get a ``_col`` suffix, and
    duplicates get ``_2``, ``_3``... suffixes. Original headers are preserved.
    """
    result: list[ColumnName] = []
    used: set[str] = set()
    for position, header in enumerate(headers, start=1):
        original = "" if header is None else str(header)
        name = _slug(original)
        if not name or _UNNAMED.match(name):
            name = f"column_{position}"
        if name[0].isdigit():
            name = f"col_{name}"
        if name in _RESERVED:
            name = f"{name}_col"
        name = name[:MAX_IDENTIFIER_LENGTH].rstrip("_")

        candidate, counter = name, 2
        while candidate in used:
            suffix = f"_{counter}"
            candidate = name[: MAX_IDENTIFIER_LENGTH - len(suffix)] + suffix
            counter += 1
        used.add(candidate)
        result.append(ColumnName(original=original, name=candidate))
    return result
