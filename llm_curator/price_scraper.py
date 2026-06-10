"""Price scraper — keeps pricing_input / pricing_output current in llm_registry.

Fetches current per-token prices from provider APIs, diffs against stored values,
updates llm_registry, and raises a warning alert in llm_alerts for any
in_litellm=TRUE model whose price changed by more than ALERT_THRESHOLD_PCT.

Supported:
  openrouter — via /api/v1/models JSON API (returns prompt/completion pricing).

Stubs (graceful degradation — log warning, no crash):
  mistral, deepseek, google-ai-studio

Schedule: wired as a cron job in the curator container (see crontab).

Manual run:
  python -m llm_curator.price_scraper
  python -m llm_curator.price_scraper --dry-run   # print diffs, no DB writes
"""
from __future__ import annotations

import argparse
import logging
import os
from decimal import Decimal
from typing import Any

import requests

sys_path_hack = True  # keep import order clean below
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from llm_curator.db import cursor, get_conn  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("price_scraper")

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
TIMEOUT_S = 30

# Alert if price changes by more than this fraction for any in_litellm model.
ALERT_THRESHOLD_PCT = 5.0


# ── Fetchers ────────────────────────────────────────────────────────────────


def fetch_openrouter_prices() -> dict[str, tuple[float, float]]:
    """Fetch current pricing from OpenRouter /api/v1/models.

    Returns: {model_id: (pricing_input_per_token, pricing_output_per_token)}
    Prices are returned in USD per token (same unit as llm_registry columns).
    """
    if not OPENROUTER_API_KEY:
        logger.warning("OPENROUTER_API_KEY not set — skipping OpenRouter price fetch")
        return {}
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "HTTP-Referer": "https://github.com/ksriram20/llm-curator",
        "X-Title": "llm-curator",
    }
    try:
        resp = requests.get(OPENROUTER_MODELS_URL, headers=headers, timeout=TIMEOUT_S)
        resp.raise_for_status()
        data = resp.json().get("data") or []
    except requests.RequestException as e:
        logger.error("OpenRouter models fetch failed: %s", e)
        return {}

    prices: dict[str, tuple[float, float]] = {}
    for model in data:
        model_id = model.get("id")
        pricing = model.get("pricing") or {}
        try:
            p_in = float(pricing.get("prompt") or 0)
            p_out = float(pricing.get("completion") or 0)
        except (ValueError, TypeError):
            continue
        if model_id:
            prices[model_id] = (p_in, p_out)

    logger.info("OpenRouter: fetched prices for %d models", len(prices))
    return prices


def fetch_mistral_prices() -> dict[str, tuple[float, float]]:
    logger.info("Mistral pricing scraper not yet implemented — skipping")
    return {}


def fetch_deepseek_prices() -> dict[str, tuple[float, float]]:
    logger.info("Deepseek pricing scraper not yet implemented — skipping")
    return {}


def fetch_google_prices() -> dict[str, tuple[float, float]]:
    logger.info("Google AI Studio pricing scraper not yet implemented — skipping")
    return {}


# ── DB helpers ───────────────────────────────────────────────────────────────


def load_current_prices() -> dict[tuple[str, str], dict[str, Any]]:
    """Load current pricing + metadata for all non-deprecated registry rows.

    Returns: {(model_id, source): {id, pricing_input, pricing_output, in_litellm}}
    """
    with cursor() as cur:
        cur.execute(
            """
            SELECT id, model_id, source, pricing_input, pricing_output, in_litellm
            FROM llm_registry
            WHERE deprecated = FALSE
            """
        )
        rows = cur.fetchall()
    return {(r["model_id"], r["source"]): dict(r) for r in rows}


