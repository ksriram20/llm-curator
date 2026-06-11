"""Outbound webhook delivery for llm-curator proposal events.

POST payload format:
  {
    "event":         "proposal.applied" | "proposal.rejected" | "test",
    "schema_version": "1.0",
    "proposal_id":   3,
    "generated_at":  "...",
    "summary":       "...",
    "changes": [
      {
        "kind":       "replace" | "add" | "remove",
        "alias":      "reasoning",
        "from_model": "openai/gpt-oss-20b:free",   # replace/remove only
        "to_model":   "openai/gpt-oss-120b:free",  # replace/add only
        "rationale":  "...",
        "evidence":   {"new_score": 1.0, "n_evals": 17}
      }
    ],
    "needs_eval": [...]   # aliases flagged as needing more eval data
  }

Security:
  If webhook.secret is set, every delivery includes:
    X-LLM-Curator-Signature: sha256=<hmac>
  Verify on the receiver with:
    hmac.compare_digest(
        "sha256=" + hmac.new(secret, body, sha256).hexdigest(),
        request.headers["X-LLM-Curator-Signature"]
    )
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from typing import Any

import requests

logger = logging.getLogger("llm_curator.webhook")


def _get_webhook_config() -> tuple[str, str]:
    """Return (url, secret) from DB/env. Both may be empty strings."""
    try:
        from llm_curator.eval_providers import _get_setting
        url = _get_setting("webhook.url", "WEBHOOK_URL")
        secret = _get_setting("webhook.secret", "WEBHOOK_SECRET")
        return url, secret
    except Exception:
        import os
        return os.getenv("WEBHOOK_URL", ""), os.getenv("WEBHOOK_SECRET", "")


def build_export(proposal: dict[str, Any]) -> dict[str, Any]:
    """Reshape a raw proposal DB row into the clean export format."""
    raw = proposal.get("proposed_changes") or {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            raw = {}

    raw_changes = raw.get("changes", []) if isinstance(raw, dict) else []
    needs_eval = raw.get("needs_eval", []) if isinstance(raw, dict) else []

    changes = []
    for c in raw_changes:
        entry: dict[str, Any] = {
            "kind": c.get("kind"),
            "alias": c.get("alias") or c.get("litellm_alias"),
        }
        if c.get("old_model"):
            entry["from_model"] = c["old_model"]
        if c.get("new_model"):
            entry["to_model"] = c["new_model"]
        if c.get("rationale"):
            entry["rationale"] = c["rationale"]
        if c.get("evidence"):
            entry["evidence"] = c["evidence"]
        changes.append(entry)

    return {
        "schema_version": "1.0",
        "proposal_id": proposal.get("id"),
        "generated_at": str(proposal.get("generated_at", "")),
        "summary": proposal.get("summary", ""),
        "changes": changes,
        "needs_eval": needs_eval,
    }


def deliver(event: str, proposal: dict[str, Any]) -> bool:
    """POST a proposal event to the configured webhook URL.

    Returns True on success, False on any failure (never raises).
    No-ops silently when webhook.url is not configured.
    """
    url, secret = _get_webhook_config()
    if not url:
        return False

    payload = {"event": event, **build_export(proposal)}
    body = json.dumps(payload, default=str)

    ts = str(int(time.time()))
    headers = {
        "Content-Type": "application/json",
        "X-LLM-Curator-Event": event,
        "X-LLM-Curator-Timestamp": ts,
        "User-Agent": "llm-curator/0.4",
    }
    if secret:
        sig = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
        headers["X-LLM-Curator-Signature"] = f"sha256={sig}"

    try:
        resp = requests.post(url, data=body, headers=headers, timeout=10)
        if resp.status_code < 300:
            logger.info("Webhook delivered: event=%s status=%d", event, resp.status_code)
            return True
        logger.warning("Webhook failed: event=%s status=%d body=%s",
                       event, resp.status_code, resp.text[:200])
        return False
    except requests.Timeout:
        logger.warning("Webhook timeout: event=%s url=%s", event, url)
        return False
    except Exception as e:
        logger.warning("Webhook error: event=%s error=%s", event, e)
        return False
