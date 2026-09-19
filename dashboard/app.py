"""Streamlit presentation layer for a saved InsightPilot graph state."""
from __future__ import annotations

import hashlib
import sys
import io
import zipfile
import csv
import re
import sqlite3
import time
import os
import tempfile
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
from llm import OpenRouterSettings, validate_configuration

MAX_UPLOAD_BYTES = 100 * 1024 * 1024
MAX_UNZIPPED_BYTES = 250 * 1024 * 1024
OVERVIEW_QUESTION = (
    "Provide a comprehensive overview of this database: identify the main entities, "
    "record distributions, meaningful trends, notable outliers, and the most useful starting insights."
)


def apply_model_settings() -> None:
    """Callback: update only this session; never put visitor keys in os.environ."""
    previous = st.session_state.get("model_settings")
    key = st.session_state.get("openrouter_api_key", "").strip()
    if not key and previous:
        key = previous.api_key
    model = ("openrouter/free" if st.session_state.get("model_choice") == "Free models"
             else st.session_state.get("custom_model", ""))
    try:
        st.session_state["model_settings"] = OpenRouterSettings(api_key=key, model=model)
        st.session_state.pop("model_error", None)
        st.session_state["openrouter_api_key"] = ""
    except ValueError as exc:
        st.session_state.pop("model_settings", None)
        st.session_state["model_error"] = str(exc)


def remove_api_key() -> None:
    st.session_state.pop("model_settings", None)
    st.session_state.pop("model_error", None)
    st.session_state["openrouter_api_key"] = ""


def session_directory() -> Path:
    """Each browser session owns its files; cleaned up when its state is released."""
    if "data_directory" not in st.session_state:
        st.session_state["data_directory"] = tempfile.TemporaryDirectory(prefix="insightpilot-")
    return Path(st.session_state["data_directory"].name)


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
    digest = hashlib.sha256(content).hexdigest()[:12]
    destination = session_directory() / f"{digest}.db"
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
    destination = session_directory() / f"{digest}-csv-import.db"
    temporary = session_directory() / f"{digest}-csv-import.tmp"
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
    if any(result.get("truncated") for result in state.get("results", [])):
        st.warning("Some query results were limited to 1,000 rows. Charts show only those rows.")
    if any(len(result.get("rows", [])) > 50 for result in state.get("results", [])):
        st.caption("Narrative findings use the first 50 rows of each query result; they may not describe the complete dataset.")
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
st.session_state.setdefault("chat_runs", [])
st.title("✦ InsightPilot")
st.caption("Load data first; InsightPilot profiles it automatically, then opens an analysis chat.")

# Root-level Streamlit secrets are exported as environment variables on access.
try:
    st.secrets.to_dict()
except FileNotFoundError:
    pass
dashboard_provider = os.getenv("INSIGHTPILOT_LLM_PROVIDER", "openrouter").strip().lower()
model_settings = None
if dashboard_provider == "ollama":
    try:
        validate_configuration()
    except ValueError as exc:
        st.error(str(exc))
        st.stop()
    model_ready = True
    st.caption("Using the local Ollama model configured by the app owner.")
else:
    st.subheader("Your model")
    st.caption("Use your own OpenRouter key. All model usage belongs to your account; the app owner's credits are never used.")
    st.selectbox("Model option", ["Free models", "Custom model"], key="model_choice")
    with st.form("model_credentials"):
        st.text_input("OpenRouter API key", type="password", key="openrouter_api_key",
                      placeholder="Paste your key, or leave blank to keep your current key")
        if st.session_state["model_choice"] == "Custom model":
            st.text_input("OpenRouter model ID", key="custom_model", placeholder="provider/model-name")
            st.caption("Choose a model with tool-calling support. Paid models charge your OpenRouter account.")
        else:
            st.caption("Uses openrouter/free. Free models have rate limits and availability can vary.")
        st.form_submit_button("Use my key", on_click=apply_model_settings, type="primary")
    if st.session_state.get("model_error"):
        st.error(st.session_state["model_error"])
    model_settings = st.session_state.get("model_settings")
    model_ready = model_settings is not None
    if model_ready:
        st.success(f"Ready to use {model_settings.model}. Your key is checked when you start an analysis.")
        st.button("Remove my key", key="remove_api_key", on_click=remove_api_key)
    else:
        st.info("Add your API key above to start an analysis.")
    st.caption("Your key is held in this browser session's server memory, never saved to a file. Remove it when finished. Questions, schema, result samples, and findings are sent through OpenRouter to the model provider.")
    st.markdown("[Get an OpenRouter key](https://openrouter.ai/settings/keys) · [Free model details](https://openrouter.ai/openrouter/free)")

