# Deploy InsightPilot with OpenRouter

Target: Streamlit Community Cloud. No Ollama server or GPU is required.

## Publish

1. Push these changes to the existing GitHub repository `priyanshudeora/data-analysis-agent`.
2. Sign in at https://share.streamlit.io/ with the GitHub account that can access the repository.
3. Create an app from that repository, choose the branch containing these changes, and set the main file to `dashboard/app.py`.
4. In Advanced settings, choose Python 3.13 and paste the contents of `.streamlit/secrets.toml.example` into Secrets. Replace the placeholder with your own OpenRouter API key from https://openrouter.ai/settings/keys. Never put a real key in GitHub or chat.
5. Deploy, open the resulting app URL, and run the bundled demo overview to verify model access, SQL execution, and chart generation.

The configured model is `openai/gpt-4.1-mini` through OpenRouter. Change both model settings to use another OpenRouter model with tool-calling support. Model calls incur OpenRouter usage charges; use an OpenRouter key with a spending limit. Hosting does not include model credits. Restrict app access in Streamlit's sharing settings if you don't want everyone with the link to use your model budget.

## Local preview with the same setup

Copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml` and fill in your key locally. The real secrets file is excluded from Git.

```bash
pip install -r requirements.txt
streamlit run dashboard/app.py
```

For the CLI, set the same settings as environment variables; the CLI does not load Streamlit secrets. For local Ollama, explicitly set `INSIGHTPILOT_LLM_PROVIDER=ollama`. The CLI defaults to Ollama; the dashboard defaults to OpenRouter.

## Data and operational behavior

- Questions, schema, result samples (up to 50 rows per query), and derived findings are sent to OpenRouter and its selected model provider. Review your provider settings before uploading private data.
- Each dashboard session uses a separate temporary directory, including its demo database. Analysis history stays in that session rather than a shared `latest_run.json`. Session data is not durable and can disappear after a disconnect or server restart.
- SQL execution uses a read-only connection and an authorizer, a 10-second execution budget, and a 1,000-row result cap. These are basic resource bounds, not a full sandbox for hostile database files.
- Model requests use a 90-second timeout, two application-level attempts, and a 2,048-token output cap by default. The model's account quota and tool support must still be checked with a real key.
- Missing credentials show a setup message. Provider failures appear in Diagnostics without copying raw API response bodies or credentials.

## Local verification (no API charges)

```bash
python -m unittest discover -s tests -p "test_*.py"
python -m tests.smoke_nodes
python -m tests.smoke_llm_nodes
```

References: [Streamlit deployment](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy), [deployment secrets](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/secrets-management), [LangChain OpenRouter integration](https://docs.langchain.com/oss/python/integrations/chat/openrouter).
