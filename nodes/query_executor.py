from __future__ import annotations

import sqlite3
import time
from contextlib import closing
from pathlib import Path

from state import AgentState, QueryResult
from nodes.utils import latest_by_step

MAX_RESULT_ROWS = 1000
QUERY_TIMEOUT_SECONDS = 10


def _authorize(action, arg1, arg2, database, source):
    if action == sqlite3.SQLITE_READ:
        # SQLite reports no database/column for COUNT(*) table reads.
        allowed = database == "main" or (database is None and arg2 == "")
        return sqlite3.SQLITE_OK if allowed else sqlite3.SQLITE_DENY
    if action in {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_RECURSIVE}:
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_FUNCTION and arg2 not in {"load_extension", "readfile", "writefile"}:
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def query_executor(state: AgentState) -> dict:
    step = state["plan"][state.get("current_step", 0)]
    query = latest_by_step(state.get("queries", []), step["id"])
    if not query:
        return {"results": state.get("results", []) + [QueryResult(step_id=step["id"], error="No generated query").model_dump()]}
    try:
        uri = Path(state["db_path"]).resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=5)) as conn:
            conn.execute("PRAGMA query_only=ON")
            conn.set_authorizer(_authorize)
            deadline = time.monotonic() + QUERY_TIMEOUT_SECONDS
            conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
            cursor = conn.execute(query["sql"])
            columns = [column[0] for column in cursor.description or []]
            fetched = cursor.fetchmany(MAX_RESULT_ROWS + 1)
            rows = [dict(zip(columns, row)) for row in fetched[:MAX_RESULT_ROWS]]
        result = QueryResult(step_id=step["id"], columns=columns, rows=rows,
                             truncated=len(fetched) > MAX_RESULT_ROWS)
    except sqlite3.Error as exc:
        result = QueryResult(step_id=step["id"], error=str(exc))
    prior = [item for item in state.get("results", []) if item.get("step_id") != step["id"]]
    return {"results": prior + [result.model_dump()]}
