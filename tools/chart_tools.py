"""Strict, data-bound Plotly chart tools used by the chart-selection LLM node."""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from typing import Callable

import pandas as pd
import plotly.express as px
from langchain_core.tools import tool


@dataclass
class ChartContext:
    frame: pd.DataFrame
    figure: object | None = None
    chart_type: str | None = None
    columns: tuple[str, str] | None = None
    title: str | None = None


_CURRENT_CHART: ContextVar[ChartContext | None] = ContextVar("insightpilot_chart_context", default=None)


def set_chart_context(frame: pd.DataFrame):
    """Make a real query-result dataframe available to the next tool call only."""
    return _CURRENT_CHART.set(ChartContext(frame=frame))


def reset_chart_context(token: object) -> None:
    _CURRENT_CHART.reset(token)  # type: ignore[arg-type]


def built_chart() -> ChartContext | None:
    return _CURRENT_CHART.get()


def _context_for(*columns: str) -> ChartContext | str:
    context = _CURRENT_CHART.get()
    if context is None:
        return "ERROR: no active query result is available for this chart tool."
    missing = [column for column in columns if column not in context.frame.columns]
    if missing:
        return f"ERROR: column(s) {missing} do not exist. Available columns: {list(context.frame.columns)}"
    if context.frame.empty:
        return "ERROR: the validated query result is empty; a chart cannot be created."
    return context


def _make_chart(kind: str, columns: tuple[str, str], title: str, factory: Callable[..., object]) -> str:
    context = _context_for(*columns)
    if isinstance(context, str):
        return context
    x_column, y_column = columns
    context.figure = factory(context.frame, x_column, y_column, title)
    context.chart_type, context.columns, context.title = kind, columns, title
    return f"OK: created {kind} chart with {x_column} and {y_column} from the validated query result."


@tool
def make_bar_chart(x_column: str, y_column: str, title: str) -> str:
    """Creates a bar chart from the current query result using real data and exact column names."""
    return _make_chart("bar", (x_column, y_column), title, lambda frame, x, y, name: px.bar(frame, x=x, y=y, title=name))


@tool
def make_line_chart(x_column: str, y_column: str, title: str) -> str:
    """Creates a line chart from the current query result using real data and exact column names."""
    return _make_chart("line", (x_column, y_column), title, lambda frame, x, y, name: px.line(frame, x=x, y=y, title=name, markers=True))


@tool
def make_pie_chart(labels_column: str, values_column: str, title: str) -> str:
    """Creates a pie chart from the current query result using real data and exact column names."""
    return _make_chart("pie", (labels_column, values_column), title, lambda frame, x, y, name: px.pie(frame, names=x, values=y, title=name))


@tool
def make_scatter_chart(x_column: str, y_column: str, title: str) -> str:
    """Creates a scatter chart from the current query result using real data and exact column names."""
    return _make_chart("scatter", (x_column, y_column), title, lambda frame, x, y, name: px.scatter(frame, x=x, y=y, title=name))


CHART_TOOLS = [make_bar_chart, make_line_chart, make_pie_chart, make_scatter_chart]
