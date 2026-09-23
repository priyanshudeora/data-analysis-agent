FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HOME=/tmp

WORKDIR /app

RUN apt-get update \
    && apt-get install --no-install-recommends -y curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system insightpilot \
    && useradd --system --gid insightpilot --home-dir /tmp insightpilot

COPY requirements.txt ./
RUN python -m pip install --upgrade pip \
    && python -m pip install -r requirements.txt

COPY .streamlit/ .streamlit/
COPY dashboard/ dashboard/
COPY db/ db/
COPY nodes/ nodes/
COPY tools/ tools/
COPY graph.py llm.py main.py prompts.py state.py ./

RUN chown -R insightpilot:insightpilot /app
USER insightpilot

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl --fail --silent http://127.0.0.1:8501/_stcore/health || exit 1

CMD ["streamlit", "run", "dashboard/app.py", "--server.address=0.0.0.0", "--server.port=8501"]
