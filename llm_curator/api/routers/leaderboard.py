"""Eval leaderboard endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Query
from llm_curator.db import cursor
from llm_curator.api import serialize

router = APIRouter()


@router.get("/")
def get_leaderboard(
    grader_version: str = Query("v4"),
    limit: int = Query(50, le=500),
) -> list[dict]:
    with cursor() as cur:
        cur.execute("""
            SELECT
                r.model_id,
                r.source,
                r.provider,
                r.is_free,
                r.in_litellm,
                r.deprecated,
                e.grader_version,
                ROUND(AVG(e.score) FILTER (WHERE e.score IS NOT NULL)::NUMERIC, 3)
                    AS mean_score,
                ROUND(AVG(e.score) FILTER (
                    WHERE e.use_case = 'reasoning' AND e.score IS NOT NULL)::NUMERIC, 3)
                    AS reasoning,
                ROUND(AVG(e.score) FILTER (
                    WHERE e.use_case = 'extraction' AND e.score IS NOT NULL)::NUMERIC, 3)
                    AS extraction,
                ROUND(AVG(e.score) FILTER (
                    WHERE e.use_case = 'classification' AND e.score IS NOT NULL)::NUMERIC, 3)
                    AS classification,
                ROUND(AVG(e.score) FILTER (
                    WHERE e.use_case = 'summarization' AND e.score IS NOT NULL)::NUMERIC, 3)
                    AS summarization,
                ROUND(AVG(e.score) FILTER (
                    WHERE e.use_case = 'tool_use' AND e.score IS NOT NULL)::NUMERIC, 3)
                    AS tool_use,
                ROUND(AVG(e.score) FILTER (
                    WHERE e.use_case = 'structured_data' AND e.score IS NOT NULL)::NUMERIC, 3)
                    AS structured_data,
                ROUND(AVG(e.score) FILTER (
                    WHERE e.use_case = 'code_exec' AND e.score IS NOT NULL)::NUMERIC, 3)
                    AS code_exec,
                COUNT(e.id)        AS n_evals,
                MAX(e.tested_at)   AS last_tested,
                ROUND(AVG(e.latency_ms) FILTER (WHERE e.latency_ms IS NOT NULL)::NUMERIC)
                    AS avg_latency_ms
            FROM llm_registry r
            JOIN llm_evals e ON e.model_registry_id = r.id
            WHERE e.grader_version = %(gv)s
            GROUP BY r.model_id, r.source, r.provider, r.is_free,
                     r.in_litellm, r.deprecated, e.grader_version
            ORDER BY mean_score DESC NULLS LAST
            LIMIT %(limit)s
        """, {"gv": grader_version, "limit": limit})
        rows = cur.fetchall()

    return [serialize(r) for r in rows]


@router.get("/versions")
def get_grader_versions() -> list[str]:
    with cursor() as cur:
        cur.execute(
            "SELECT DISTINCT grader_version FROM llm_evals ORDER BY grader_version DESC"
        )
        return [r["grader_version"] for r in cur.fetchall()]
