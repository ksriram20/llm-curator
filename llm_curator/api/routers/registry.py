"""Model registry endpoints."""
from __future__ import annotations

from typing import Optional
from fastapi import APIRouter, Query
from llm_curator.db import cursor
from llm_curator.api import serialize

router = APIRouter()


@router.get("/")
def get_registry(
    source: Optional[str] = Query(None),
    free_only: bool = Query(False),
    deprecated: Optional[bool] = Query(None),
    in_routing: Optional[bool] = Query(None),
    limit: int = Query(200, le=1000),
) -> list[dict]:
    conditions = ["1=1"]
    params: dict = {}

    if source:
        conditions.append("source = %(source)s")
        params["source"] = source
    if free_only:
        conditions.append("is_free = TRUE")
    if deprecated is not None:
        conditions.append("deprecated = %(deprecated)s")
        params["deprecated"] = deprecated
    if in_routing is not None:
        conditions.append("in_litellm = %(in_routing)s")
        params["in_routing"] = in_routing

    params["limit"] = limit

    with cursor() as cur:
        cur.execute(f"""
            SELECT model_id, source, provider, is_free, in_litellm, deprecated,
                   context_length, pricing_input, pricing_output,
                   first_seen, last_seen, last_evaluated_at, deprecated_at
            FROM llm_registry
            WHERE {' AND '.join(conditions)}
            ORDER BY last_seen DESC
            LIMIT %(limit)s
        """, params)
        rows = cur.fetchall()

    return [serialize(r) for r in rows]


@router.get("/sources")
def get_sources() -> list[str]:
    with cursor() as cur:
        cur.execute("SELECT DISTINCT source FROM llm_registry ORDER BY source")
        return [r["source"] for r in cur.fetchall()]
