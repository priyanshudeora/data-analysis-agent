"""Read-only SQLite schema profile and relationship discovery for the frontend."""
from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path


def profile_database(db_path: str | Path) -> dict:
    """Return tables, columns, row counts, declared FKs, and cautious inferred links."""
    tables: list[dict] = []
    relationships: list[dict] = []
    with closing(sqlite3.connect(db_path)) as connection:
        names = [row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )]
        table_columns: dict[str, set[str]] = {}
        for table in names:
            columns = connection.execute(f'PRAGMA table_info("{table}")').fetchall()
            table_columns[table] = {column[1] for column in columns}
            row_count = connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            tables.append({
                "name": table,
                "row_count": row_count,
                "columns": [{"name": column[1], "type": column[2] or "TEXT", "primary_key": bool(column[5])} for column in columns],
            })
            for fk in connection.execute(f'PRAGMA foreign_key_list("{table}")').fetchall():
                relationships.append({"from_table": table, "from_column": fk[3], "to_table": fk[2], "to_column": fk[4], "kind": "Declared foreign key"})
    declared = {(item["from_table"], item["from_column"], item["to_table"], item["to_column"]) for item in relationships}
    for index, left in enumerate(names):
        for right in names[index + 1:]:
            for column in sorted(table_columns[left] & table_columns[right]):
                if (left, column, right, column) not in declared and (right, column, left, column) not in declared:
                    relationships.append({"from_table": left, "from_column": column, "to_table": right, "to_column": column, "kind": "Inferred shared column"})
    return {"tables": tables, "relationships": relationships}
