"""Plotly rendering of chart specs from real query results.

``render_chart`` validates the spec again (columns exist, values are numeric) and
falls back to "table only" instead of raising. Numbers use the Indonesian format
(1.234,5): Plotly's ``separators`` is set to ",.". Colors follow a fixed
categorical order (validated palette, dataviz reference instance); a category keeps
its color across charts via ``colors``.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import plotly.graph_objects as go
import streamlit as st

# Categorical slots in fixed order (never cycled).
SERIES_COLORS = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100")
NEUTRAL = "#8a8986"
TEXT = "#0F172A"
MUTED = "#475569"
GRID = "#E2E8F0"
SUPPORTED = {"bar", "hbar", "grouped_bar", "line", "area", "pie", "donut"}
MAX_MARKER_POINTS = 24


def _is_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def chart_problem(
    spec: dict[str, Any] | None, columns: Sequence[str], rows: Sequence
) -> str | None:
    """Why the spec cannot be drawn from these rows, or ``None`` when it can."""
    if not spec or spec.get("type") not in SUPPORTED:
        return "no chart for this result"
    if not rows:
        return "no rows"
    x, ys = spec.get("x"), list(spec.get("y") or [])
    if not ys or x is None or x not in columns or any(y not in columns for y in ys):
        return "the chart refers to columns that are not in the result"
    for y in ys:
        index = list(columns).index(y)
        values = [row[index] for row in rows]
        if not any(_is_number(v) for v in values) or not all(
            v is None or _is_number(v) for v in values
        ):
            return f"'{y}' is not numeric"
    if spec["type"] in {"pie", "donut"}:
        values = [row[list(columns).index(ys[0])] or 0 for row in rows]
        if any(v < 0 for v in values) or sum(values) <= 0:
            return "a share chart needs positive values"
    return None


def _hover(label: str, value_format: str) -> str:
    return f"<b>%{{x}}</b><br>{label}: %{{y:{value_format}}}<extra></extra>"


def build_figure(
    spec: dict[str, Any],
    columns: Sequence[str],
    rows: Sequence[Sequence[Any]],
    labels: dict[str, str] | None = None,
    colors: dict[str, str] | None = None,
    value_format: str | None = None,
    percent_axis: bool = False,
    series_colors: Sequence[str] | None = None,
) -> go.Figure:
    """Plotly figure for a validated spec (call :func:`chart_problem` first)."""
    labels = labels or {}
    columns = list(columns)
    x_col, ys = spec["x"], list(spec["y"])
    xi = columns.index(x_col)
    kind = spec["type"]
    data = [list(row) for row in rows]
    if kind == "hbar":
        yi = columns.index(ys[0])
        data.sort(key=lambda r: r[yi] if r[yi] is not None else float("-inf"), reverse=True)
        if spec.get("top_n"):
            data = data[: spec["top_n"]]
        data.reverse()  # largest at the top
    if kind in {"line", "area"}:
        data.sort(key=lambda r: str(r[xi]))  # time order (ISO dates and period labels)
    xs = [str(r[xi]) if r[xi] is not None else "(empty)" for r in data]
    if value_format is None:  # decimals only when the data has them
        values = [r[columns.index(y)] for r in data for y in ys]
        fractional = any(isinstance(v, float) and not float(v).is_integer() for v in values)
        value_format = ",.2f" if fractional else ",.0f"

    fig = go.Figure()
    if kind in {"pie", "donut"}:
        yi = columns.index(ys[0])
        slice_colors = [
            (colors or {}).get(x, SERIES_COLORS[i % len(SERIES_COLORS)]) for i, x in enumerate(xs)
        ]
        fig.add_trace(
            go.Pie(
                labels=[labels.get(x, x) for x in xs],
                values=[r[yi] or 0 for r in data],
                hole=0.55 if kind == "donut" else 0,
                sort=False,
                marker={"colors": slice_colors, "line": {"color": "#FFFFFF", "width": 2}},
                texttemplate="%{percent:.1%}",
                hovertemplate="<b>%{label}</b><br>%{value:,.0f} (%{percent})<extra></extra>",
            )
        )
    else:
        for n, y in enumerate(ys):
            yi = columns.index(y)
            values = [r[yi] for r in data]
            name = labels.get(y, y)
            palette = series_colors or SERIES_COLORS
            color = palette[n % len(palette)]
            if kind in {"line", "area"}:
                fig.add_trace(
                    go.Scatter(
                        x=xs,
                        y=values,
                        name=name,
                        mode="lines+markers" if len(xs) <= MAX_MARKER_POINTS else "lines",
                        line={"color": color, "width": 2},
                        marker={"size": 8, "color": color},
                        fill="tozeroy" if kind == "area" else None,
                        hovertemplate=_hover(name, value_format),
                    )
                )
            elif kind == "hbar":
                fig.add_trace(
                    go.Bar(
                        x=values,
                        y=xs,
                        name=name,
                        orientation="h",
                        marker={"color": color},
                        hovertemplate=f"<b>%{{y}}</b><br>{name}: %{{x:{value_format}}}"
                        "<extra></extra>",
                    )
                )
            else:
                bar_colors = (
                    [(colors or {}).get(x, color) for x in xs] if len(ys) == 1 and colors else color
                )
                fig.add_trace(
                    go.Bar(
                        x=[labels.get(x, x) for x in xs],
                        y=values,
                        name=name,
                        marker={"color": bar_colors},
                        hovertemplate=_hover(name, value_format),
                    )
                )
    show_legend = len(ys) > 1 or kind in {"pie", "donut"}
    fig.update_layout(
        separators=",.",  # Indonesian: decimal comma, thousands dot
        title={"text": spec.get("title") or "", "font": {"size": 15, "color": TEXT}},
        height=340,
        margin={"l": 8, "r": 8, "t": 48 if spec.get("title") else 16, "b": 8},
        paper_bgcolor="#FFFFFF",
        plot_bgcolor="#FFFFFF",
        font={"color": MUTED, "size": 12},
        showlegend=show_legend,
        legend=(
            {"orientation": "h", "yanchor": "top", "y": -0.02, "x": 0.5, "xanchor": "center"}
            if kind in {"pie", "donut"}
            else {"orientation": "h", "yanchor": "bottom", "y": 1.0, "x": 0}
        ),
        barmode="group",
        bargap=0.3,
        bargroupgap=0.08,
        hoverlabel={"bgcolor": "#FFFFFF", "font": {"color": TEXT}},
    )
    if kind not in {"pie", "donut"}:
        value_axis = {"gridcolor": GRID, "zerolinecolor": GRID, "rangemode": "tozero"}
        if percent_axis:
            value_axis["tickformat"] = ".0%"
        else:
            value_axis["tickformat"] = ",d" if value_format.endswith("0f") else ","
        category_axis = {"showgrid": False, "linecolor": GRID, "type": "category"}
        if kind == "hbar":
            fig.update_xaxes(**value_axis, title=labels.get(ys[0], ys[0]), automargin=True)
            fig.update_yaxes(**category_axis, automargin=True)
        else:
            fig.update_yaxes(
                **value_axis,
                title=labels.get(ys[0], ys[0]) if len(ys) == 1 else "",
                automargin=True,
            )
            fig.update_xaxes(**category_axis, title=labels.get(x_col, x_col), automargin=True)
    return fig


def render_chart(
    spec: dict[str, Any] | None,
    columns: Sequence[str],
    rows: Sequence[Sequence[Any]],
    *,
    labels: dict[str, str] | None = None,
    colors: dict[str, str] | None = None,
    value_format: str | None = None,
    percent_axis: bool = False,
    series_colors: Sequence[str] | None = None,
    key: str | None = None,
) -> bool:
    """Draw the chart; return False (and draw nothing) when it cannot be drawn safely."""
    if chart_problem(spec, columns, rows) is not None:
        return False
    try:
        figure = build_figure(
            spec, columns, rows, labels, colors, value_format, percent_axis, series_colors
        )
    except (KeyError, ValueError, TypeError, IndexError):
        return False
    st.plotly_chart(
        figure,
        theme=None,
        key=key,
        config={"displaylogo": False, "modeBarButtonsToRemove": ["select2d", "lasso2d"]},
    )
    if spec.get("top_n") and len(rows) > spec["top_n"]:
        st.caption(f"Showing the top {spec['top_n']} of {len(rows)} categories.")
    return True
