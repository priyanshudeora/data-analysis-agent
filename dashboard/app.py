"""Streamlit presentation layer for a saved InsightPilot graph state."""
from __future__ import annotations

import json
import hashlib
import sys
import io
import zipfile
import csv
import re
import sqlite3
import time
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any

import plotly.graph_objects as go
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db.setup_db import setup_database
from db.schema_profile import profile_database
from main import run

RESULT_PATH = Path(__file__).with_name("latest_run.json")
UPLOAD_DIR = ROOT / "uploads"
MAX_UPLOAD_BYTES = 100 * 1024 * 1024
MAX_UNZIPPED_BYTES = 250 * 1024 * 1024
OVERVIEW_QUESTION = (
    "Provide a comprehensive overview of this database: identify the main entities, "
    "record distributions, meaningful trends, notable outliers, and the most useful starting insights."
)


def _json_default(value: Any) -> Any:
    if hasattr(value, "to_plotly_json"):
        return value.to_plotly_json()
    return str(value)


def save_uploaded_database(uploaded_file: Any) -> Path:
    """Save SQLite data directly, or import CSV data into a temporary SQLite database."""
    content = uploaded_file.getvalue()
    if len(content) > MAX_UPLOAD_BYTES:
        raise ValueError("Upload exceeds the 100 MB limit.")
    name = Path(uploaded_file.name).name
    if name.lower().endswith(".zip"):
        kind, payload = _contents_from_zip(content)
        if kind == "sqlite":
            content, name = payload
        else:
            return _csv_files_to_sqlite(payload, hashlib.sha256(content).hexdigest()[:12])
    if name.lower().endswith(".csv"):
        return _csv_files_to_sqlite([(name, content)], hashlib.sha256(content).hexdigest()[:12])
    if not content.startswith(b"SQLite format 3\x00"):
        raise ValueError("Upload a valid SQLite database, CSV file, or ZIP containing CSV files.")
    UPLOAD_DIR.mkdir(exist_ok=True)
    digest = hashlib.sha256(content).hexdigest()[:12]
    destination = UPLOAD_DIR / f"{digest}-{name}"
    destination.write_bytes(content)
    return destination


def _contents_from_zip(content: bytes) -> tuple[str, Any]:
    """Return one SQLite database or a non-empty set of CSV files from a safe archive."""
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            members = archive.infolist()
            if len(members) > 100:
                raise ValueError("ZIP archive contains too many files.")
            if sum(member.file_size for member in members) > MAX_UNZIPPED_BYTES:
                raise ValueError("ZIP archive expands beyond the 250 MB limit.")
            databases, csv_files = [], []
            for member in members:
                path = PurePosixPath(member.filename)
                if path.is_absolute() or ".." in path.parts:
                    raise ValueError("ZIP archive contains an unsafe file path.")
                if not member.is_dir() and path.suffix.lower() in {".db", ".sqlite", ".sqlite3"}:
                    databases.append(member)
                if not member.is_dir() and path.suffix.lower() == ".csv":
                    csv_files.append(member)
            if len(databases) == 1 and not csv_files:
                database = databases[0]
                return "sqlite", (archive.read(database), PurePosixPath(database.filename).name)
            if not databases and csv_files:
                return "csv", [(PurePosixPath(member.filename).name, archive.read(member)) for member in csv_files]
            raise ValueError("ZIP must contain exactly one SQLite database or one or more CSV files, not both.")
    except zipfile.BadZipFile as exc:
        raise ValueError("The uploaded ZIP file is invalid or corrupt.") from exc


def _safe_identifier(value: str, fallback: str, used: set[str]) -> str:
    base = re.sub(r"[^A-Za-z0-9_]", "_", value.strip()).strip("_").lower() or fallback
    if base[0].isdigit():
        base = f"_{base}"
    candidate, suffix = base, 2
    while candidate in used:
        candidate = f"{base}_{suffix}"
        suffix += 1
    used.add(candidate)
    return candidate


def _csv_files_to_sqlite(files: list[tuple[str, bytes]], digest: str) -> Path:
    """Import CSVs into a content-addressed SQLite DB; every CSV becomes one TEXT table."""
    UPLOAD_DIR.mkdir(exist_ok=True)
    destination = UPLOAD_DIR / f"{digest}-csv-import.db"
    temporary = UPLOAD_DIR / f"{digest}-csv-import.tmp"
    if temporary.exists():
        temporary.unlink()
    used_tables: set[str] = set()
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(temporary)
        try:
            for filename, content in files:
                try:
                    text = content.decode("utf-8-sig")
                except UnicodeDecodeError as exc:
                    raise ValueError(f"{filename} must be UTF-8 encoded.") from exc
                reader = csv.reader(io.StringIO(text))
                header = next(reader, None)
                if not header or not any(column.strip() for column in header):
                    raise ValueError(f"{filename} has no usable header row.")
                table_name = _safe_identifier(Path(filename).stem, "data", used_tables)
                used_columns: set[str] = set()
                columns = [_safe_identifier(column, f"column_{index + 1}", used_columns) for index, column in enumerate(header)]
                quoted_columns = ", ".join(f'"{column}" TEXT' for column in columns)
                connection.execute(f'CREATE TABLE "{table_name}" ({quoted_columns})')
                placeholders = ", ".join("?" for _ in columns)
                insert = f'INSERT INTO "{table_name}" VALUES ({placeholders})'
                batch: list[list[str | None]] = []
                for row in reader:
                    values = (row + [None] * len(columns))[:len(columns)]
                    batch.append(values)
                    if len(batch) == 1000:
                        connection.executemany(insert, batch)
                        batch.clear()
                if batch:
                    connection.executemany(insert, batch)
            connection.commit()
        finally:
            # SQLite's context manager commits/rolls back but does not reliably release
            # the Windows file handle before a rename; close it explicitly.
            connection.close()
            connection = None
        for attempt in range(3):
            try:
                temporary.replace(destination)
                break
            except PermissionError:
                if attempt == 2:
                    raise
                time.sleep(0.25)
    except (csv.Error, sqlite3.Error, OSError) as exc:
        if connection is not None:
            connection.close()
        if temporary.exists():
            temporary.unlink()
        raise ValueError(f"Could not import CSV data: {exc}") from exc
    return destination

