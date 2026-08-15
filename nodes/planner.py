from __future__ import annotations

from pydantic import BaseModel, Field

from llm import invoke_llm, reasoning_llm
from prompts import PLANNER_PROMPT
from state import AgentState, PlanStep
from nodes.utils import parse_json_model


class PlanOutput(BaseModel):
    steps: list[PlanStep] = Field(min_length=1, max_length=4)


def planner(state: AgentState) -> dict:
    try:
        # The requested graph deliberately inspects schema after planning; schema grounding happens in query generation.
        schema_hint = state.get("schema") or "Schema will be inspected before SQL is generated; ask data-focused questions only."
        parsed = parse_json_model(invoke_llm(reasoning_llm, PLANNER_PROMPT.format(question=state["question"], schema=schema_hint)), PlanOutput)
        return {"plan": [step.model_dump() for step in parsed.steps], "current_step": 0, "retry_count": 0, "errors": []}
    except (RuntimeError, ValueError) as exc:
        # Keep the workflow diagnosable if the local model is temporarily unavailable.
        fallback = [
            PlanStep(id=1, question=state["question"], purpose="Answer the requested business question"),
            PlanStep(id=2, question="Provide a relevant breakdown or trend for the business question", purpose="Add supporting context"),
        ]
        return {"plan": [item.model_dump() for item in fallback], "current_step": 0, "retry_count": 0,
                "errors": [f"Planning failed; using a generic fallback plan: {exc}"]}
