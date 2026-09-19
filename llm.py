"""Lazy, retrying model access for OpenRouter hosting and local Ollama."""
from __future__ import annotations

import logging
import os
import time
from functools import lru_cache
from typing import Any

LOGGER = logging.getLogger(__name__)


def provider() -> str:
    return os.getenv("INSIGHTPILOT_LLM_PROVIDER", "ollama").strip().lower()


def validate_configuration() -> None:
    if provider() not in {"openrouter", "ollama"}:
        raise ValueError("INSIGHTPILOT_LLM_PROVIDER must be openrouter or ollama.")
    if provider() == "openrouter" and not os.getenv("OPENROUTER_API_KEY", "").strip():
        raise ValueError("Add OPENROUTER_API_KEY in the app's deployment secrets, then restart the app.")
    for name, default in (("INSIGHTPILOT_LLM_TIMEOUT", "90"), ("INSIGHTPILOT_LLM_ATTEMPTS", "2"),
                          ("INSIGHTPILOT_MAX_TOKENS", "2048")):
        try:
            if int(os.getenv(name, default)) < 1:
                raise ValueError
        except ValueError:
            raise ValueError(f"{name} must be a positive integer.") from None


@lru_cache(maxsize=2)
def _client(role: str) -> Any:
    validate_configuration()
    timeout = int(os.getenv("INSIGHTPILOT_LLM_TIMEOUT", "90"))
    model_var = f"INSIGHTPILOT_{role.upper()}_MODEL"
    if provider() == "openrouter":
        from langchain_openrouter import ChatOpenRouter
        from openrouter import OpenRouter

        return ChatOpenRouter(
            model=os.getenv(model_var, "openai/gpt-4.1-mini"),
            api_key=os.environ["OPENROUTER_API_KEY"],
            temperature=0.2,
            max_tokens=int(os.getenv("INSIGHTPILOT_MAX_TOKENS", "2048")),
            timeout=timeout * 1000,  # OpenRouter's SDK uses milliseconds.
            max_retries=0,
            # Explicitly disable SDK defaults; max_retries=0 alone leaves them on.
            client=OpenRouter(api_key=os.environ["OPENROUTER_API_KEY"],
                              timeout_ms=timeout * 1000, retry_config=None),
        )
    from langchain_ollama import ChatOllama

    return ChatOllama(
        model=os.getenv(model_var, "qwen2.5-coder:7b"),
        base_url=os.getenv("OLLAMA_HOST", "http://localhost:11434"),
        temperature=0.2,
        num_ctx=int(os.getenv("INSIGHTPILOT_NUM_CTX", "4096")),
        client_kwargs={"timeout": timeout},
    )


class LazyModel:
    """Keep imports and the setup screen usable before secrets are configured."""

    def __init__(self, role: str):
        self.role = role

    def invoke(self, messages: Any) -> Any:
        return _client(self.role).invoke(messages)

    def bind_tools(self, tools: Any) -> Any:
        return _client(self.role).bind_tools(tools)


coder_llm = LazyModel("coder")
reasoning_llm = LazyModel("reasoning")


def invoke_message(llm: Any, messages: Any) -> Any:
    attempts = max(1, int(os.getenv("INSIGHTPILOT_LLM_ATTEMPTS", "2")))
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return llm.invoke(messages)
        except Exception as exc:
            last_error = exc
            # Provider exceptions can contain request data; never expose their payloads.
            LOGGER.warning("Model call %s/%s failed (%s)", attempt, attempts, type(exc).__name__)
            if attempt < attempts:
                time.sleep(1.0)
    kind = type(last_error).__name__
    raise RuntimeError(
        f"Model request failed after {attempts} attempts ({kind}). "
        "Check the provider credentials, model availability, quota, and connection."
    ) from None


def invoke_llm(llm: Any, prompt: str) -> str:
    content = invoke_message(llm, prompt).content
    if isinstance(content, str):
        return content
    return "".join(block.get("text", "") for block in content
                   if isinstance(block, dict) and block.get("type") == "text")
