"""LLM Curator Phase 4 — alert detector.

Designed to be called at the END of each discovery agent's run. It compares the
current state of the registry to what the agent just observed and emits alerts
when something operationally meaningful changes for IN-USE models.

Categories produced:
  - IN_USE_DEPRECATED (critical) : a model in litellm_config just got flagged deprecated
  - IN_USE_PAID      (critical) : an Ollama Cloud model in litellm_config moved free→paid
  - MODEL_RESURRECTED (info)    : a previously-deprecated in-use model is back
  - LITELLM_ORPHAN   (warn)     : an alias points at a model not in the registry at all

De-dup safety: each alert has a natural-key signature (category + model_id +
litellm_alias). If the same alert fired in the last 24h, we don't fire it again.
Run:
  python -m llm_curator.alert_detector              # scan + emit
  python -m llm_curator.alert_detector --dry-run    # scan + print, no DB writes
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from llm_curator.db import cursor, get_conn  # noqa: E402
from llm_curator.litellm_config_parser import parse as parse_yaml  # noqa: E402

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "brain"))
try:
    from memory_notify import notify  # type: ignore
except Exception:
    def notify(*_args, **_kwargs):
        return None

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("alert_detector")

# Critical alerts get Telegram-relayed; non-critical just sit in the dashboard
CRITICAL_CATEGORIES = {"IN_USE_DEPRECATED", "IN_USE_PAID"}
DEDUP_WINDOW_HOURS = 24


# ── Detection passes ────────────────────────────────────────────────────────


def scan() -> list[dict[str, Any]]:
    """Return a list of {severity, category, model_id, source, litellm_alias,
    message} dicts. Pure read — no DB mutations here."""
    alerts: list[dict[str, Any]] = []

    with cursor() as cur:
        # Pass A: in-use models flagged deprecated
        cur.execute("""
            SELECT model_id, source, litellm_alias
            FROM llm_registry
            WHERE in_litellm = TRUE AND deprecated = TRUE
        """)
        for r in cur.fetchall():
            alerts.append({
                "severity": "critical",
                "category": "IN_USE_DEPRECATED",
                "model_id": r["model_id"],
                "source":   r["source"],
                "litellm_alias": r["litellm_alias"],
                "message":  (f"Model `{r['model_id']}` ({r['source']}) is in litellm_config "
                             f"as `{r['litellm_alias']}` but was flagged DEPRECATED. "
                             f"Update litellm_config.yaml."),
            })

        # Pass B: in-use Ollama Cloud models that moved free → paid
        # (We can only detect this on ollama-cloud rows — OpenRouter prices change
        # but in-use status doesn't break.)
        cur.execute("""
            SELECT model_id, source, litellm_alias
            FROM llm_registry
            WHERE in_litellm = TRUE
              AND source     = 'ollama-cloud'
              AND is_free    = FALSE
              AND deprecated = FALSE
        """)
        for r in cur.fetchall():
            alerts.append({
                "severity": "critical",
                "category": "IN_USE_PAID",
                "model_id": r["model_id"],
                "source":   r["source"],
                "litellm_alias": r["litellm_alias"],
                "message":  (f"Ollama Cloud model `{r['model_id']}` is in litellm_config "
                             f"as `{r['litellm_alias']}` but the latest verification says "
                             f"it requires a paid subscription. It will start failing. "
                             f"Update litellm_config.yaml."),
            })

    # Pass C: LiteLLM aliases pointing at models that aren't in the registry at all
    # (e.g. an Ollama-Cloud tag that vanished from the catalog)
    cfg = parse_yaml()
    registry_known: set[str] = set()
    with cursor() as cur:
        cur.execute("SELECT model_id FROM llm_registry")
        registry_known = {r["model_id"] for r in cur.fetchall()}
    for alias in cfg.aliases.values():
        if alias.model.startswith("ollama/") and "qwen2.5:3b" in alias.model:
            continue   # local-only, not expected in cloud registry
        if alias.model.startswith("mistral/"):
            continue   # direct Mistral API, not tracked
        candidates = [
            alias.model,
            alias.model.split("/", 1)[1] if "/" in alias.model else alias.model,
            alias.model.rsplit("/", 1)[1] if "/" in alias.model else alias.model,
        ]
        if not any(c in registry_known for c in candidates):
            alerts.append({
                "severity": "warn",
                "category": "LITELLM_ORPHAN",
                "model_id": alias.model,
                "source":   None,
                "litellm_alias": alias.alias,
                "message":  (f"Alias `{alias.alias}` points at `{alias.model}`, "
                             f"but no registry row matches. The model may have been "
                             f"removed from its source catalog."),
            })

    return alerts


# ── Persistence with de-dup ──────────────────────────────────────────────────


def recently_seen(category: str, model_id: str | None, alias: str | None) -> bool:
    with cursor() as cur:
        cur.execute(f"""
            SELECT 1 FROM llm_alerts
            WHERE category      = %s
              AND COALESCE(model_id, '')      = COALESCE(%s, '')
              AND COALESCE(litellm_alias, '') = COALESCE(%s, '')
              AND generated_at  > NOW() - INTERVAL '{DEDUP_WINDOW_HOURS} hours'
            LIMIT 1
        """, (category, model_id, alias))
        return cur.fetchone() is not None


def persist(alert: dict[str, Any]) -> int | None:
    if recently_seen(alert["category"], alert["model_id"], alert["litellm_alias"]):
        return None
    with cursor() as cur:
        cur.execute(
            """
            INSERT INTO llm_alerts
                (severity, category, model_id, source, litellm_alias, message)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                alert["severity"], alert["category"], alert["model_id"],
                alert["source"], alert["litellm_alias"], alert["message"],
            ),
        )
        return cur.fetchone()["id"]


def run(dry_run: bool = False) -> dict[str, int]:
    alerts = scan()
    stats = {"scanned": len(alerts), "emitted": 0, "deduped": 0, "critical": 0}
    if not alerts:
        logger.info("No alerts to emit.")
        return stats

    for a in alerts:
        if dry_run:
            logger.info("  [%s] %s — %s", a["severity"].upper(), a["category"], a["message"][:120])
            continue
        new_id = persist(a)
        if new_id is None:
            stats["deduped"] += 1
            continue
        stats["emitted"] += 1
        if a["severity"] == "critical":
            stats["critical"] += 1
            notify("llm_alert",
                   f"[{a['category']}] {a['litellm_alias'] or a['model_id']}: {a['message'][:200]}")
        logger.info("  emitted alert #%d [%s] %s", new_id, a["severity"], a["category"])

    if not dry_run:
        get_conn().commit()
    logger.info("Done. scanned=%d emitted=%d deduped=%d critical=%d",
                stats["scanned"], stats["emitted"], stats["deduped"], stats["critical"])
    return stats


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    run(dry_run=args.dry_run)
