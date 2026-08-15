from __future__ import annotations

import re

from pydantic import BaseModel

from llm import coder_llm, invoke_llm
from prompts import QUERY_PROMPT
from state import AgentState, SQLQuery
from nodes.utils import parse_json_model


class QueryOutput(BaseModel):
    sql: str
    explanation: str


def query_generator(state: AgentState) -> dict:
    index = state.get("current_step", 0)
    plan = state.get("plan", [])
    if index >= len(plan):
        return {}
    step = plan[index]
    try:
        output = parse_json_model(invoke_llm(coder_llm, QUERY_PROMPT.format(
            question=state["question"], step=step["question"], schema=state["schema"], feedback=state.get("validation_feedback") or "None"
        )), QueryOutput)
        sql = output.sql.strip().rstrip(";")
        if not re.match(r"^(SELECT|WITH)\b", sql, re.IGNORECASE) or ";" in sql:
            raise ValueError("Only a single SQLite SELECT/WITH statement is allowed")
        query = SQLQuery(step_id=step["id"], sql=sql, explanation=output.explanation)
        prior = [q for q in state.get("queries", []) if q.get("step_id") != step["id"]]
        return {"queries": prior + [query.model_dump()], "validation_feedback": None}
    except (RuntimeError, ValueError) as exc:
        return {"validation_feedback": str(exc), "errors": state.get("errors", []) + [f"Query generation failed: {exc}"]}