st.subheader("1. Load and profile your data")
data_source = st.radio("Database source", ["Bundled tech-layoffs demo", "Upload my data"], horizontal=True)
uploaded_db = None
if data_source == "Upload my data":
    uploaded_db = st.file_uploader("SQLite database, CSV, or ZIP", type=["db", "sqlite", "sqlite3", "csv", "zip"])
load_data = st.button("Load data and generate overview", type="primary", width="stretch", key="load_data", disabled=not model_ready)

if load_data:
    if not model_ready:
        st.error("Add your own API key before starting an analysis.")
        st.stop()
    if data_source == "Upload my data" and not uploaded_db:
        st.error("Upload a database, CSV, or ZIP file first.")
    else:
        try:
            db_path = save_uploaded_database(uploaded_db) if uploaded_db else setup_database(session_directory() / "demo.db")
            with st.spinner("Inspecting schema, mapping connections, and generating the complete data overview..."):
                profile = profile_database(db_path)
                overview = run(OVERVIEW_QUESTION, str(db_path), model_settings=model_settings)
            st.session_state.update({"active_db_path": str(db_path), "schema_profile": profile,
                                     "overview_state": overview, "chat_runs": []})
            st.success(f"Loaded {Path(db_path).name}. The analysis chat is ready.")
        except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
            st.error(f"Data loading could not run: {exc}")

data_ready = "active_db_path" in st.session_state
if data_ready:
    render_schema(st.session_state["schema_profile"])
    st.subheader("Automatic data overview")
    render_analysis(st.session_state["overview_state"], expanded=False)
else:
    st.info("Choose your data source and click **Load data and generate overview**. You can see the chat below; it becomes available after data is loaded.")

st.subheader("2. Ask InsightPilot")
if not model_ready:
    st.info("Add your OpenRouter API key above to enable chat.")
elif not data_ready:
    st.info("Load and profile a dataset above to enable chat.")
else:
    st.caption("Ask follow-up questions about the loaded database. Each question runs the full validated analysis workflow.")

for item in st.session_state.get("chat_runs", []):
    with st.chat_message("user"):
        st.write(item["question"])
    with st.chat_message("assistant"):
        render_analysis(item["state"])

chat_disabled = not (model_ready and data_ready)
if not model_ready:
    chat_placeholder = "Add your OpenRouter API key to start chatting"
elif not data_ready:
    chat_placeholder = "Load a dataset to start chatting"
else:
    chat_placeholder = "Ask about this database, for example: Which industry had the highest layoffs?"

if question := st.chat_input(chat_placeholder, disabled=chat_disabled, key="chat_prompt", submit_mode="disable"):
    with st.chat_message("user"):
        st.write(question)
    answer = None
    with st.chat_message("assistant"):
        try:
            with st.spinner("Running the validated InsightPilot workflow..."):
                answer = run(question, st.session_state["active_db_path"], model_settings=model_settings)
            render_analysis(answer)
        except (OSError, RuntimeError, ValueError, sqlite3.Error):
            st.error("Analysis could not finish. Check the model configuration and retry.")
    if answer is not None:
        st.session_state["chat_runs"].append({"question": question, "state": answer})
