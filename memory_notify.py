"""Standalone notify shim for llm-curator.

The curator package imports `notify` with a try/except no-op fallback:

    try:
        from memory_notify import notify
    except Exception:
        def notify(*_a, **_k): ...

A thin Telegram sender that is a silent no-op when the bot token /
chat id are not configured.

Place this file at the repo root (on PYTHONPATH) so the package picks it up.
"""
from __future__ import annotations

import logging
import os

import requests

logger = logging.getLogger("llm_curator.notify")

_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
_CHAT = os.getenv("TELEGRAM_CHAT_ID", "").strip()


def notify(category: str = "", message: str = "", **_kwargs) -> None:
    """Send a Telegram message; no-op if Telegram is not configured."""
    text = f"🐙 llm-curator · {category}\n{message}".strip()
    if not (_TOKEN and _CHAT):
        logger.info("notify (telegram off): %s", text.replace("\n", " | "))
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{_TOKEN}/sendMessage",
            json={"chat_id": _CHAT, "text": text},
            timeout=15,
        )
    except Exception as exc:  # never let a notify failure crash a job
        logger.warning("notify failed: %s", exc)