def upsert_prices(
    new_prices: dict[str, tuple[float, float]],
    source: str,
    current: dict[tuple[str, str], dict[str, Any]],
    dry_run: bool,
) -> list[dict[str, Any]]:
    """Write new prices to llm_registry and return a list of changed rows.

    Changed row format: {model_id, source, old_in, old_out, new_in, new_out, pct_change_in}
    Only rows already in the registry are updated (no new rows inserted here — that
    is discovery's job).
    """
    changed: list[dict[str, Any]] = []

    for model_id, (new_in, new_out) in new_prices.items():
        key = (model_id, source)
        if key not in current:
            continue  # not in registry yet — discovery will add it
        row = current[key]
        old_in = float(row["pricing_input"]) if row["pricing_input"] is not None else None
        old_out = float(row["pricing_output"]) if row["pricing_output"] is not None else None

        # Detect meaningful change (avoid float noise for zero-priced free models)
        in_changed = _price_changed(old_in, new_in)
        out_changed = _price_changed(old_out, new_out)

        if in_changed or out_changed:
            pct = _pct_change(old_in, new_in)
            changed.append({
                "model_id": model_id,
                "source": source,
                "registry_id": row["id"],
                "in_litellm": bool(row["in_litellm"]),
                "old_in": old_in,
                "old_out": old_out,
                "new_in": new_in,
                "new_out": new_out,
                "pct_change_in": pct,
            })
            logger.info(
                "  price change: %s [%s]  in: %s→%.8f  out: %s→%.8f  (%.1f%%)",
                model_id, source,
                f"{old_in:.8f}" if old_in is not None else "None", new_in,
                f"{old_out:.8f}" if old_out is not None else "None", new_out,
                pct if pct is not None else 0.0,
            )

        if not dry_run and (in_changed or out_changed or old_in is None or old_out is None):
            with cursor() as cur:
                cur.execute(
                    """
                    UPDATE llm_registry
                       SET pricing_input = %s, pricing_output = %s
                     WHERE id = %s
                    """,
                    (new_in, new_out, row["id"]),
                )

    return changed


def raise_pricing_alerts(changed: list[dict[str, Any]], dry_run: bool) -> int:
    """Create llm_alerts rows for in_litellm models with significant price changes."""
    alerts_raised = 0
    for c in changed:
        if not c["in_litellm"]:
            continue
        pct = c["pct_change_in"]
        if pct is None or abs(pct) < ALERT_THRESHOLD_PCT:
            continue
        direction = "increased" if pct > 0 else "decreased"
        message = (
            f"Pricing {direction} by {abs(pct):.1f}% for {c['model_id']} [{c['source']}]. "
            f"Input: ${c['old_in']:.8f} → ${c['new_in']:.8f} per token. "
            f"Output: ${c['old_out']:.8f} → ${c['new_out']:.8f} per token."
        )
        logger.warning("ALERT: %s", message)
        if not dry_run:
            with cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO llm_alerts (model_registry_id, category, severity, message)
                    VALUES (%s, 'pricing_change', 'warning', %s)
                    """,
                    (c["registry_id"], message),
                )
        alerts_raised += 1
    return alerts_raised


# ── Utilities ────────────────────────────────────────────────────────────────


def _price_changed(old: float | None, new: float) -> bool:
    if old is None:
        return new != 0.0
    return abs(old - new) > 1e-10


def _pct_change(old: float | None, new: float) -> float | None:
    if old is None or old == 0.0:
        return None
    return (new - old) / old * 100.0


# ── Main ─────────────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(description="Scrape and update LLM pricing in registry.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print diffs without writing to DB or raising alerts")
    args = parser.parse_args()

    if args.dry_run:
        logger.info("DRY RUN — no DB writes")

    current = load_current_prices()
    logger.info("Loaded %d registry rows", len(current))

    total_changed = 0
    total_alerts = 0

    # OpenRouter
    or_prices = fetch_openrouter_prices()
    if or_prices:
        changed = upsert_prices(or_prices, "openrouter", current, args.dry_run)
        alerts = raise_pricing_alerts(changed, args.dry_run)
        total_changed += len(changed)
        total_alerts += alerts

    # Stubs — extend here as scrapers are implemented
    for fetch_fn, source in [
        (fetch_mistral_prices, "mistral-api"),
        (fetch_deepseek_prices, "deepseek-api"),
        (fetch_google_prices, "google-ai-studio"),
    ]:
        prices = fetch_fn()
        if prices:
            changed = upsert_prices(prices, source, current, args.dry_run)
            alerts = raise_pricing_alerts(changed, args.dry_run)
            total_changed += len(changed)
            total_alerts += alerts

    if not args.dry_run:
        get_conn().commit()

    logger.info(
        "Done. %d price changes detected, %d alerts raised%s.",
        total_changed, total_alerts,
        " (dry run — nothing written)" if args.dry_run else "",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
