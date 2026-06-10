"""LLM evaluator — picks the next-due model from the registry, runs every prompt
in eval_prompts.PROMPTS, grades each, writes rows to llm_evals, updates
llm_registry.last_evaluated_at.

Rotation rules:
  1. Free models only (is_free=TRUE) by default — safety net against paid budget.
     Paid models with eval_cost_cap_usd set are included automatically.
     Override with --include-paid (CLI/manual only; never from the cron timer).
  2. Skip deprecated models.
  3. Order: NULL last_evaluated_at first (never tested), then oldest tested.
  4. One model per invocation. Daily timer = one model per day.

Cost safety:
  - HARD_COST_CAP_USD = 0.10 per run (global default).
  - Per-model override: llm_registry.eval_cost_cap_usd (NULL = use global default).
  - Refuses to evaluate any model whose projected cost exceeds its effective cap.

Tiered eval depth:
  - Light suite (LIGHT_SUITE_IDS): 2 prompts for never-evaluated models.
    Quick capability check on first encounter; avoids burning tokens on bad models.
  - Full suite: all prompts for models with prior eval history.

Tool use prompts:
  - Routed to call_with_tools() instead of call().
  - Skipped (error recorded) for non-OpenRouter sources.

Manual run:
  python -m llm_curator.eval_runner                 # next-due free model
  python -m llm_curator.eval_runner --model deepseek/deepseek-v4-flash --source openrouter
  python -m llm_curator.eval_runner --include-paid  # opens up paid models too
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from llm_curator.db import cursor, get_conn  # noqa: E402
from llm_curator.eval_prompts import PROMPTS, GRADER_VERSION  # noqa: E402
from llm_curator.eval_providers import call, call_with_tools, CallResult, estimate_cost_usd  # noqa: E402
from llm_curator.policy import is_eval_eligible  # noqa: E402

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
logger = logging.getLogger("eval_runner")

HARD_COST_CAP_USD = 0.10  # global default cap per single-model eval run

# Light-tier prompt IDs: run for never-evaluated models (last_evaluated_at IS NULL).
# One reasoning + one classification — fast, cheap, broad signal.
LIGHT_SUITE_IDS: frozenset[str] = frozenset({"reasoning_widgets", "classification_sentiment"})


# ── Tier / cap helpers ──────────────────────────────────────────────────────


def select_prompts(model: dict[str, Any]) -> list[dict[str, Any]]:
    """Light suite for first-run models; full suite for all others."""
    if model.get("last_evaluated_at") is None:
        light = [p for p in PROMPTS if p["id"] in LIGHT_SUITE_IDS]
        return light if light else PROMPTS[:2]
    return PROMPTS


def cost_cap_for(model: dict[str, Any]) -> float:
    """Effective cost cap: per-model override or global default."""
    cap = model.get("eval_cost_cap_usd")
    return float(cap) if cap is not None else HARD_COST_CAP_USD


# ── Picker ─────────────────────────────────────────────────────────────────


def pick_next_model(include_paid: bool = False) -> dict[str, Any] | None:
    """Return the next policy-eligible model row to evaluate, or None.

    Pulls candidates in priority order (oldest-evaluated first), then walks
    them applying the policy filter. Skipped rows get logged so the choice
    is auditable.
    """
    where = ["deprecated = FALSE"]
    if not include_paid:
        # Paid models with an explicit cost cap are included automatically;
        # all other paid models require --include-paid.
        where.append("(is_free = TRUE OR eval_cost_cap_usd IS NOT NULL)")
    sql = f"""
        SELECT id, model_id, source, provider, is_free, in_litellm,
               pricing_input, pricing_output, last_evaluated_at,
               eval_cost_cap_usd
        FROM llm_registry
        WHERE {' AND '.join(where)}
        ORDER BY last_evaluated_at NULLS FIRST, last_seen DESC
        LIMIT 100
    """
    with cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall()

    for r in rows:
        ok, reason = is_eval_eligible(
            r["model_id"], r["source"], bool(r["is_free"]), bool(r["in_litellm"])
        )
        if ok:
            return r
        logger.info("  skip %s [%s] — %s", r["model_id"], r["source"], reason)
    return None


def find_model(model_id: str, source: str | None = None) -> dict[str, Any] | None:
    sql = "SELECT * FROM llm_registry WHERE model_id = %s"
    params: list[Any] = [model_id]
    if source:
        sql += " AND source = %s"
        params.append(source)
    sql += " LIMIT 1"
    with cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()


# ── Cost guardrail ─────────────────────────────────────────────────────────


def projected_cost(model: dict[str, Any], prompts: list[dict[str, Any]]) -> float | None:
    """Worst-case projection: assume max_tokens output for every prompt in the suite."""
    p_in = model.get("pricing_input")
    p_out = model.get("pricing_output")
    if p_in is None or p_out is None:
        return None
    # Rough estimate: 1500 tokens total input across all prompts + 512 output each
    est_input = 1500
    est_output = 512 * len(prompts)
    return float(p_in) * est_input + float(p_out) * est_output


# ── Persistence ────────────────────────────────────────────────────────────


def record_eval(model_id_pk: int, prompt: dict[str, Any], result, score: float | None,
                cost_usd: float | None, error: str | None) -> None:
    with cursor() as cur:
        cur.execute(
            """
            INSERT INTO llm_evals (
                model_registry_id, use_case, eval_name, score,
                raw_output, expected_output, latency_ms,
                tokens_input, tokens_output, cost_usd, error_message,
                grader_version
            ) VALUES (
                %s, %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s, %s,
                %s
            )
            """,
            (
                model_id_pk,
                prompt["use_case"],
                prompt["id"],
                score,
                result.output[:4000] if result.output else None,
                prompt.get("expected"),
                result.latency_ms,
                result.tokens_input,
                result.tokens_output,
                cost_usd,
                error,
                GRADER_VERSION,
            ),
        )


def touch_last_evaluated(model_id_pk: int) -> None:
    with cursor() as cur:
        cur.execute(
            "UPDATE llm_registry SET last_evaluated_at = NOW() WHERE id = %s",
            (model_id_pk,),
        )


# ── Runner ─────────────────────────────────────────────────────────────────


def evaluate_one(model: dict[str, Any]) -> dict[str, Any]:
    """Run the appropriate prompt suite against one model, persist results, return summary."""
    model_pk = model["id"]
    model_id = model["model_id"]
    source = model["source"]

    prompts = select_prompts(model)
    cap = cost_cap_for(model)
    tier = "light" if model.get("last_evaluated_at") is None else "full"
    logger.info("Evaluating %s [%s] — %s suite (%d prompts), cap=$%.2f",
                model_id, source, tier, len(prompts), cap)

    proj = projected_cost(model, prompts)
    if proj is not None and proj > cap:
        msg = (f"Projected cost ${proj:.4f} exceeds cap ${cap:.4f} — "
               f"skipping {model_id}")
        logger.warning(msg)
        return {"model": model_id, "skipped": True, "reason": msg}

    summary: dict[str, Any] = {
        "model": model_id,
        "source": source,
        "tier": tier,
        "skipped": False,
        "prompts_run": 0,
        "prompts_failed": 0,
        "total_cost_usd": 0.0,
        "scores": {},
    }

    for prompt in prompts:
        # tool_use prompts use the function-calling API; all others use plain chat.
        if prompt["use_case"] == "tool_use":
            if source != "openrouter":
                result = CallResult(
                    "", None, None, 0,
                    error=f"tool_use_unsupported: source '{source}' does not support function calling",
                )
            else:
                tool_result = call_with_tools(model_id, source, prompt["user"], prompt["tools"])
                result = tool_result.to_call_result()
        else:
            result = call(model_id, source, prompt["user"], prompt.get("system"))

        cost = estimate_cost_usd(
            model.get("pricing_input"), model.get("pricing_output"),
            result.tokens_input, result.tokens_output,
        )
        if result.error:
            logger.warning("  %s [%s] ERROR: %s", prompt["id"], prompt["use_case"], result.error)
            record_eval(model_pk, prompt, result, None, cost, result.error)
            summary["prompts_failed"] += 1
            summary["prompts_run"] += 1
            continue
        try:
            score = float(prompt["grader"](result.output, prompt.get("expected") or ""))
        except Exception as ge:
            logger.warning("  %s grader crashed: %s", prompt["id"], ge)
            score = None
        record_eval(model_pk, prompt, result, score, cost, None)
        summary["scores"][prompt["id"]] = score
        summary["prompts_run"] += 1
        if cost:
            summary["total_cost_usd"] += cost
        logger.info("  %-30s [%-14s] score=%s  latency=%dms  tokens=%s/%s  cost=$%.6f",
                    prompt["id"], prompt["use_case"],
                    f"{score:.2f}" if score is not None else "n/a",
                    result.latency_ms,
                    result.tokens_input, result.tokens_output,
                    cost or 0.0)

    touch_last_evaluated(model_pk)
    get_conn().commit()

    # Aggregate score (mean of non-None grades)
    valid = [s for s in summary["scores"].values() if s is not None]
    summary["mean_score"] = sum(valid) / len(valid) if valid else None
    logger.info("Done. mean_score=%s, cost=$%.6f, prompts %d/%d ok",
                f"{summary['mean_score']:.3f}" if summary["mean_score"] is not None else "n/a",
                summary["total_cost_usd"],
                summary["prompts_run"] - summary["prompts_failed"],
                summary["prompts_run"])
    return summary


def main() -> int:
    p = argparse.ArgumentParser(description="Evaluate one LLM from the registry.")
    p.add_argument("--model", help="Specific model_id to evaluate (skips rotation picker)")
    p.add_argument("--source", help="Constrain --model to this source (openrouter|ollama-cloud)")
    p.add_argument("--include-paid", action="store_true",
                   help="Allow evaluating paid models (default: free-only, for cost safety)")
    args = p.parse_args()

    if args.model:
        model = find_model(args.model, args.source)
        if not model:
            logger.error("Not found: %s (source=%s)", args.model, args.source)
            return 2
    else:
        model = pick_next_model(include_paid=args.include_paid)
        if not model:
            logger.error("No eligible model to evaluate — registry empty?")
            return 1

    try:
        summary = evaluate_one(model)
    except Exception as e:
        get_conn().rollback()
        logger.exception("evaluate_one failed")
        return 1

    if summary.get("skipped"):
        return 0

    mean_str = (f"{summary['mean_score']:.3f}"
                if summary['mean_score'] is not None else "n/a")
    notify(
        "llm_registry",
        f"LLM eval: {summary['model']} → mean_score={mean_str}, "
        f"cost=${summary['total_cost_usd']:.6f}, "
        f"{summary['prompts_run'] - summary['prompts_failed']}/{summary['prompts_run']} prompts ok"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
