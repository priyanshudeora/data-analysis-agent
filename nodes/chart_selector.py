"""Tool-bound chart selection. The LLM never receives or generates chart values."""
from __future__ import annotations

import json
import logging

import pandas as pd
from langchain_core.messages import HumanMessage, ToolMessage

from llm import invoke_message, reasoning_llm
from nodes.utils import latest_by_step
from prompts import CHART_TOOL_PROMPT
from state import AgentState, ChartSpec
from tools.chart_tools import CHART_TOOLS, built_chart, reset_chart_context, set_chart_context

LOGGER = logging.getLogger(__name__)
MAX_TOOL_SELECTION_ATTEMPTS = 3
TOOL_TO_CHART = {"make_bar_chart": "bar", "make_line_chart": "line", "make_pie_chart": "pie", "make_scatter_chart": "scatter"}


def _chart_args(call: dict) -> tuple[str | None, str | None, str]:
    args = call.get("args", {})
    return args.get("x_column", args.get("labels_column")), args.get("y_column", args.get("values_column")), str(args.get("title", "InsightPilot chart"))


def _fallback(step_id: int, reason: str) -> dict:
    return ChartSpec(step_id=step_id, chart_type="table", title=f"Validated result {step_id}", caption=reason).model_dump()


def chart_selector(state: AgentState) -> dict:
    charts: list[dict] = []
    figures: dict[int, object] = {}
    errors = list(state.get("errors", []))
    tool_llm = reasoning_llm.bind_tools(CHART_TOOLS)
    for insight in state.get("insights", []):
        step_id = insight["step_id"]
        result = latest_by_step(state.get("results", []), step_id) or {}
        frame = pd.DataFrame(result.get("rows", []), columns=result.get("columns", []))
        if frame.empty:
            charts.append(_fallback(step_id, "No validated rows are available; showing a table."))
            continue
        finding = insight.get("findings", [{}])[0].get("finding", "Validated result overview")
        messages = [HumanMessage(content=CHART_TOOL_PROMPT.format(columns=json.dumps(list(frame.columns)), insight=finding))]
        context_token = set_chart_context(frame)
        try:
            completed = False
            for _ in range(MAX_TOOL_SELECTION_ATTEMPTS):
                response = invoke_message(tool_llm, messages)
                calls = getattr(response, "tool_calls", [])
                if not calls:
                    messages.extend([response, HumanMessage(content="ERROR: call exactly one chart tool.")])
                    continue
                call = calls[0]
                tool = next((candidate for candidate in CHART_TOOLS if candidate.name == call["name"]), None)
                if tool is None:
                    messages.extend([response, ToolMessage(content="ERROR: unknown chart tool.", tool_call_id=call["id"])])
                    continue
                tool_result = tool.invoke(call["args"])
                messages.extend([response, ToolMessage(content=str(tool_result), tool_call_id=call["id"])])
                context = built_chart()
                if str(tool_result).startswith("OK:") and context and context.figure is not None:
                    x, y, title = _chart_args(call)
                    charts.append(ChartSpec(step_id=step_id, chart_type=TOOL_TO_CHART[call["name"]], x=x, y=y, title=title, caption=finding).model_dump())
                    figures[step_id] = context.figure
                    completed = True
                    break
            if not completed:
                charts.append(_fallback(step_id, "Chart tool selection did not produce a valid chart; showing the source table."))
                errors.append(f"Chart selection failed for step {step_id}: tool retries exhausted")
        except (RuntimeError, ValueError, KeyError, TypeError) as exc:
            LOGGER.warning("Chart selection failed for step %s: %s", step_id, exc)
            charts.append(_fallback(step_id, "Chart selection failed; showing the validated source table."))
            errors.append(f"Chart selection failed for step {step_id}: {exc}")
        finally:
            reset_chart_context(context_token)
    return {"chart_specs": charts, "figures": figures, "errors": errors}
