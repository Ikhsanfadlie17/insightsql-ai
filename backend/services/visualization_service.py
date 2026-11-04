"""Rule-based chart selection (no LLM). An LLM hint is used only when it fits the data.

Column kinds are inferred from the result values themselves: numbers, ISO dates,
period labels (``2026``, ``2026-03``, ``2026-Q1``; or integer years in a column named
like a year), text, or empty. Every spec is validated against the result before it
is returned, and the frontend validates it again before drawing.
"""

from __future__ import annotations

import re

from backend.schemas.charts import ChartSpec, ColumnKind
from backend.schemas.llm_outputs import ChartHint
from backend.schemas.query import QueryResult

MAX_SERIES = 4
MAX_BAR_CATEGORIES = 12
MAX_DONUT_SLICES = 6
HBAR_TOP_N = 20
MAX_LABEL_CHARS = 20

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}(:\d{2}(\.\d+)?)?)?$")
_PERIOD = re.compile(r"^\d{4}(-(0[1-9]|1[0-2])|-Q[1-4])?$")
_YEAR_NAMES = re.compile(r"(^|_)(year|tahun|thn)($|_)", re.IGNORECASE)
_TIME_KINDS = {"date", "period"}


def _is_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _kind(name: str, values: list[object]) -> ColumnKind:
    present = [v for v in values if v is not None]
    if not present:
        return "empty"
    if all(_is_number(v) for v in present):
        is_year = all(isinstance(v, int) and 1900 <= v <= 2200 for v in present)
        return "period" if is_year and _YEAR_NAMES.search(name) else "number"
    if all(isinstance(v, str) for v in present):
        if all(_DATE.match(v) for v in present):
            return "date"
        if all(_PERIOD.match(v) for v in present):
            return "period"
    return "text"


def infer_result_types(result: QueryResult) -> dict[str, ColumnKind]:
    """Kind of every column, from its values."""
    return {
        name: _kind(name, [row[i] for row in result.rows]) for i, name in enumerate(result.columns)
    }


def validate_chart_spec(spec: ChartSpec, result: QueryResult) -> bool:
    """True when the spec can be drawn from ``result`` without inventing anything."""
    kinds = infer_result_types(result)
    if not result.rows or not spec.y or len(spec.y) > MAX_SERIES:
        return False
    if any(kinds.get(y) != "number" for y in spec.y):
        return False
    if spec.type == "kpi":
        return result.row_count == 1 and spec.x is None
    if spec.x is None or spec.x not in kinds or spec.x in spec.y:
        return False
    x_kind = kinds[spec.x]
    if spec.type in {"line", "area"}:
        return x_kind in _TIME_KINDS
    if x_kind == "empty":
        return False
    if spec.type in {"pie", "donut"}:
        values = [row[result.columns.index(spec.y[0])] for row in result.rows]
        return (
            len(spec.y) == 1
            and 2 <= result.row_count <= MAX_DONUT_SLICES
            and all(v is not None and v >= 0 for v in values)
            and sum(values) > 0
        )
    if spec.type == "grouped_bar":
        return len(spec.y) >= 2
    return spec.type in {"bar", "hbar"}


def _from_hint(hint: ChartHint, result: QueryResult) -> ChartSpec | None:
    if hint.type == "table":
        return None
    spec = ChartSpec(type=hint.type, x=hint.x, y=list(hint.y)[:MAX_SERIES])
    return spec if validate_chart_spec(spec, result) else None


def recommend_chart(
    result: QueryResult, hint: ChartHint | None = None, title: str = ""
) -> ChartSpec | None:
    """Choose a chart for ``result``; ``None`` means "show the table only".

    Rules: one row of 1-4 numbers -> KPI cards; a time column + numbers -> line;
    a category + one number -> bar (<= 12 categories), horizontal top-20 bar
    (more categories or long labels), or donut when hinted with <= 6 non-negative
    slices; a category + several numbers -> grouped bar. A hint is used only when it
    passes validation; a ``table`` hint means no chart.
    """
    if hint is not None and hint.type == "table":
        return None
    if not result.rows:
        return None
    kinds = infer_result_types(result)
    numbers = [c for c in result.columns if kinds[c] == "number"]
    if not numbers:
        return None

    if result.row_count == 1:
        if len(numbers) == len(result.columns) and len(numbers) <= MAX_SERIES:
            return ChartSpec(type="kpi", y=numbers, title=title)
        return None

    if hint is not None:
        hinted = _from_hint(hint, result)
        if hinted is not None and hinted.type not in {"bar", "hbar"}:
            hinted.title = title
            return hinted

    time_axis = next((c for c in result.columns if kinds[c] in _TIME_KINDS), None)
    if time_axis is not None:
        series = [c for c in numbers if c != time_axis][:MAX_SERIES]
        spec = ChartSpec(type="line", x=time_axis, y=series, title=title)
        return spec if validate_chart_spec(spec, result) else None

    category = next((c for c in result.columns if kinds[c] == "text"), None)
    if category is None:
        return None
    if len(numbers) >= 2:
        spec = ChartSpec(type="grouped_bar", x=category, y=numbers[:MAX_SERIES], title=title)
        if result.row_count <= MAX_BAR_CATEGORIES:
            return spec if validate_chart_spec(spec, result) else None
        numbers = numbers[:1]
    labels = [str(row[result.columns.index(category)]) for row in result.rows]
    long_labels = any(len(label) > MAX_LABEL_CHARS for label in labels)
    if result.row_count > MAX_BAR_CATEGORIES or long_labels:
        spec = ChartSpec(type="hbar", x=category, y=numbers[:1], title=title)
        if result.row_count > HBAR_TOP_N:
            spec.top_n = HBAR_TOP_N
    else:
        spec = ChartSpec(type="bar", x=category, y=numbers[:1], title=title)
    return spec if validate_chart_spec(spec, result) else None
