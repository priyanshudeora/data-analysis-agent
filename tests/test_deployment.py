"""Cloud configuration and database boundaries; no live model requests."""
from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import llm
from nodes.query_executor import query_executor


class ModelConfigurationTests(unittest.TestCase):
    def tearDown(self):
        llm._client.cache_clear()

    def test_openrouter_client_and_tools(self):
        with patch.dict(os.environ, {"INSIGHTPILOT_LLM_PROVIDER": "openrouter", "OPENROUTER_API_KEY": "test-only", "INSIGHTPILOT_LLM_TIMEOUT": "90"}):
            client = llm._client("coder")
            self.assertEqual(client.model, "openai/gpt-4.1-mini")
            self.assertEqual(client.request_timeout, 90000)
            self.assertEqual(client.max_retries, 0)
            from tools.chart_tools import CHART_TOOLS
            self.assertIsNotNone(client.bind_tools(CHART_TOOLS))

    def test_missing_key_is_actionable(self):
        with patch.dict(os.environ, {"INSIGHTPILOT_LLM_PROVIDER": "openrouter", "OPENROUTER_API_KEY": ""}):
            with self.assertRaisesRegex(ValueError, "OPENROUTER_API_KEY"):
                llm.validate_configuration()

    def test_provider_error_does_not_expose_payload(self):
        fake = SimpleNamespace(invoke=lambda _: (_ for _ in ()).throw(ValueError("secret-key-and-private-data")))
        with patch.dict(os.environ, {"INSIGHTPILOT_LLM_ATTEMPTS": "1"}):
            with self.assertRaises(RuntimeError) as caught:
                llm.invoke_message(fake, "test")
            self.assertNotIn("secret-key-and-private-data", str(caught.exception))

    def test_text_blocks(self):
        fake = SimpleNamespace(invoke=lambda _: SimpleNamespace(content=[{"type": "text", "text": "{}"}]))
        self.assertEqual(llm.invoke_llm(fake, "test"), "{}")


class QueryBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "source.db"
        connection = sqlite3.connect(self.path)
        connection.execute("CREATE TABLE records (value INTEGER)")
        connection.executemany("INSERT INTO records VALUES (?)", [(i,) for i in range(1010)])
        connection.commit()
        connection.close()

    def tearDown(self):
        self.directory.cleanup()

    def execute(self, sql):
        return query_executor({"db_path": str(self.path), "plan": [{"id": 1}],
            "queries": [{"step_id": 1, "sql": sql}]})["results"][0]

    def test_read_and_cap(self):
        result = self.execute("WITH subset AS (SELECT * FROM records) SELECT * FROM subset")
        self.assertIsNone(result["error"])
        self.assertEqual(len(result["rows"]), 1000)
        self.assertTrue(result["truncated"])

    def test_cte_write_rejected(self):
        self.assertTrue(self.execute("WITH x AS (SELECT 1) DELETE FROM records")["error"])
        self.assertEqual(self.execute("SELECT COUNT(*) AS count FROM records")["rows"][0]["count"], 1010)

    def test_attach_rejected(self):
        self.assertTrue(self.execute("ATTACH ':memory:' AS other")["error"])

    def test_query_deadline(self):
        with patch("nodes.query_executor.QUERY_TIMEOUT_SECONDS", -1):
            self.assertTrue(self.execute("WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM n) SELECT sum(x) FROM n")["error"])


class DashboardTests(unittest.TestCase):
    def test_missing_key_renders_setup(self):
        from streamlit.testing.v1 import AppTest
        with patch.dict(os.environ, {"INSIGHTPILOT_LLM_PROVIDER": "openrouter", "OPENROUTER_API_KEY": ""}):
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "dashboard/app.py"), default_timeout=30).run()
            self.assertFalse(app.exception)
            self.assertIn("OPENROUTER_API_KEY", app.error[0].value)

    def test_sessions_use_different_databases(self):
        from streamlit.testing.v1 import AppTest
        with patch.dict(os.environ, {"INSIGHTPILOT_LLM_PROVIDER": "openrouter", "OPENROUTER_API_KEY": "test-only"}), patch("main.run", return_value={"final_summary": "Test overview"}):
            apps = [AppTest.from_file(str(Path(__file__).resolve().parents[1] / "dashboard/app.py"), default_timeout=30).run() for _ in range(2)]
            try:
                for app in apps:
                    app.button[0].click().run()
                    self.assertFalse(app.exception)
                    self.assertFalse(app.error)
                self.assertNotEqual(apps[0].session_state["active_db_path"], apps[1].session_state["active_db_path"])
            finally:
                for app in apps:
                    app.session_state["data_directory"].cleanup()


if __name__ == "__main__":
    unittest.main()
