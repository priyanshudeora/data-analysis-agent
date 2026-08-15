"""One sequential, retrying local-LLM access point for all graph nodes."""
from __future__ import annotations

import logging
import os
import time
from typing import Any

from langchain_ollama import ChatOllama

LOGGER = logging.getLogger(__name__)
OLLAMA_TIMEOUT_SECONDS = float(os.getenv("INSIGHTPILOT_LLM_TIMEOUT", "90"))
MAX_LLM_ATTEMPTS = int(os.getenv("INSIGHTPILOT_LLM_ATTEMPTS", "2"))

# Keep contexts bounded for a 6 GB GPU. Set both models equal to run just one pull.
coder_llm = ChatOllama(
    model=os.getenv("INSIGHTPILOT_CODER_MODEL", "qwen2.5-coder:7b"),
    temperature=0.2,
    num_ctx=int(os.getenv("INSIGHTPILOT_NUM_CTX", "4096")),
    timeout=OLLAMA_TIMEOUT_SECONDS,
)
reasoning_llm = ChatOllama(
    model=os.getenv("INSIGHTPILOT_REASONING_MODEL", "qwen2.5-coder:7b"),
    temperature=0.2,
    num_ctx=int(os.getenv("INSIGHTPILOT_NUM_CTX", "4096")),
    timeout=OLLAMA_TIMEOUT_SECONDS,
)


def invoke_llm(llm: ChatOllama, prompt: str) -> str:
    """Invoke synchronously with bounded retries; LangGraph calls nodes sequentially."""
    last_error: Exception | None = None
    for attempt in range(1, MAX_LLM_ATTEMPTS + 1):
        try:
            response: Any = llm.invoke(prompt)
            return str(response.content)
        except Exception as exc:  # the Ollama HTTP client exposes several exception types
            last_error = exc
            LOGGER.warning("Local LLM attempt %s/%s failed: %s", attempt, MAX_LLM_ATTEMPTS, exc)
            if attempt < MAX_LLM_ATTEMPTS:
                time.sleep(1.0)
    raise RuntimeError(f"Local LLM unavailable after {MAX_LLM_ATTEMPTS} attempts: {last_error}") from last_error


def invoke_message(llm: Any, messages: Any) -> Any:
    """Retry a message/tool-call invocation without discarding tool-call metadata."""
    last_error: Exception | None = None
    for attempt in range(1, MAX_LLM_ATTEMPTS + 1):
        try:
            return llm.invoke(messages)
        except Exception as exc:
            last_error = exc
            LOGGER.warning("Local tool-call attempt %s/%s failed: %s", attempt, MAX_LLM_ATTEMPTS, exc)
            if attempt < MAX_LLM_ATTEMPTS:
                time.sleep(1.0)
    raise RuntimeError(f"Local LLM unavailable after {MAX_LLM_ATTEMPTS} attempts: {last_error}") from last_error
