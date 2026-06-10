"""Single call() abstraction across OpenRouter and Ollama Cloud.

Why not route through LiteLLM:
  - LiteLLM only knows the ~20 models in litellm_config.yaml. The whole point of
    the evaluator is to test models we DON'T have aliased yet so we can decide
    whether to add them. Going around LiteLLM keeps the eval surface independent
    from the production routing surface.

Provider adapter contract (v0.3):
  New providers implement two functions and register their source name in
  ADAPTER_SOURCES. No changes to eval_runner.py or eval_prompts.py needed.

  call(model_id, source, user_prompt, system_prompt, max_tokens) -> CallResult
  call_with_tools(model_id, source, user_prompt, tools) -> ToolCallResult

  See ProviderAdapter Protocol below for the typed contract.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

import requests

logger = logging.getLogger("llm_curator.eval_providers")

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
# Module-level constants kept as documented defaults; runtime reads via _get_setting().
OLLAMA_LOCAL_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")


def _get_setting(db_key: str, env_var: str, default: str = "") -> str:
    """Read a config value: curator_settings DB first, env var fallback, then default.

    Called at request time (not import time) so UI-written keys take effect immediately
    without restarting the container.
    """
    try:
        from llm_curator.db import cursor as _cursor  # local import avoids circular dep
        with _cursor() as cur:
            cur.execute("SELECT value FROM curator_settings WHERE key = %s", (db_key,))
            row = cur.fetchone()
            if row and row["value"]:
                return row["value"]
    except Exception:
        pass  # DB unavailable (e.g. migration not yet run) — fall back gracefully
    return os.getenv(env_var, default)

# Conservative timeouts — keep one eval prompt under 90s
TIMEOUT_S = 90

# Sources with a registered adapter implementation.
# Add a new source string here when you wire up a new provider.
ADAPTER_SOURCES = ("openrouter", "ollama-cloud")


# ── Result types ───────────────────────────────────────────────────────────────

@dataclass
class CallResult:
    output: str
    tokens_input: int | None
    tokens_output: int | None
    latency_ms: int
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass
class ToolCallResult:
    """Result from call_with_tools(). output is JSON: {"name": "...", "arguments": {...}}."""
    output: str                       # serialised tool call; "" when no tool call produced
    function_name: str | None
    arguments: dict[str, Any] | None
    tokens_input: int | None
    tokens_output: int | None
    latency_ms: int
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    def to_call_result(self) -> CallResult:
        """Bridge to CallResult so record_eval() works unchanged for tool_use rows."""
        return CallResult(
            output=self.output,
            tokens_input=self.tokens_input,
            tokens_output=self.tokens_output,
            latency_ms=self.latency_ms,
            error=self.error,
        )


# ── Provider adapter Protocol ──────────────────────────────────────────────────

@runtime_checkable
class ProviderAdapter(Protocol):
    """Typed contract for adding new LLM providers.

    To add a provider:
      1. Create a class implementing both methods below.
      2. Register its source string in ADAPTER_SOURCES.
      3. Add dispatch cases in call() and call_with_tools().

    call_with_tools is optional — return ToolCallResult with
    error="tool_use_unsupported" if the provider does not support function calling.
    """

    def call(
        self,
        model_id: str,
        user_prompt: str,
        system_prompt: str | None,
        max_tokens: int,
    ) -> CallResult: ...

    def call_with_tools(
        self,
        model_id: str,
        user_prompt: str,
        tools: list[dict[str, Any]],
    ) -> ToolCallResult: ...


# ── Public dispatch ────────────────────────────────────────────────────────────

def call(
    model_id: str,
    source: str,
    user_prompt: str,
    system_prompt: str | None = None,
    max_tokens: int = 512,
) -> CallResult:
    """Dispatch to the correct provider based on source."""
    if source == "openrouter":
        return _call_openrouter(model_id, user_prompt, system_prompt, max_tokens)
    if source == "ollama-cloud":
        return _call_ollama(model_id, user_prompt, system_prompt, max_tokens)
    return CallResult("", None, None, 0, error=f"unknown source: {source}")


def call_with_tools(
    model_id: str,
    source: str,
    user_prompt: str,
    tools: list[dict[str, Any]],
) -> ToolCallResult:
    """Send a function-calling request and return the parsed tool call.

    Only OpenRouter is supported; Ollama Cloud does not reliably support the
    tool-calling API format and returns an unsupported error immediately.
    """
    if source == "openrouter":
        return _call_openrouter_tools(model_id, user_prompt, tools)
    return ToolCallResult(
        "", None, None, None, None, 0,
        error=f"tool_use_unsupported: source '{source}' does not support function calling",
    )


def _call_openrouter(
    model_id: str,
    user_prompt: str,
    system_prompt: str | None,
    max_tokens: int,
) -> CallResult:
    key = _get_setting("openrouter.api_key", "OPENROUTER_API_KEY")
    if not key:
        return CallResult("", None, None, 0, error="OPENROUTER_API_KEY not set")
    messages: list[dict[str, Any]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": user_prompt})
    payload = {"model": model_id, "messages": messages, "max_tokens": max_tokens}
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/ksriram20/llm-curator",
        "X-Title": "llm-curator",
    }
    start = time.monotonic()
    try:
        resp = requests.post(OPENROUTER_URL, json=payload, headers=headers, timeout=TIMEOUT_S)
        latency_ms = int((time.monotonic() - start) * 1000)
        if resp.status_code != 200:
            return CallResult("", None, None, latency_ms,
                              error=f"HTTP {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        choices = data.get("choices") or []
        if not choices:
            return CallResult("", None, None, latency_ms,
                              error=f"no choices in response: {str(data)[:300]}")
        output = (choices[0].get("message") or {}).get("content") or ""
        usage = data.get("usage") or {}
        return CallResult(
            output=output,
            tokens_input=usage.get("prompt_tokens"),
            tokens_output=usage.get("completion_tokens"),
            latency_ms=latency_ms,
        )
    except requests.RequestException as e:
        return CallResult("", None, None, int((time.monotonic() - start) * 1000),
                          error=f"request error: {e}")


def _call_ollama(
    model_id: str,
    user_prompt: str,
    system_prompt: str | None,
    max_tokens: int,
) -> CallResult:
    """Local Ollama relays cloud-tagged models to Ollama Cloud."""
    base_url = _get_setting("ollama.base_url", "OLLAMA_URL", "http://localhost:11434")
    url = f"{base_url.rstrip('/')}/api/generate"
    full_prompt = user_prompt
    if system_prompt:
        # Ollama /api/generate doesn't have a system slot like /api/chat does;
        # prepend the system instruction explicitly.
        full_prompt = f"{system_prompt}\n\n{user_prompt}"
    payload = {
        "model": model_id,
        "prompt": full_prompt,
        "stream": False,
        "options": {"num_predict": max_tokens},
    }
    start = time.monotonic()
    try:
        resp = requests.post(url, json=payload, timeout=TIMEOUT_S)
        latency_ms = int((time.monotonic() - start) * 1000)
        if resp.status_code != 200:
            return CallResult("", None, None, latency_ms,
                              error=f"HTTP {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        return CallResult(
            output=data.get("response", ""),
            tokens_input=data.get("prompt_eval_count"),
            tokens_output=data.get("eval_count"),
            latency_ms=latency_ms,
        )
    except requests.RequestException as e:
        return CallResult("", None, None, int((time.monotonic() - start) * 1000),
                          error=f"request error: {e}")


def _call_openrouter_tools(
    model_id: str,
    user_prompt: str,
    tools: list[dict[str, Any]],
) -> ToolCallResult:
    key = _get_setting("openrouter.api_key", "OPENROUTER_API_KEY")
    if not key:
        return ToolCallResult("", None, None, None, None, 0,
                              error="OPENROUTER_API_KEY not set")
    payload = {
        "model": model_id,
        "messages": [{"role": "user", "content": user_prompt}],
        "tools": tools,
        "tool_choice": "auto",
    }
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/ksriram20/llm-curator",
        "X-Title": "llm-curator",
    }
    start = time.monotonic()
    try:
        resp = requests.post(OPENROUTER_URL, json=payload, headers=headers, timeout=TIMEOUT_S)
        latency_ms = int((time.monotonic() - start) * 1000)
        usage = {}
        if resp.status_code != 200:
            return ToolCallResult("", None, None, None, None, latency_ms,
                                  error=f"HTTP {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        usage = data.get("usage") or {}
        tokens_in = usage.get("prompt_tokens")
        tokens_out = usage.get("completion_tokens")
        choices = data.get("choices") or []
        if not choices:
            return ToolCallResult("", None, None, tokens_in, tokens_out, latency_ms,
                                  error=f"no choices in response: {str(data)[:200]}")
        msg = choices[0].get("message") or {}
        tool_calls = msg.get("tool_calls") or []
        if not tool_calls:
            return ToolCallResult("", None, None, tokens_in, tokens_out, latency_ms,
                                  error="no_tool_call_in_response")
        tc = tool_calls[0]
        fn = tc.get("function") or {}
        fn_name = fn.get("name", "")
        raw_args = fn.get("arguments", "{}")
        args: dict[str, Any] = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
        output = json.dumps({"name": fn_name, "arguments": args})
        return ToolCallResult(
            output=output,
            function_name=fn_name,
            arguments=args,
            tokens_input=tokens_in,
            tokens_output=tokens_out,
            latency_ms=latency_ms,
        )
    except json.JSONDecodeError as e:
        return ToolCallResult("", None, None, None, None,
                              int((time.monotonic() - start) * 1000),
                              error=f"json parse error: {e}")
    except requests.RequestException as e:
        return ToolCallResult("", None, None, None, None,
                              int((time.monotonic() - start) * 1000),
                              error=f"request error: {e}")


def estimate_cost_usd(
    pricing_input: float | None,
    pricing_output: float | None,
    tokens_input: int | None,
    tokens_output: int | None,
) -> float | None:
    """Compute call cost given per-token prices. Returns None if anything unknown."""
    if pricing_input is None or pricing_output is None:
        return None
    if tokens_input is None or tokens_output is None:
        return None
    return float(pricing_input) * tokens_input + float(pricing_output) * tokens_output
