"""Small independent checks that do not need Ollama; run after `python db/setup_db.py`."""
from __future__ import annotations

from pathlib import Path

from db.setup_db import setup_database
from nodes.query_executor import query_executor
from nodes.query_validator import query_validator
from nodes.schema_inspector import schema_inspector


def main() -> None:
    db_path = str(setup_database())
    base = {"db_path": db_path, "plan": [{"id": 1, "question": "Totals", "purpose": "test"}], "current_step": 0,
            "queries": [{"step_id": 1, "sql": "SELECT industry, SUM(employees_laid_off) AS layoffs FROM layoffs WHERE date >= '2023-01-01' AND date < '2024-01-01' GROUP BY industry ORDER BY layoffs DESC", "explanation": "test"}],
            "results": [], "errors": []}
    schema = schema_inspector(base)
    assert "layoffs(" in schema["schema"], schema
    executed = query_executor(base)
    assert executed["results"][0]["rows"], executed
    checked = query_validator({**base, **executed, "retry_count": 0})
    assert checked["validation_feedback"] is None, checked
    print("schema_inspector, query_executor, and query_validator: PASS")


if __name__ == "__main__":
    main()
