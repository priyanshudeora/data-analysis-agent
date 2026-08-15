from __future__ import annotations

import sqlite3

from state import AgentState, QueryResult
from nodes.utils import latest_by_step


def query_executor(state: AgentState) -> dict:
    step = state["plan"][state.get("current_step", 0)]
    query = latest_by_step(state.get("queries", []), step["id"])
    if not query:
        return {"results": state.get("results", []) + [QueryResult(step_id=step["id"], error="No generated query").model_dump()]}
    try:
        with sqlite3.connect(state["db_path"]) as conn:
            cursor = conn.execute(query["sql"])
            columns = [column[0] for column in cursor.description or []]
            rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
        result = QueryResult(step_id=step["id"], columns=columns, rows=rows)
    except sqlite3.Error as exc:
        result = QueryResult(step_id=step["id"], error=str(exc))
    prior = [item for item in state.get("results", []) if item.get("step_id") != step["id"]]
    return {"results": prior + [result.model_dump()]}
