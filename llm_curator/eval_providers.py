"""Single call() abstraction across OpenRouter and Ollama Cloud.

Why not route through LiteLLM:
  - LiteLLM only knows the ~20 models in litellm_config.yaml. The whole point of
    the evaluator is to test models we DON'T have aliased yet so we can decide
    whether to add them. Going around LiteLLM keeps the eval surface independent
    from the production routing surface.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Any

import requests

logger = logging.getLogger("llm_curator.eval_providers")

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OLLAMA_LOCAL_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")

# Conservative timeouts — keep one eval prompt under 90s
TIMEOUT_S = 90


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


def _call_openrouter(
    model_id: str,
    user_prompt: str,
    system_prompt: str | None,
    max_tokens: int,
) -> CallResult:
    if not OPENROUTER_API_KEY:
        return CallResult("", None, None, 0, error="OPENROUTER_API_KEY not set")
    messages: list[dict[str, Any]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": user_prompt})
    payload = {"model": model_id, "messages": messages, "max_tokens": max_tokens}
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://parcon.local",       # OpenRouter recommends
        "X-Title": "PARCON LLM Curator",
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
    url = f"{OLLAMA_LOCAL_URL.rstrip('/')}/api/generate"
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
