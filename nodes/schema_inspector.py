from __future__ import annotations

import sqlite3

from state import AgentState


def schema_inspector(state: AgentState) -> dict:
    """Read only table and column metadata; never put full data into LLM context."""
    db_path = state["db_path"]
    try:
        with sqlite3.connect(db_path) as conn:
            try:
                # LangChain's SQLDatabase is the database toolkit boundary; sample rows stay disabled.
                from langchain_community.utilities import SQLDatabase
                toolkit_db = SQLDatabase.from_uri(f"sqlite:///{db_path.replace(chr(92), '/')}", sample_rows_in_table_info=0)
                tables = [(name,) for name in toolkit_db.get_usable_table_names()]
            except ImportError:
                # Keeps this small node testable before the optional project dependencies are installed.
                tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()
            lines: list[str] = []
            for (table,) in tables:
                columns = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
                rendered = ", ".join(f"{name} {dtype}" for _, name, dtype, *_ in columns)
                lines.append(f"{table}({rendered})")
        if not lines:
            raise ValueError("No user tables found in SQLite database")
        return {"schema": "\n".join(lines)}
    except (sqlite3.Error, OSError, ValueError) as exc:
        return {"schema": "", "errors": [f"Schema inspection failed: {exc}"]}