def render_analysis(state: dict, expanded: bool = False) -> None:
    st.write(state.get("final_summary", "No final summary generated."))
    with st.expander("Plan, SQL, and reasoning trail", expanded=expanded):
        for step in state.get("plan", []):
            st.markdown(f"**{step['id']}. {step['question']}** — {step['purpose']}")
        for query in state.get("queries", []):
            st.code(query["sql"], language="sql")
    results = {item["step_id"]: item for item in state.get("results", [])}
    insights = {item["step_id"]: item for item in state.get("insights", [])}
    figures = state.get("figures", {})
    queries = {item["step_id"]: item for item in state.get("queries", [])}
    for spec in state.get("chart_specs", []):
        result = results.get(spec["step_id"], {})
        st.markdown(f"#### {spec['title']}")
        if query := queries.get(spec["step_id"]):
            with st.expander("SQL used for this insight"):
                st.code(query["sql"], language="sql")
        figure_json = figures.get(str(spec["step_id"]), figures.get(spec["step_id"]))
        if figure_json and spec["chart_type"] != "table":
            st.plotly_chart(go.Figure(figure_json), width="stretch")
        else:
            st.dataframe(result.get("rows", []), width="stretch", hide_index=True)
        st.caption(spec["caption"])
        for finding in insights.get(spec["step_id"], {}).get("findings", []):
            st.write(f"• {finding['finding']}")
    if state.get("errors"):
        with st.expander("Diagnostics"):
            st.json(state["errors"])


def render_schema(profile: dict) -> None:
    st.subheader("Database schema and connections")
    tables = profile["tables"]
    st.caption(f"{len(tables)} table(s) loaded. Relationships labelled “inferred” use shared column names; they are not guaranteed foreign keys.")
    for table in tables:
        with st.expander(f"{table['name']} — {table['row_count']:,} rows"):
            st.dataframe(table["columns"], width="stretch", hide_index=True)
    links = profile["relationships"]
    if links:
        st.markdown("**Detected connections**")
        st.dataframe(links, width="stretch", hide_index=True)
        lines = ["digraph schema {", "rankdir=LR;", "node [shape=box style=rounded];"]
        for table in tables:
            lines.append(f'"{table["name"]}";')
        for link in links:
            label = f'{link["from_column"]} → {link["to_column"]} ({link["kind"]})'.replace('"', "'")
            lines.append(f'"{link["from_table"]}" -> "{link["to_table"]}" [label="{label}"];')
        lines.append("}")
        st.graphviz_chart("\n".join(lines))
    else:
        st.info("No declared or shared-column table relationships were detected.")


st.set_page_config(page_title="InsightPilot", page_icon="✦", layout="wide")
st.title("✦ InsightPilot")
st.caption("Load data first; InsightPilot profiles it automatically, then opens an analysis chat.")

st.subheader("1. Load and profile your data")
data_source = st.radio("Database source", ["Bundled tech-layoffs demo", "Upload my data"], horizontal=True)
uploaded_db = None
if data_source == "Upload my data":
    uploaded_db = st.file_uploader("SQLite database, CSV, or ZIP", type=["db", "sqlite", "sqlite3", "csv", "zip"])
load_data = st.button("Load data and generate overview", type="primary", width="stretch")

if load_data:
    if data_source == "Upload my data" and not uploaded_db:
        st.error("Upload a database, CSV, or ZIP file first.")
    else:
        try:
            db_path = save_uploaded_database(uploaded_db) if uploaded_db else setup_database()
            with st.spinner("Inspecting schema, mapping connections, and generating the complete data overview..."):
                profile = profile_database(db_path)
                overview = run(OVERVIEW_QUESTION, str(db_path))
            st.session_state.update({"active_db_path": str(db_path), "schema_profile": profile,
                                     "overview_state": overview, "chat_runs": []})
            RESULT_PATH.write_text(json.dumps(overview, indent=2, default=_json_default), encoding="utf-8")
            st.success(f"Loaded {Path(db_path).name}. The analysis chat is ready.")
        except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
            st.error(f"Data loading could not run: {exc}")

if "active_db_path" not in st.session_state:
    st.info("Choose your data source and click **Load data and generate overview**. The chat unlocks after profiling finishes.")
    st.stop()

render_schema(st.session_state["schema_profile"])
st.subheader("Automatic data overview")
render_analysis(st.session_state["overview_state"], expanded=False)

st.subheader("2. Ask InsightPilot")
st.caption("Ask follow-up questions about the loaded database. Each question runs the full validated analysis workflow.")
for item in st.session_state["chat_runs"]:
    with st.chat_message("user"):
        st.write(item["question"])
    with st.chat_message("assistant"):
        render_analysis(item["state"])

if question := st.chat_input("Ask about this database, for example: Which industry had the highest layoffs?"):
    with st.chat_message("user"):
        st.write(question)
    with st.chat_message("assistant"):
        with st.spinner("Running the validated InsightPilot workflow..."):
            answer = run(question, st.session_state["active_db_path"])
        render_analysis(answer)
    st.session_state["chat_runs"].append({"question": question, "state": answer})
    RESULT_PATH.write_text(json.dumps(answer, indent=2, default=_json_default), encoding="utf-8")
