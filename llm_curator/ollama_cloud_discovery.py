"""Ollama Cloud discovery agent.

Two responsibilities:
  1. Scrape ollama.com/search?c=cloud for the current cloud-model catalog.
  2. Test each model against the LOCAL Ollama instance (http://ollama:11434 by default)
     with a tiny prompt to verify free-tier accessibility. Only verified-free models
     get is_free=TRUE; the rest stay in the registry with is_free=FALSE so we know
     they exist but can't use them on the free tier.

Why test-before-add: Ollama Cloud silently moves models between free and paid
tiers without notice. This agent prevents that by testing before registering.

Run manually:   python -m llm_curator.ollama_cloud_discovery
Run from systemd timer: llm-curator-ollama.timer (daily)
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from llm_curator.db import cursor, get_conn  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "brain"))
try:
    from memory_notify import notify  # type: ignore
except Exception:
    def notify(*_args, **_kwargs):
        return None

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("ollama_cloud_discovery")

OLLAMA_SEARCH_URL = "https://ollama.com/search?c=cloud"
OLLAMA_LIBRARY_URL = "https://ollama.com/library/{slug}"
OLLAMA_LOCAL_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
SEED_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ollama_seed_known_free.txt")
SOURCE = "ollama-cloud"
DEPRECATION_DAYS = 30
TEST_PROMPT = "Reply with the single word OK."
TEST_TIMEOUT_S = 30
# Substring in Ollama Cloud's response when a model is paid-tier only
PAID_TIER_SIGNATURE = "requires a subscription"


def load_seed_tags() -> list[str]:
    """Load the user-verified known-free cloud model list."""
    if not os.path.exists(SEED_FILE):
        return []
    tags = []
    with open(SEED_FILE) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                tags.append(line)
    return tags


# ── Scrape catalog ──────────────────────────────────────────────────────────

def scrape_catalog() -> list[str]:
    """Return list of cloud-model slugs from ollama.com (e.g. 'cogito-2.1:671b-cloud')."""
    logger.info("Scraping %s ...", OLLAMA_SEARCH_URL)
    resp = requests.get(OLLAMA_SEARCH_URL, timeout=30, headers={"User-Agent": "llm-curator/1.0"})
    resp.raise_for_status()
    html = resp.text

    # Model links look like: <a href="/library/cogito-2.1">cogito-2.1</a>
    # Cloud variants append :Nb-cloud or :cloud
    slugs = set()
    for m in re.finditer(r'href="/library/([a-z0-9._\-]+)"', html, re.IGNORECASE):
        slugs.add(m.group(1))

    # For each base slug, also probe the library page for its cloud-tagged variants
    cloud_tags = []
    for slug in sorted(slugs):
        tags = fetch_cloud_tags(slug)
        cloud_tags.extend(tags)
    logger.info("Found %d cloud-tagged variants across %d base slugs", len(cloud_tags), len(slugs))
    return cloud_tags


def fetch_cloud_tags(base_slug: str) -> list[str]:
    """Fetch the library page for a model and extract its :*-cloud tags."""
    url = OLLAMA_LIBRARY_URL.format(slug=base_slug)
    try:
        resp = requests.get(url, timeout=20, headers={"User-Agent": "llm-curator/1.0"})
        if resp.status_code != 200:
            return []
    except requests.RequestException:
        return []
    # Look for tag patterns like base_slug:xxx-cloud or base_slug:cloud
    pattern = rf'{re.escape(base_slug)}:[a-z0-9.\-]*cloud'
    tags = set(re.findall(pattern, resp.text, re.IGNORECASE))
    return sorted(tags)


# ── Verify accessibility via local Ollama ───────────────────────────────────

def test_model(model_tag: str) -> tuple[str, str | None]:
    """POST a tiny generate request to local Ollama.

    Returns (status, message) where status is one of:
      'free'     — model responded successfully → accessible on free tier
      'paid'     — Ollama returned the subscription-required error
      'unknown'  — transient error (timeout, network, 5xx); skip update this run
    """
    url = f"{OLLAMA_LOCAL_URL.rstrip('/')}/api/generate"
    payload = {
        "model": model_tag,
        "prompt": TEST_PROMPT,
        "stream": False,
        "options": {"num_predict": 8},
    }
    try:
        resp = requests.post(url, json=payload, timeout=TEST_TIMEOUT_S)
        body = resp.text or ""
        if resp.status_code == 200 and '"done":true' in body.replace(" ", ""):
            return "free", None
        if PAID_TIER_SIGNATURE in body:
            return "paid", body[:200]
        return "unknown", f"HTTP {resp.status_code}: {body[:200]}"
    except requests.RequestException as e:
        return "unknown", f"Request error: {e}"


# ── Parse / upsert ──────────────────────────────────────────────────────────

def parse_tag(model_tag: str, is_free: bool) -> dict[str, Any]:
    """Normalise an Ollama cloud-tag into the registry schema."""
    base = model_tag.split(":")[0]
    return {
        "model_id": model_tag,                  # e.g. 'cogito-2.1:671b-cloud'
        "source": SOURCE,
        "provider": base.split("-")[0],         # rough provider guess
        "display_name": model_tag,
        "context_length": None,                 # Ollama doesn't expose this in the catalog
        "max_completion_tokens": None,
        "modalities": ["text"] + (["image"] if "vl" in base.lower() else []),
        "supports_tools": False,                # unknown without invocation; default conservative
        "supports_reasoning": False,
        "supports_vision": "vl" in base.lower(),
        "supports_audio": False,
        "pricing_input": 0 if is_free else None,
        "pricing_output": 0 if is_free else None,
        "is_free": is_free,
        "knowledge_cutoff": None,
        "description": f"Ollama Cloud model — accessibility verified {datetime.now(UTC).isoformat()}",
        "raw_metadata": json.dumps({"source_url": OLLAMA_SEARCH_URL, "tag": model_tag, "verified_free": is_free}),
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
                provider        = EXCLUDED.provider,
                display_name    = EXCLUDED.display_name,
                modalities      = EXCLUDED.modalities,
                supports_vision = EXCLUDED.supports_vision,
                pricing_input   = EXCLUDED.pricing_input,
                pricing_output  = EXCLUDED.pricing_output,
                is_free         = EXCLUDED.is_free,
                description     = EXCLUDED.description,
                raw_metadata    = EXCLUDED.raw_metadata,
                last_seen       = NOW(),
                deprecated      = FALSE,
                deprecated_at   = NULL
            RETURNING (xmax = 0) AS inserted
            """,
            model,
        )
        row = cur.fetchone()
        return "new" if (row and row["inserted"]) else "updated"


