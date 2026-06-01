"""LLM Curator Phase 3 — proposal generator.

Reads:
  - Current litellm_config.yaml (via litellm_config_parser)
  - Registry + eval scores (llm_registry + llm_evals)

Produces a structured proposal listing:
  - REPLACE: alias whose underlying model should change (better candidate exists)
  - ADD:     a model not currently in litellm_config that scores high enough to alias
  - REMOVE:  alias whose underlying model is deprecated and has no decent successor

Conservatism rules (deliberate — we don't want flapping):
  - Need ≥ MIN_EVALS recent evals for a model to be eligible as a candidate.
  - Only REPLACE if candidate score exceeds incumbent by ≥ IMPROVEMENT_THRESHOLD.
  - Maximum MAX_CHANGES per proposal (avoid massive churn).
  - REMOVE only when the underlying model is flagged deprecated in registry.
  - Proposals are NEVER auto-applied — they land in llm_proposals.status='pending'.

Run:
  python -m llm_curator.proposal_generator                  # generate + persist
  python -m llm_curator.proposal_generator --dry-run        # print only, don't store
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections import defaultdict
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from llm_curator.db import cursor, get_conn  # noqa: E402
from llm_curator.litellm_config_parser import parse as parse_yaml  # noqa: E402
from llm_curator.policy import is_eval_eligible  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "brain"))
try:
    from memory_notify import notify  # type: ignore
except Exception:
    def notify(*_args, **_kwargs):
        return None

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("proposal_generator")

# ── Tunables ─────────────────────────────────────────────────────────────────
MIN_EVALS = 2                       # candidate must have ≥ N recent evals
LOOKBACK_DAYS = 60                  # eval window
IMPROVEMENT_THRESHOLD = 0.10        # candidate must beat incumbent by this much
MAX_CHANGES_PER_PROPOSAL = 5        # safety cap on churn

# Map LiteLLM tier semantics → eval use_cases (mean score across these = tier score)
TIER_USE_CASES: dict[str, list[str]] = {
    "reasoning": ["reasoning"],
    "standard":  ["extraction", "classification", "summarization"],
    "free":      ["reasoning", "extraction", "classification", "summarization"],
}


# ── Step 1: Pull eval scores per model ───────────────────────────────────────


def fetch_model_scores() -> dict[tuple[str, str], dict[str, Any]]:
    """
    Return dict keyed by (model_id, source) → {
        'is_free': bool, 'deprecated': bool, 'in_litellm': bool,
        'mean_by_uc': {use_case: mean_score},
        'n_evals': int,
        'reliability': float (% prompts that returned a valid score),
    }

    Policy-excluded models are filtered out — they can't be candidates even
    if they happen to have historical eval scores in the DB.
    """
    with cursor() as cur:
        cur.execute(f"""
            SELECT r.model_id, r.source, r.is_free, r.deprecated, r.in_litellm,
                   e.use_case,
                   AVG(e.score)::float                       AS mean_score,
                   COUNT(*)                                  AS n_evals,
                   COUNT(*) FILTER (WHERE e.score IS NULL)   AS n_failed
            FROM llm_evals e
            JOIN llm_registry r ON r.id = e.model_registry_id
            WHERE e.tested_at > NOW() - INTERVAL '{LOOKBACK_DAYS} days'
            GROUP BY r.model_id, r.source, r.is_free, r.deprecated, r.in_litellm, e.use_case
        """)
        rows = cur.fetchall()

    out: dict[tuple[str, str], dict[str, Any]] = {}
    totals_per_model: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
    skipped_by_policy: set[tuple[str, str]] = set()
    for r in rows:
        key = (r["model_id"], r["source"])
        # Policy filter — historical scores from excluded models are not candidates
        ok, _ = is_eval_eligible(r["model_id"], r["source"],
                                 bool(r["is_free"]), bool(r["in_litellm"]))
        if not ok:
            skipped_by_policy.add(key)
            continue
        entry = out.setdefault(key, {
            "is_free":     r["is_free"],
            "deprecated":  r["deprecated"],
            "in_litellm":  r["in_litellm"],
            "mean_by_uc":  {},
            "n_evals":     0,
        })
        if r["mean_score"] is not None:
            entry["mean_by_uc"][r["use_case"]] = round(float(r["mean_score"]), 4)
        entry["n_evals"] += r["n_evals"]
        totals_per_model[key][0] += r["n_evals"]
        totals_per_model[key][1] += r["n_failed"]

    for key, (total, failed) in totals_per_model.items():
        if key in out:
            out[key]["reliability"] = round(1.0 - (failed / total), 4) if total else 0.0

    if skipped_by_policy:
        logger.info("Policy filter dropped %d eval-bearing models from candidate pool",
                    len(skipped_by_policy))
    return out


# ── Step 2: Score a model for a given tier ───────────────────────────────────


def tier_score(model_record: dict[str, Any], tier: str) -> tuple[float | None, int]:
    """
    Return (mean score across the tier's use_cases, n_use_cases_covered).
    None if the model has no scores on any of the relevant use_cases.
    """
    relevant = TIER_USE_CASES.get(tier, [])
    scores = [model_record["mean_by_uc"][uc] for uc in relevant if uc in model_record["mean_by_uc"]]
    if not scores:
        return None, 0
    return round(sum(scores) / len(scores), 4), len(scores)


# ── Step 3: Generate replacement candidates per alias ────────────────────────


def alias_tier(alias_entry, model_record: dict[str, Any] | None) -> str:
    """Best-effort tier classification for an existing alias."""
    if alias_entry.reasoning_flag:
        return "reasoning"
    if alias_entry.alias.startswith("or-") or alias_entry.model.endswith(":free"):
        return "free"
    if "vision" in alias_entry.alias or "image" in alias_entry.alias:
        return "vision"   # not scored in our suite — skip from proposals
    return "standard"


def best_candidate_for_tier(
    tier: str,
    incumbent_score: float | None,
    all_scores: dict[tuple[str, str], dict[str, Any]],
    exclude_model: str | None = None,
    free_only: bool = False,
) -> tuple[tuple[str, str], float, int, dict[str, Any]] | None:
    """Return ((model_id, source), tier_score, n_evals_covered, full_record) or None."""
    best: tuple[tuple[str, str], float, int, dict[str, Any]] | None = None
    for key, rec in all_scores.items():
        if exclude_model and key[0] == exclude_model:
            continue
        if rec["deprecated"]:
            continue
        if rec["n_evals"] < MIN_EVALS:
            continue
        if free_only and not rec["is_free"]:
            continue
        score, n_covered = tier_score(rec, tier)
        if score is None:
            continue
        if best is None or score > best[1]:
            best = (key, score, n_covered, rec)
    return best


# ── Step 4: Build the proposal ───────────────────────────────────────────────


def generate_proposal() -> dict[str, Any]:
    cfg = parse_yaml()
    scores = fetch_model_scores()
    logger.info("Loaded %d eval-bearing models, %d aliases in current config",
                len(scores), len(cfg.aliases))

    changes: list[dict[str, Any]] = []
    needs_eval: list[dict[str, Any]] = []        # aliases whose incumbent has zero eval data
    n_replace = n_add = n_remove = 0

    def _find_score(model_str: str) -> dict[str, Any] | None:
        """Match the litellm `model:` string to a (model_id, source) key in scores."""
        for key, rec in scores.items():
            mid = key[0]
            if mid == model_str:
                return rec
            # litellm uses prefixes like "openrouter/", "deepseek/", "ollama/"
            if "/" in model_str and model_str.split("/", 1)[1] == mid:
                return rec
            if model_str.endswith("/" + mid):
                return rec
        return None

    # Pass 1: REPLACE — only when BOTH incumbent and candidate have eval data
    for alias in cfg.aliases.values():
        tier = alias_tier(alias, None)
        if tier == "vision":
            continue                                  # not in our eval suite

        incumbent_record = _find_score(alias.model)
        incumbent_score = (
            tier_score(incumbent_record, tier)[0] if incumbent_record else None
        )

        # CASE A: incumbent has no eval data → surface for attention (don't auto-replace)
        if incumbent_record is None:
            needs_eval.append({
                "alias": alias.alias,
                "model": alias.model,
                "tier": tier,
                "reason": "no recent eval data — prioritize in eval rotation",
            })
            continue

        # CASE B: incumbent deprecated → must replace (or surface for removal)
        if incumbent_record["deprecated"]:
            cand = best_candidate_for_tier(tier, None, scores,
                                           exclude_model=alias.model,
                                           free_only=(tier == "free"))
            if cand:
                (mid, src), s, n_cov, rec = cand
                changes.append({
                    "kind": "replace",
                    "alias": alias.alias,
                    "old_model": alias.model,
                    "new_model": mid,
                    "new_source": src,
                    "rationale": "incumbent flagged deprecated; replacing with best-scoring eligible candidate",
                    "evidence": {
                        "tier": tier,
                        "incumbent_score": incumbent_score,
                        "candidate_score": s,
                        "candidate_n_evals": rec["n_evals"],
                        "candidate_use_cases_covered": n_cov,
                    },
                })
                n_replace += 1
            else:
                changes.append({
                    "kind": "remove",
                    "alias": alias.alias,
                    "old_model": alias.model,
                    "rationale": "incumbent deprecated; no eligible replacement in registry",
                    "evidence": {"tier": tier, "incumbent_score": incumbent_score},
                })
                n_remove += 1
            continue

        # CASE C: incumbent has data AND not deprecated → look for materially better candidate
        if incumbent_score is None:
            # Incumbent has eval data but not for this tier's use_cases — skip silently
            continue

        cand = best_candidate_for_tier(tier, incumbent_score, scores,
                                       exclude_model=alias.model,
                                       free_only=(tier == "free"))
        if not cand:
            continue
        (mid, src), s, n_cov, rec = cand

        if s - incumbent_score < IMPROVEMENT_THRESHOLD:
            continue   # not enough lift to bother

        changes.append({
            "kind": "replace",
            "alias": alias.alias,
            "old_model": alias.model,
            "new_model": mid,
            "new_source": src,
            "rationale": f"candidate beats incumbent by {s - incumbent_score:.3f} on {tier} tier",
            "evidence": {
                "tier": tier,
                "incumbent_score": incumbent_score,
                "candidate_score": s,
                "candidate_n_evals": rec["n_evals"],
                "candidate_use_cases_covered": n_cov,
            },
        })
        n_replace += 1
        if len(changes) >= MAX_CHANGES_PER_PROPOSAL:
            break

    # (Phase 3 deliberately omits ADD recommendations — once REPLACE behaviour is
    # battle-tested we can broaden the engine. ADD would risk bloating model_list.)

    summary = (
        f"{n_replace} replacement(s), {n_add} addition(s), {n_remove} removal(s) "
        f"across {len(cfg.aliases)} aliases · {len(needs_eval)} need eval data "
        f"(lookback={LOOKBACK_DAYS}d, min_evals={MIN_EVALS}, threshold={IMPROVEMENT_THRESHOLD})"
    )

    return {
        "summary": summary,
        "current_snapshot": cfg.to_snapshot(),
        "proposed_changes": changes,
        "needs_eval":     needs_eval,
        "n_replacements": n_replace,
        "n_additions":    n_add,
        "n_removals":     n_remove,
    }


# ── Step 5: Persist + supersede older pending proposals ──────────────────────


def persist(proposal: dict[str, Any]) -> int:
    with cursor() as cur:
        # Supersede any older pending proposals
        cur.execute(
            "UPDATE llm_proposals SET status='superseded', reviewed_at=NOW() "
            "WHERE status='pending'"
        )
        # needs_eval is stored as a sibling under proposed_changes wrapper
        payload = {
            "changes":    proposal["proposed_changes"],
            "needs_eval": proposal["needs_eval"],
        }
        cur.execute(
            """
            INSERT INTO llm_proposals
                (summary, current_snapshot, proposed_changes,
                 n_replacements, n_additions, n_removals)
            VALUES (%s, %s::jsonb, %s::jsonb, %s, %s, %s)
            RETURNING id
            """,
            (
                proposal["summary"],
                json.dumps(proposal["current_snapshot"]),
                json.dumps(payload),
                proposal["n_replacements"],
                proposal["n_additions"],
                proposal["n_removals"],
            ),
        )
        row = cur.fetchone()
        get_conn().commit()
        return row["id"]


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true",
                   help="Print proposal but don't persist or notify")
    args = p.parse_args()

    proposal = generate_proposal()
    logger.info("PROPOSAL: %s", proposal["summary"])
    for ch in proposal["proposed_changes"]:
        if ch["kind"] == "replace":
            logger.info("  REPLACE %s: %s → %s  [score %.3f → %.3f]",
                        ch["alias"], ch["old_model"], ch["new_model"],
                        ch["evidence"].get("incumbent_score") or 0.0,
                        ch["evidence"]["candidate_score"])
        elif ch["kind"] == "remove":
            logger.info("  REMOVE  %s: %s", ch["alias"], ch["old_model"])
        elif ch["kind"] == "add":
            logger.info("  ADD     %s: %s", ch["alias"], ch["new_model"])
    if proposal["needs_eval"]:
        logger.info("ATTENTION — %d aliases have no recent eval data:",
                    len(proposal["needs_eval"]))
        for ne in proposal["needs_eval"][:10]:
            logger.info("  %-24s (%s, %s tier)", ne["alias"], ne["model"], ne["tier"])
        if len(proposal["needs_eval"]) > 10:
            logger.info("  ... and %d more", len(proposal["needs_eval"]) - 10)

    if args.dry_run:
        return 0

    pid = persist(proposal)
    logger.info("Persisted proposal id=%d", pid)

    n = len(proposal["proposed_changes"])
    if n == 0:
        notify("llm_registry",
               f"Curator: no changes recommended (lookback {LOOKBACK_DAYS}d, "
               f"{proposal['n_replacements']+proposal['n_additions']+proposal['n_removals']} candidates qualified).")
    else:
        notify("llm_registry",
               f"Curator proposal #{pid}: {proposal['summary']}. "
               f"Review: llm-curator proposal {pid}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
