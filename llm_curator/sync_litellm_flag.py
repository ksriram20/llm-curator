"""Sync the `in_litellm` + `litellm_alias` columns in llm_registry from the
live litellm_config.yaml.

Why this exists:
  - Without these flags set, the alert detector has no way to know WHICH
    registry rows actually matter (i.e. whose status changes are operationally
    critical vs. just informational catalog churn).
  - Currently all 19 aliased models in the YAML have these columns NULL/false.

Behaviour:
  1. Parse litellm_config.yaml → set of {model_str, alias} pairs.
  2. For each row in llm_registry, set in_litellm/litellm_alias to match
     (TRUE + alias if matched, FALSE + NULL otherwise).
  3. Match logic tries: exact model_id, trailing-segment after first slash,
     and full path-strip (handles 'openrouter/foo/bar' → 'foo/bar' → 'bar').

Idempotent + safe — pure UPDATE statements, no inserts or deletes.

Run:
  python -m llm_curator.sync_litellm_flag
"""
from __future__ import annotations

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from llm_curator.db import cursor, get_conn  # noqa: E402
from llm_curator.litellm_config_parser import parse as parse_yaml  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("sync_litellm_flag")


def _candidate_keys(model_str: str) -> list[str]:
    """All plausible registry model_id forms a litellm `model:` string could match."""
    keys = [model_str]
    if "/" in model_str:
        keys.append(model_str.split("/", 1)[1])      # strip first segment
        keys.append(model_str.rsplit("/", 1)[1])     # last segment only
    return list(dict.fromkeys(keys))                  # dedupe, preserve order


def sync() -> dict[str, int]:
    cfg = parse_yaml()
    matched = unmatched = cleared = 0

    with cursor() as cur:
        # Step 1: clear the flag everywhere — fresh slate each run
        cur.execute("UPDATE llm_registry SET in_litellm = FALSE, litellm_alias = NULL")
        cleared = cur.rowcount

        # Step 2: for each alias in YAML, find the matching registry row
        for alias in cfg.aliases.values():
            keys = _candidate_keys(alias.model)
            cur.execute(
                """
                UPDATE llm_registry
                SET in_litellm = TRUE, litellm_alias = %s
                WHERE model_id = ANY(%s) AND deprecated = FALSE
                RETURNING id, model_id, source
                """,
                (alias.alias, keys),
            )
            rows = cur.fetchall()
            if rows:
                matched += 1
                row = rows[0]
                logger.info("  matched %s → %s [%s]", alias.alias, row["model_id"], row["source"])
                if len(rows) > 1:
                    logger.warning("    (matched %d registry rows for alias %s — extra: %s)",
                                   len(rows), alias.alias,
                                   [r["model_id"] for r in rows[1:]])
            else:
                unmatched += 1
                logger.warning("  no registry row for alias %s (model=%s)",
                               alias.alias, alias.model)

    get_conn().commit()
    logger.info("Done. cleared=%d, matched=%d aliases, unmatched=%d aliases",
                cleared, matched, unmatched)
    return {"cleared": cleared, "matched": matched, "unmatched": unmatched}


if __name__ == "__main__":
    sync()
