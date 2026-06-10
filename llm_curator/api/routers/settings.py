"""Settings API — read/write provider configuration stored in curator_settings.

Endpoints:
  GET  /api/settings/               — all keys, API keys masked to last-4 chars
  PUT  /api/settings/{key}          — write one key (empty string clears it)
  POST /api/settings/test/{provider} — live connection test, returns {ok, message, latency_ms}
"""
from __future__ import annotations

import os
import time
from typing import Any

import requests as http_requests
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from llm_curator.db import cursor, get_conn

router = APIRouter()

_MASK_IF = ("api_key", "token", "secret")
_TEST_TIMEOUT = 10  # seconds


def _is_secret(key: str) -> bool:
    return any(s in key for s in _MASK_IF)


def _mask(value: str) -> str:
    if not value:
        return ""
    return "••••••••" + value[-4:] if len(value) > 4 else "••••"


def _load(setting_key: str, env_var: str = "", default: str = "") -> str:
    """Read from DB first, env var fallback, then default."""
    with cursor() as cur:
        cur.execute("SELECT value FROM curator_settings WHERE key = %s", (setting_key,))
        row = cur.fetchone()
        if row and row["value"]:
            return row["value"]
    return os.getenv(env_var, default) if env_var else default


# ── GET all settings ───────────────────────────────────────────────────────────

@router.get("/")
async def get_all_settings() -> list[dict[str, Any]]:
    with cursor() as cur:
        cur.execute("SELECT key, value, updated_at FROM curator_settings ORDER BY key")
        rows = cur.fetchall()
    result = []
    for r in rows:
        secret = _is_secret(r["key"])
        val = r["value"] or ""
        result.append({
            "key": r["key"],
            "is_set": bool(val),
            "display": _mask(val) if secret else val,
            "is_secret": secret,
            "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None,
        })
    return result


# ── PUT one setting ────────────────────────────────────────────────────────────

class SettingUpdate(BaseModel):
    value: str


@router.put("/{key}")
async def update_setting(key: str, body: SettingUpdate) -> dict[str, Any]:
    with cursor() as cur:
        cur.execute("SELECT key FROM curator_settings WHERE key = %s", (key,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail=f"Unknown setting key: {key!r}")
        new_val = body.value.strip() or None  # empty string → NULL (clears the key)
        cur.execute(
            "UPDATE curator_settings SET value = %s, updated_at = NOW() WHERE key = %s",
            (new_val, key),
        )
    get_conn().commit()
    return {"ok": True, "cleared": new_val is None}


# ── POST connection test ───────────────────────────────────────────────────────

@router.post("/test/{provider}")
async def test_provider(provider: str) -> dict[str, Any]:
    try:
        return _do_test(provider)
    except http_requests.Timeout:
        return {"ok": False, "message": "Connection timed out", "latency_ms": 0}
    except http_requests.ConnectionError:
        return {"ok": False, "message": "Connection refused — is the service running?", "latency_ms": 0}
    except Exception as exc:
        return {"ok": False, "message": str(exc), "latency_ms": 0}


def _do_test(provider: str) -> dict[str, Any]:
    start = time.monotonic()

    if provider == "openrouter":
        key = _load("openrouter.api_key", "OPENROUTER_API_KEY")
        if not key:
            return {"ok": False, "message": "API key not configured", "latency_ms": 0}
        resp = http_requests.get(
            "https://openrouter.ai/api/v1/models",
            headers={"Authorization": f"Bearer {key}", "X-Title": "llm-curator"},
            timeout=_TEST_TIMEOUT,
        )
        ms = int((time.monotonic() - start) * 1000)
        if resp.status_code == 200:
            n = len(resp.json().get("data", []))
            return {"ok": True, "message": f"Connected — {n} models visible", "latency_ms": ms}
        return {"ok": False, "message": f"HTTP {resp.status_code}: {resp.text[:120]}", "latency_ms": ms}

    elif provider == "ollama":
        base_url = _load("ollama.base_url", "OLLAMA_URL", "http://localhost:11434")
        resp = http_requests.get(f"{base_url.rstrip('/')}/api/tags", timeout=_TEST_TIMEOUT)
        ms = int((time.monotonic() - start) * 1000)
        if resp.status_code == 200:
            n = len(resp.json().get("models", []))
            return {"ok": True, "message": f"Connected — {n} model(s) available", "latency_ms": ms}
        return {"ok": False, "message": f"HTTP {resp.status_code}", "latency_ms": ms}

    elif provider == "mistral":
        key = _load("mistral.api_key", "MISTRAL_API_KEY")
        if not key:
            return {"ok": False, "message": "API key not configured", "latency_ms": 0}
        resp = http_requests.get(
            "https://api.mistral.ai/v1/models",
            headers={"Authorization": f"Bearer {key}"},
            timeout=_TEST_TIMEOUT,
        )
        ms = int((time.monotonic() - start) * 1000)
        if resp.status_code == 200:
            n = len(resp.json().get("data", []))
            return {"ok": True, "message": f"Connected — {n} models", "latency_ms": ms}
        return {"ok": False, "message": f"HTTP {resp.status_code}", "latency_ms": ms}

    elif provider == "deepseek":
        key = _load("deepseek.api_key", "DEEPSEEK_API_KEY")
        if not key:
            return {"ok": False, "message": "API key not configured", "latency_ms": 0}
        resp = http_requests.get(
            "https://api.deepseek.com/models",
            headers={"Authorization": f"Bearer {key}"},
            timeout=_TEST_TIMEOUT,
        )
        ms = int((time.monotonic() - start) * 1000)
        if resp.status_code == 200:
            return {"ok": True, "message": "Connected", "latency_ms": ms}
        return {"ok": False, "message": f"HTTP {resp.status_code}", "latency_ms": ms}

    elif provider == "google":
        key = _load("google.api_key", "GOOGLE_API_KEY")
        if not key:
            return {"ok": False, "message": "API key not configured", "latency_ms": 0}
        resp = http_requests.get(
            "https://generativelanguage.googleapis.com/v1beta/models",
            params={"key": key, "pageSize": 1},
            timeout=_TEST_TIMEOUT,
        )
        ms = int((time.monotonic() - start) * 1000)
        if resp.status_code == 200:
            return {"ok": True, "message": "Connected", "latency_ms": ms}
        return {"ok": False, "message": f"HTTP {resp.status_code}", "latency_ms": ms}

    elif provider == "anthropic":
        key = _load("anthropic.api_key", "ANTHROPIC_API_KEY")
        if not key:
            return {"ok": False, "message": "API key not configured", "latency_ms": 0}
        resp = http_requests.get(
            "https://api.anthropic.com/v1/models",
            headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
            timeout=_TEST_TIMEOUT,
        )
        ms = int((time.monotonic() - start) * 1000)
        if resp.status_code == 200:
            return {"ok": True, "message": "Connected", "latency_ms": ms}
        return {"ok": False, "message": f"HTTP {resp.status_code}", "latency_ms": ms}

    return {"ok": False, "message": f"Unknown provider: {provider!r}", "latency_ms": 0}
