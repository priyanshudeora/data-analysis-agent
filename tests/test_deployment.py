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
        llm._default_client.cache_clear()

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
    def new_app(self):
        from streamlit.testing.v1 import AppTest
        return AppTest.from_file(str(Path(__file__).resolve().parents[1] / "dashboard/app.py"), default_timeout=30).run()

    def configure(self, app, key):
        app.text_input(key="openrouter_api_key").set_value(key)
        next(button for button in app.button if button.label == "Use my key").click().run()
        self.assertFalse(app.exception)

    def test_owner_key_does_not_unlock_dashboard(self):
        with patch.dict(os.environ, {"INSIGHTPILOT_LLM_PROVIDER": "openrouter", "OPENROUTER_API_KEY": "owner-key"}):
            app = self.new_app()
            self.assertFalse(app.exception)
            self.assertFalse(app.error)
            self.assertTrue(app.button(key="load_data").disabled)
            self.assertEqual(app.text_input(key="openrouter_api_key").value, "")
            self.assertEqual(len(app.chat_input), 1)
            self.assertTrue(app.chat_input(key="chat_prompt").disabled)
            self.assertIn("Add your OpenRouter API key", app.chat_input(key="chat_prompt").placeholder)

    def test_apply_and_remove_key(self):
        with patch.dict(os.environ, {"INSIGHTPILOT_LLM_PROVIDER": "openrouter", "OPENROUTER_API_KEY": "owner-key"}), patch("llm._openrouter_client") as client:
            app = self.new_app()
            self.configure(app, "visitor-key")
            self.assertEqual(app.segmented_control(key="data_source").value, "Upload my data")
            self.assertTrue(app.button(key="load_data").disabled)
            self.assertTrue(app.chat_input(key="chat_prompt").disabled)
            self.assertIn("Load a dataset", app.chat_input(key="chat_prompt").placeholder)
            self.assertEqual(app.session_state["model_settings"].api_key, "visitor-key")
            self.assertEqual(app.session_state["model_settings"].model, "openrouter/free")
            self.assertEqual(app.text_input(key="openrouter_api_key").value, "")
            self.assertEqual(os.environ["OPENROUTER_API_KEY"], "owner-key")
            client.assert_not_called()  # Merely saving a key spends no credits.
            app.button(key="remove_api_key").click().run()
            self.assertFalse(app.exception)
            self.assertTrue(app.button(key="load_data").disabled)
            self.assertNotIn("model_settings", app.session_state)

    def test_custom_model_validation(self):
        with patch.dict(os.environ, {"INSIGHTPILOT_LLM_PROVIDER": "openrouter"}):
            app = self.new_app()
            app.selectbox(key="model_choice").select("Custom model").run()
            app.text_input(key="custom_model").set_value("bad model")
            self.configure(app, "visitor-key")
            self.assertTrue(app.error)
            self.assertTrue(app.button(key="load_data").disabled)
            app.text_input(key="custom_model").set_value("provider/tool-model")
            self.configure(app, "visitor-key")
            self.assertEqual(app.session_state["model_settings"].model, "provider/tool-model")

    def test_sessions_use_different_databases(self):
        with patch.dict(os.environ, {"INSIGHTPILOT_LLM_PROVIDER": "openrouter", "OPENROUTER_API_KEY": "owner-key"}), patch("main.run", return_value={"final_summary": "Test overview"}) as run:
            apps = [self.new_app() for _ in range(2)]
            try:
                for index, app in enumerate(apps):
                    self.configure(app, f"visitor-{index}")
                    app.segmented_control(key="data_source").select("Bundled tech-layoffs demo").run()
                    self.assertFalse(app.button(key="load_data").disabled)
                    app.button(key="load_data").click().run()
                    self.assertFalse(app.exception)
                    self.assertFalse(app.error)
                    self.assertFalse(app.chat_input(key="chat_prompt").disabled)
                    self.assertEqual(run.call_args.kwargs["model_settings"].api_key, f"visitor-{index}")
                self.assertNotEqual(apps[0].session_state["active_db_path"], apps[1].session_state["active_db_path"])
                apps[0].button(key="remove_api_key").click().run()
                self.assertTrue(apps[0].chat_input[0].disabled)
                self.assertEqual(apps[1].session_state["model_settings"].api_key, "visitor-1")
            finally:
                for app in apps:
                    app.session_state["data_directory"].cleanup()


class SessionCredentialTests(unittest.TestCase):
    def test_parallel_runs_isolate_credentials(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        barrier = Barrier(2)

        def build(key, model, stack):
            return SimpleNamespace(key=key, model=model)

        def worker(key):
            with llm.model_session(llm.OpenRouterSettings(api_key=key)):
                barrier.wait(timeout=10)
                return llm._client("coder").key, llm._client("reasoning").key

        with patch("llm._openrouter_client", side_effect=build), ThreadPoolExecutor(2) as pool:
            futures = [pool.submit(worker, key) for key in ("visitor-a", "visitor-b")]
            self.assertEqual([future.result() for future in futures], [("visitor-a", "visitor-a"), ("visitor-b", "visitor-b")])
        self.assertIsNone(llm._active_models.get())

    def test_langgraph_workers_receive_context_without_serializing_key(self):
        import main
        from langgraph.graph import StateGraph, START, END
        from state import AgentState
        from langchain_core.messages import AIMessage

        graph = StateGraph(AgentState)
        graph.add_node("check", lambda state: {"final_summary": llm.invoke_llm(llm.coder_llm, "test")})
        graph.add_edge(START, "check")
        graph.add_edge("check", END)
        settings = llm.OpenRouterSettings(api_key="private-visitor-key", model="provider/test-model")
        fake = SimpleNamespace(invoke=lambda _: AIMessage(content="Test summary"))
        with patch("main.insight_graph", graph.compile()), patch("llm._openrouter_client", return_value=fake) as factory:
            result = main.run("test", "unused.db", model_settings=settings)
        self.assertEqual(result["final_summary"], "Test summary")
        self.assertEqual(factory.call_args.args[:2], ("private-visitor-key", "provider/test-model"))
        self.assertNotIn("private-visitor-key", repr(result))
        self.assertNotIn("private-visitor-key", repr(settings))
        self.assertIsNone(llm._active_models.get())

    def test_quota_error_stops_remaining_requests(self):
        from unittest.mock import Mock
        error = RuntimeError("private error body")
        error.status_code = 402
        fake = Mock()
        fake.invoke.side_effect = error
        with llm.model_session(llm.OpenRouterSettings(api_key="visitor-key")):
            for _ in range(2):
                with self.assertRaisesRegex(RuntimeError, "insufficient credits"):
                    llm.invoke_message(fake, "test")
        self.assertEqual(fake.invoke.call_count, 1)

    def test_exception_cleans_up_context(self):
        with self.assertRaisesRegex(RuntimeError, "test failure"):
            with llm.model_session(llm.OpenRouterSettings(api_key="visitor-key")):
                raise RuntimeError("test failure")
        self.assertIsNone(llm._active_models.get())


if __name__ == "__main__":
    unittest.main()
