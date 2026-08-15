from __future__ import annotations

import json

from pydantic import BaseModel, Field

from llm import invoke_llm, reasoning_llm
from prompts import INSIGHT_PROMPT
from state import AgentState, Finding, Insight
from nodes.utils import parse_json_model


class InsightOutput(BaseModel):
    findings: list[Finding] = Field(min_length=1)
    summary: str


def insight_extractor(state: AgentState) -> dict:
    insights: list[dict] = []
    summaries: list[str] = []
    errors = list(state.get("errors", []))
    for result in state.get("results", []):
        if result.get("error") or not result.get("rows"):
            continue
        try:
            rows = result["rows"][:50]  # preserve bounded context even for another database
            parsed = parse_json_model(invoke_llm(reasoning_llm, INSIGHT_PROMPT.format(
                question=state["question"], rows=json.dumps(rows, default=str)
            )), InsightOutput)
            insights.append(Insight(step_id=result["step_id"], findings=parsed.findings).model_dump())
            summaries.append(parsed.summary)
        except (RuntimeError, ValueError) as exc:
            errors.append(f"Insight extraction failed for step {result['step_id']}: {exc}")
    summary = " ".join(summaries) or "No validated query results were available for a narrative summary."
    return {"insights": insights, "final_summary": summary, "errors": errors}
