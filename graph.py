"""InsightPilot StateGraph assembly; all LLM calls are intentionally sequential."""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from nodes.chart_selector import chart_selector
from nodes.insight_extractor import insight_extractor
from nodes.planner import planner
from nodes.query_executor import query_executor
from nodes.query_generator import query_generator
from nodes.query_validator import validation_route, query_validator
from nodes.schema_inspector import schema_inspector
from state import AgentState


def advance_step(state: AgentState) -> dict:
    return {"current_step": state.get("current_step", 0) + 1, "retry_count": 0, "validation_feedback": None}


def build_graph():
    workflow = StateGraph(AgentState)
    workflow.add_node("planner", planner)
    workflow.add_node("schema_inspector", schema_inspector)
    workflow.add_node("query_generator", query_generator)
    workflow.add_node("query_executor", query_executor)
    workflow.add_node("query_validator", query_validator)
    workflow.add_node("advance_step", advance_step)
    workflow.add_node("insight_extractor", insight_extractor)
    workflow.add_node("chart_selector", chart_selector)
    workflow.add_edge(START, "planner")
    workflow.add_edge("planner", "schema_inspector")
    workflow.add_edge("schema_inspector", "query_generator")
    workflow.add_edge("query_generator", "query_executor")
    workflow.add_edge("query_executor", "query_validator")
    workflow.add_conditional_edges("query_validator", validation_route, {
        "retry": "query_generator", "next": "advance_step", "skip": "insight_extractor", "insights": "insight_extractor"
    })
    workflow.add_edge("advance_step", "query_generator")
    workflow.add_edge("insight_extractor", "chart_selector")
    workflow.add_edge("chart_selector", END)
    return workflow.compile()


insight_graph = build_graph()