def mark_deprecated() -> int:
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
    stats = {"seen": 0, "new": 0, "updated": 0, "deprecated": 0,
             "free": 0, "paid": 0, "unknown": 0}
    error_msg = None
    try:
        seed_tags = load_seed_tags()
        scraped_tags = scrape_catalog()
        # Merge: seed list (authoritative) + scraped (catches new models we don't know about)
        tags = sorted(set(seed_tags) | set(scraped_tags))
        new_from_scrape = sorted(set(scraped_tags) - set(seed_tags))
        if new_from_scrape:
            logger.info("Scrape found %d models not in seed: %s",
                        len(new_from_scrape), new_from_scrape)
        stats["seen"] = len(tags)
        logger.info("Total tags to test: %d (seed=%d, scrape=%d)",
                    len(tags), len(seed_tags), len(scraped_tags))
        for tag in tags:
            logger.info("Testing %s ...", tag)
            status, err = test_model(tag)
            if status == "free":
                stats["free"] += 1
                logger.info("  → FREE ✓")
            elif status == "paid":
                stats["paid"] += 1
                logger.info("  → PAID (locked)")
            else:
                stats["unknown"] += 1
                logger.warning("  → UNKNOWN — skipping update: %s", err)
                continue  # don't overwrite known state with a transient error
            model = parse_tag(tag, is_free=(status == "free"))
            outcome = upsert(model)
            if outcome == "new":
                stats["new"] += 1
                logger.info("  NEW (free=%s)", status == "free")
            else:
                stats["updated"] += 1
            time.sleep(0.5)  # be gentle on Ollama Cloud
        stats["deprecated"] = mark_deprecated()
        get_conn().commit()
        record_run(stats, success=True)
        get_conn().commit()
        logger.info(
            "Done. seen=%d free=%d paid=%d unknown=%d new=%d updated=%d deprecated=%d",
            stats["seen"], stats["free"], stats["paid"], stats["unknown"],
            stats["new"], stats["updated"], stats["deprecated"],
        )
        notify(
            "llm_registry",
            f"Ollama Cloud discovery: {stats['seen']} cloud models seen, "
            f"{stats['free']} verified free, {stats['paid']} paid-only, "
            f"{stats['unknown']} unverifiable. New: {stats['new']}, deprecated: {stats['deprecated']}.",
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
        logger.exception("Ollama Cloud discovery failed")
        try:
            record_run(stats, success=False, error=error_msg)
            get_conn().commit()
        except Exception:
            pass
        raise


if __name__ == "__main__":
    run()
