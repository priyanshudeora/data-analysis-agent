from __future__ import annotations

from state import AgentState
from nodes.utils import latest_by_step

MAX_QUERY_RETRIES = 3


def query_validator(state: AgentState) -> dict:
    step = state["plan"][state.get("current_step", 0)]
    result = latest_by_step(state.get("results", []), step["id"])
    feedback: str | None = None
    if not result or result.get("error"):
        feedback = (result or {}).get("error", "Query produced no result")
    elif not result.get("rows"):
        feedback = "Query returned an empty result set; revise filters or aggregation."
    else:
        for row in result["rows"]:
            if any(isinstance(value, (int, float)) and value < 0 for value in row.values()):
                feedback = "Result has a negative aggregate; correct the aggregation."
                break
    if feedback:
        retries = state.get("retry_count", 0) + 1
        return {"retry_count": retries, "validation_feedback": feedback,
                "errors": state.get("errors", []) + ([f"Validation warning: {feedback}"] if retries >= MAX_QUERY_RETRIES else [])}
    return {"retry_count": 0, "validation_feedback": None}


def validation_route(state: AgentState) -> str:
    if state.get("validation_feedback") and state.get("retry_count", 0) < MAX_QUERY_RETRIES:
        return "retry"
    if state.get("validation_feedback"):
        return "skip"
    if state.get("current_step", 0) + 1 < len(state.get("plan", [])):
        return "next"
    return "insights"
