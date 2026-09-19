# Deploy InsightPilot with OpenRouter

Target: Streamlit Community Cloud. No Ollama server or GPU is required.

## Publish

1. Push these changes to the existing GitHub repository `priyanshudeora/data-analysis-agent`.
2. Sign in at https://share.streamlit.io/ with the GitHub account that can access the repository.
3. Create an app from that repository, choose the branch containing these changes, and set the main file to `dashboard/app.py`.
4. In Advanced settings, choose Python 3.13. No owner API key is required. Optional timeout and token settings are shown in `.streamlit/secrets.toml.example`.
5. Deploy and open the resulting app URL. Under **Your model**, paste your own OpenRouter API key, choose **Free models** or enter a custom model ID, and click **Use my key**. Run the bundled demo overview to verify model access, SQL execution, and chart generation.

Each visitor provides their own key from https://openrouter.ai/settings/keys. The dashboard never falls back to an owner's `OPENROUTER_API_KEY`, even if one exists in deployment secrets. No model requests are sent just for entering or applying a key.

The default **Free models** option uses `openrouter/free`. Free models have rate limits and variable availability; they are not unlimited. Custom models must support tool calling and can charge the visitor's OpenRouter account. Select **Remove my key** to discard credentials and disable further analysis. Previously generated results remain visible.

## Local preview with the same setup

Start the app and enter your key in its **Your model** form. No secrets file is required.

```bash
pip install -r requirements.txt
streamlit run dashboard/app.py
```

For the CLI, set `INSIGHTPILOT_LLM_PROVIDER=openrouter`, `OPENROUTER_API_KEY`, and optionally `INSIGHTPILOT_CODER_MODEL` / `INSIGHTPILOT_REASONING_MODEL` as environment variables; the CLI does not load Streamlit secrets or use browser keys. For local Ollama, explicitly set `INSIGHTPILOT_LLM_PROVIDER=ollama`. The CLI defaults to Ollama; the dashboard defaults to visitor-supplied OpenRouter credentials.

## Data and operational behavior

- Questions, schema, result samples (up to 50 rows per query), and derived findings are sent to OpenRouter and its selected model provider. Review your provider settings before uploading private data.
- Each dashboard session uses a separate temporary directory, including its demo database. Analysis history stays in that session rather than a shared `latest_run.json`. Session data is not durable and can disappear after a disconnect or server restart.
- Visitor keys live in their Streamlit session's server memory, not in environment variables, shared caches, graph state, saved results, or files. Per-run model clients are closed after the graph finishes. A key is sent to OpenRouter to authenticate requests. Deploy with HTTPS (provided by Streamlit Community Cloud); password masking alone does not encrypt transport. Use **Remove my key** to clear it immediately; session cleanup timing after disconnection is managed by Streamlit.
- SQL execution uses a read-only connection and an authorizer, a 10-second execution budget, and a 1,000-row result cap. These are basic resource bounds, not a full sandbox for hostile database files.
- Model requests use a 90-second timeout, two application-level attempts, and a 2,048-token output cap by default. The model's account quota and tool support must still be checked with a real key.
- Missing visitor credentials disable analysis. Authentication, credit, access, unavailable-model, and rate-limit errors stop further model requests in the current run and appear in Diagnostics without raw API response bodies or credentials. A visitor must explicitly start another analysis to try again.

## Local verification (no API charges)

```bash
python -m unittest discover -s tests -p "test_*.py"
python -m tests.smoke_nodes
python -m tests.smoke_llm_nodes
```

References: [Streamlit deployment](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy), [deployment secrets](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/secrets-management), [LangChain OpenRouter integration](https://docs.langchain.com/oss/python/integrations/chat/openrouter).
