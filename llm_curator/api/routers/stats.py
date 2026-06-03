"""Dashboard summary stats."""
from __future__ import annotations

from fastapi import APIRouter
from llm_curator.db import cursor

router = APIRouter()


@router.get("/")
def get_stats() -> dict:
    with cursor() as cur:
        cur.execute("""
            SELECT
                COUNT(*)                                        AS total_models,
                COUNT(*) FILTER (WHERE is_free = TRUE)         AS free_models,
                COUNT(*) FILTER (WHERE deprecated = TRUE)      AS deprecated_models,
                COUNT(*) FILTER (WHERE in_litellm = TRUE)      AS in_routing,
                COUNT(*) FILTER (WHERE is_free = TRUE
                                   AND deprecated = FALSE)     AS active_free
            FROM llm_registry
        """)
        reg = dict(cur.fetchone())

        cur.execute("""
            SELECT r.model_id, e.tested_at, e.grader_version,
                   ROUND(AVG(e.score) FILTER (WHERE e.score IS NOT NULL)::NUMERIC, 3) AS mean_score
            FROM llm_evals e
            JOIN llm_registry r ON r.id = e.model_registry_id
            WHERE e.tested_at = (SELECT MAX(tested_at) FROM llm_evals)
            GROUP BY r.model_id, e.tested_at, e.grader_version
            LIMIT 1
        """)
        last_eval_row = cur.fetchone()
        last_eval = dict(last_eval_row) if last_eval_row else None
        if last_eval and last_eval.get("tested_at"):
            last_eval["tested_at"] = last_eval["tested_at"].isoformat()
        if last_eval and last_eval.get("mean_score"):
            last_eval["mean_score"] = float(last_eval["mean_score"])

        cur.execute("""
            SELECT id, generated_at, summary, status,
                   n_replacements, n_additions, n_removals
            FROM llm_proposals
            ORDER BY generated_at DESC
            LIMIT 1
        """)
        prop_row = cur.fetchone()
        last_proposal = dict(prop_row) if prop_row else None
        if last_proposal and last_proposal.get("generated_at"):
            last_proposal["generated_at"] = last_proposal["generated_at"].isoformat()

        cur.execute("""
            SELECT COUNT(*) AS unacked
            FROM llm_alerts
            WHERE acknowledged = FALSE
        """)
        alert_row = cur.fetchone()
        open_alerts = int(alert_row["unacked"]) if alert_row else 0

        cur.execute("""
            SELECT COUNT(*) AS unacked,
                   COUNT(*) FILTER (WHERE severity = 'critical') AS critical
            FROM llm_alerts WHERE acknowledged = FALSE
        """)
        ac = cur.fetchone()

    return {
        **reg,
        "open_alerts": open_alerts,
        "critical_alerts": int(ac["critical"]) if ac else 0,
        "last_eval": last_eval,
        "last_proposal": last_proposal,
    }
