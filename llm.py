"""Lazy, retrying model access for hosted providers and local Ollama."""
from __future__ import annotations

import logging
import os
import re
import time
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class OpenRouterSettings:
    """Private run configuration, never part of graph state or serialized results."""

    api_key: str = field(repr=False)
    model: str = "openrouter/free"

    def __post_init__(self):
        object.__setattr__(self, "api_key", self.api_key.strip())
        object.__setattr__(self, "model", self.model.strip())
        if not self.api_key:
            raise ValueError("Enter your OpenRouter API key.")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.:/-]+", self.model):
            raise ValueError("Enter an OpenRouter model ID, such as openrouter/free or provider/model-name.")


NVIDIA_MODEL = "nvidia/nemotron-3.5-lightning-30b-a3b"
NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"


@dataclass(frozen=True)
class NvidiaSettings:
    """Visitor-supplied NVIDIA API credentials for the Nemotron agent."""

    api_key: str = field(repr=False)
    model: str = NVIDIA_MODEL

    def __post_init__(self):
        object.__setattr__(self, "api_key", self.api_key.strip())
        if not self.api_key:
            raise ValueError("Enter your NVIDIA API key.")


ModelSettings = OpenRouterSettings | NvidiaSettings


@dataclass
class _RunModels:
    settings: ModelSettings
    stack: ExitStack = field(default_factory=ExitStack, repr=False)
    client: Any = field(default=None, repr=False)
    failure: str | None = None


_active_models: ContextVar[_RunModels | None] = ContextVar("insightpilot_models", default=None)


@contextmanager
def model_session(settings: ModelSettings | None):
    """ContextVars propagate through LangGraph workers without global credentials."""
    if settings is None:
        yield
        return
    active = _RunModels(settings)
    token = _active_models.set(active)
    try:
        yield
    finally:
        _active_models.reset(token)
        active.stack.close()
        active.client = None


def _openrouter_client(api_key: str, model: str, stack: ExitStack | None = None) -> Any:
    from langchain_openrouter import ChatOpenRouter
    from openrouter import OpenRouter

    timeout = int(os.getenv("INSIGHTPILOT_LLM_TIMEOUT", "90"))
    sdk = OpenRouter(api_key=api_key, timeout_ms=timeout * 1000, retry_config=None)
    if stack is not None:
        sdk = stack.enter_context(sdk)
    return ChatOpenRouter(
        model=model, api_key=api_key, temperature=0.2,
        max_tokens=int(os.getenv("INSIGHTPILOT_MAX_TOKENS", "2048")),
        timeout=timeout * 1000, max_retries=0, client=sdk,
    )


def _nvidia_client(api_key: str, model: str = NVIDIA_MODEL) -> Any:
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=model, api_key=api_key, base_url=NVIDIA_BASE_URL,
        temperature=1.0,
        max_tokens=int(os.getenv("INSIGHTPILOT_MAX_TOKENS", "2048")),
        timeout=int(os.getenv("INSIGHTPILOT_LLM_TIMEOUT", "90")),
        max_retries=0,
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
    )


def provider() -> str:
    return os.getenv("INSIGHTPILOT_LLM_PROVIDER", "ollama").strip().lower()


def validate_configuration() -> None:
    if provider() not in {"openrouter", "ollama", "nvidia"}:
        raise ValueError("INSIGHTPILOT_LLM_PROVIDER must be openrouter, nvidia, or ollama.")
    if provider() == "openrouter" and not os.getenv("OPENROUTER_API_KEY", "").strip():
        raise ValueError("Add OPENROUTER_API_KEY in the app's deployment secrets, then restart the app.")
    if provider() == "nvidia" and not os.getenv("NVIDIA_API_KEY", "").strip():
        raise ValueError("Set NVIDIA_API_KEY before running the CLI with NVIDIA.")
    for name, default in (("INSIGHTPILOT_LLM_TIMEOUT", "90"), ("INSIGHTPILOT_LLM_ATTEMPTS", "2"),
                          ("INSIGHTPILOT_MAX_TOKENS", "2048")):
        try:
            if int(os.getenv(name, default)) < 1:
                raise ValueError
        except ValueError:
            raise ValueError(f"{name} must be a positive integer.") from None


