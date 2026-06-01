"""OpenRouter discovery agent.

Hits https://openrouter.ai/api/v1/models, upserts every model into llm_registry.
Marks models not seen in 30 days as deprecated. Records audit row in llm_discovery_runs.

Run manually:   python -m llm_curator.openrouter_discovery
Run from systemd timer: llm-curator-openrouter.timer (daily)
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import UTC, date, datetime, timedelta
from typing import Any

import requests

# Allow running as `python -m llm_curator.openrouter_discovery` from sahay/
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from llm_curator.db import cursor, get_conn  # noqa: E402

# Brain memory notifier — fire-and-forget; records discovery summary in memory.md
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "brain"))
try:
    from memory_notify import notify  # type: ignore
except Exception:  # pragma: no cover — Brain may not be reachable in standalone runs
    def notify(*_args, **_kwargs):  # noqa: D401
        return None

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("openrouter_discovery")

OPENROUTER_URL = "https://openrouter.ai/api/v1/models"
SOURCE = "openrouter"
DEPRECATION_DAYS = 30


def fetch_models() -> list[dict[str, Any]]:
    """Fetch full model list from OpenRouter. No auth needed for /models."""
    logger.info("Fetching OpenRouter model list...")
    resp = requests.get(OPENROUTER_URL, timeout=30)
    resp.raise_for_status()
    data = resp.json().get("data", [])
    logger.info("Fetched %d models from OpenRouter", len(data))
    return data


def parse_model(m: dict[str, Any]) -> dict[str, Any]:
    """Normalise an OpenRouter model dict into our registry schema."""
    model_id = m.get("id", "")
    provider = model_id.split("/")[0] if "/" in model_id else None

    arch = m.get("architecture") or {}
    input_mods = arch.get("input_modalities") or []
    output_mods = arch.get("output_modalities") or []
    modalities = sorted(set(input_mods + output_mods))

    supported = m.get("supported_parameters") or []
    supports_tools = "tools" in supported or "tool_choice" in supported
    supports_reasoning = "reasoning" in supported or "include_reasoning" in supported
    supports_vision = "image" in input_mods
    supports_audio = "audio" in input_mods

    pricing = m.get("pricing") or {}
    try:
        p_in = float(pricing.get("prompt", 0) or 0)
        p_out = float(pricing.get("completion", 0) or 0)
    except (TypeError, ValueError):
        p_in = p_out = None

    # Free models: either listed in :free slug, or both prices are 0
    is_free = (":free" in model_id) or (
        p_in is not None and p_out is not None and p_in == 0 and p_out == 0
    )

    cutoff = m.get("knowledge_cutoff")
    cutoff_date = None
    if cutoff:
        try:
            cutoff_date = date.fromisoformat(cutoff[:10])
        except ValueError:
            cutoff_date = None

    top = m.get("top_provider") or {}

    return {
        "model_id": model_id,
        "source": SOURCE,
        "provider": provider,
        "display_name": m.get("name"),
        "context_length": m.get("context_length") or top.get("context_length"),
        "max_completion_tokens": top.get("max_completion_tokens"),
        "modalities": modalities,
        "supports_tools": supports_tools,
        "supports_reasoning": supports_reasoning,
        "supports_vision": supports_vision,
        "supports_audio": supports_audio,
        "pricing_input": p_in,
        "pricing_output": p_out,
        "is_free": is_free,
        "knowledge_cutoff": cutoff_date,
        "description": (m.get("description") or "")[:2000],
        "raw_metadata": json.dumps(m),
    }


def upsert(model: dict[str, Any]) -> str:
    """Atomic UPSERT via ON CONFLICT. Returns 'new' or 'updated'."""
    with cursor() as cur:
        cur.execute(
            """
            INSERT INTO llm_registry (
                model_id, source, provider, display_name,
                context_length, max_completion_tokens, modalities,
                supports_tools, supports_reasoning, supports_vision, supports_audio,
                pricing_input, pricing_output, is_free,
                knowledge_cutoff, description, raw_metadata,
                first_seen, last_seen
            ) VALUES (
                %(model_id)s, %(source)s, %(provider)s, %(display_name)s,
                %(context_length)s, %(max_completion_tokens)s, %(modalities)s,
                %(supports_tools)s, %(supports_reasoning)s, %(supports_vision)s, %(supports_audio)s,
                %(pricing_input)s, %(pricing_output)s, %(is_free)s,
                %(knowledge_cutoff)s, %(description)s, %(raw_metadata)s::jsonb,
                NOW(), NOW()
            )
            ON CONFLICT (model_id, source) DO UPDATE SET
                provider              = EXCLUDED.provider,
                display_name          = EXCLUDED.display_name,
                context_length        = EXCLUDED.context_length,
                max_completion_tokens = EXCLUDED.max_completion_tokens,
                modalities            = EXCLUDED.modalities,
                supports_tools        = EXCLUDED.supports_tools,
                supports_reasoning    = EXCLUDED.supports_reasoning,
                supports_vision       = EXCLUDED.supports_vision,
                supports_audio        = EXCLUDED.supports_audio,
                pricing_input         = EXCLUDED.pricing_input,
                pricing_output        = EXCLUDED.pricing_output,
                is_free               = EXCLUDED.is_free,
                knowledge_cutoff      = EXCLUDED.knowledge_cutoff,
                description           = EXCLUDED.description,
                raw_metadata          = EXCLUDED.raw_metadata,
                last_seen             = NOW(),
                deprecated            = FALSE,
                deprecated_at         = NULL
            RETURNING (xmax = 0) AS inserted
            """,
            model,
        )
        row = cur.fetchone()
        return "new" if (row and row["inserted"]) else "updated"


def mark_deprecated() -> int:
    """Mark models not seen for DEPRECATION_DAYS as deprecated. Returns count."""
    cutoff = datetime.now(UTC) - timedelta(days=DEPRECATION_DAYS)
    with cursor() as cur:
        cur.execute(
            """
            UPDATE llm_registry
            SET deprecated = TRUE, deprecated_at = NOW()
            WHERE source = %s AND last_seen < %s AND deprecated = FALSE
            RETURNING id
            """,
            (SOURCE, cutoff),
        )
        return len(cur.fetchall())


def record_run(stats: dict[str, Any], success: bool, error: str | None = None) -> None:
    with cursor() as cur:
        cur.execute(
            """
            INSERT INTO llm_discovery_runs
                (source, finished_at, models_seen, models_new, models_updated,
                 models_deprecated, success, error_message)
            VALUES (%s, NOW(), %s, %s, %s, %s, %s, %s)
            """,
            (
                SOURCE,
                stats.get("seen", 0),
                stats.get("new", 0),
                stats.get("updated", 0),
                stats.get("deprecated", 0),
                success,
                error,
            ),
        )


def run() -> None:
    stats = {"seen": 0, "new": 0, "updated": 0, "deprecated": 0}
    error_msg = None
    try:
        models = fetch_models()
        stats["seen"] = len(models)
        for raw in models:
            parsed = parse_model(raw)
            if not parsed["model_id"]:
                continue
            outcome = upsert(parsed)
            if outcome == "new":
                stats["new"] += 1
                logger.info("NEW: %s", parsed["model_id"])
            elif outcome == "updated":
                stats["updated"] += 1
        stats["deprecated"] = mark_deprecated()
        get_conn().commit()
        record_run(stats, success=True)
        get_conn().commit()
        logger.info(
            "Done. seen=%d new=%d updated=%d deprecated=%d",
            stats["seen"], stats["new"], stats["updated"], stats["deprecated"],
        )
        notify(
            "llm_registry",
            f"OpenRouter discovery: {stats['seen']} models seen, "
            f"{stats['new']} new, {stats['updated']} updated, "
            f"{stats['deprecated']} deprecated.",
        )
        # Phase 4: re-sync in_litellm flag and scan for in-use changes
        try:
            from llm_curator.sync_litellm_flag import sync as _sync_flag
            from llm_curator.alert_detector import run as _alert_scan
            _sync_flag()
            _alert_scan()
        except Exception:
            logger.exception("alert pipeline failed (non-fatal)")
    except Exception as e:
        get_conn().rollback()
        error_msg = str(e)
        logger.exception("OpenRouter discovery failed")
        try:
            record_run(stats, success=False, error=error_msg)
            get_conn().commit()
        except Exception:
            pass
        raise


if __name__ == "__main__":
    run()