@lru_cache(maxsize=2)
def _default_client(role: str) -> Any:
    validate_configuration()
    timeout = int(os.getenv("INSIGHTPILOT_LLM_TIMEOUT", "90"))
    model_var = f"INSIGHTPILOT_{role.upper()}_MODEL"
    if provider() == "openrouter":
        return _openrouter_client(os.environ["OPENROUTER_API_KEY"], os.getenv(model_var, "openai/gpt-4.1-mini"))
    if provider() == "nvidia":
        return _nvidia_client(os.environ["NVIDIA_API_KEY"], os.getenv(model_var, NVIDIA_MODEL))
    from langchain_ollama import ChatOllama

    return ChatOllama(
        model=os.getenv(model_var, "qwen2.5-coder:7b"),
        base_url=os.getenv("OLLAMA_HOST", "http://localhost:11434"),
        temperature=0.2,
        num_ctx=int(os.getenv("INSIGHTPILOT_NUM_CTX", "4096")),
        client_kwargs={"timeout": timeout},
    )


def _client(role: str) -> Any:
    active = _active_models.get()
    if active is None:
        return _default_client(role)
    if active.client is None:
        if isinstance(active.settings, NvidiaSettings):
            active.client = _nvidia_client(active.settings.api_key, active.settings.model)
        else:
            active.client = _openrouter_client(active.settings.api_key, active.settings.model, active.stack)
    return active.client


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
    active = _active_models.get()
    if active is not None and active.failure:
        raise RuntimeError(active.failure)
    attempts = max(1, int(os.getenv("INSIGHTPILOT_LLM_ATTEMPTS", "2")))
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return llm.invoke(messages)
        except Exception as exc:
            last_error = exc
            # Provider exceptions can contain request data; never expose their payloads.
            LOGGER.warning("Model call %s/%s failed (%s)", attempt, attempts, type(exc).__name__)
            status = getattr(exc, "status_code", None)
            is_nvidia = active is not None and isinstance(active.settings, NvidiaSettings)
            permanent = ({
                400: "The NVIDIA model rejected this request. Check its tool-calling support and input.",
                401: "Your NVIDIA key was rejected. Replace it in Your model.",
                402: "Your NVIDIA account has insufficient credits.",
                403: "Your NVIDIA key does not have access to this model.",
                404: "The NVIDIA model is unavailable.",
                429: "NVIDIA's rate limit was reached. Wait before starting another analysis.",
            } if is_nvidia else {
                400: "The model rejected this request. Choose a model with tool-calling support.",
                401: "Your OpenRouter key was rejected. Replace it in Your model.",
                402: "Your OpenRouter account has insufficient credits. Choose the free model option or add credits.",
                403: "Your OpenRouter key does not have access to this model.",
                404: "This OpenRouter model is unavailable. Choose another model.",
                429: "OpenRouter's rate limit was reached. Wait before starting another analysis.",
            }).get(status)
            if permanent:
                if active is not None:
                    active.failure = permanent
                raise RuntimeError(permanent) from None
            if attempt < attempts:
                time.sleep(1.0)
    kind = type(last_error).__name__
    if "timeout" in kind.lower():
        message = (f"Model request timed out after {attempts} attempts. "
                   "Try again later or increase INSIGHTPILOT_LLM_TIMEOUT.")
    else:
        message = (f"Model request failed after {attempts} attempts ({kind}). "
                   "Check the provider credentials, model availability, quota, and connection.")
    if active is not None:
        active.failure = message  # Avoid repeating a failed hosted request for every graph step.
    raise RuntimeError(message) from None


def invoke_llm(llm: Any, prompt: str) -> str:
    content = invoke_message(llm, prompt).content
    if isinstance(content, str):
        return content
    return "".join(block.get("text", "") for block in content
                   if isinstance(block, dict) and block.get("type") == "text")
