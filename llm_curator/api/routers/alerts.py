"""Alerts endpoints (read-only for v0.2)."""
from __future__ import annotations

from typing import Optional
from fastapi import APIRouter, Query
from llm_curator.db import cursor
from llm_curator.api import serialize

router = APIRouter()


@router.get("/count")
def get_alert_count() -> dict:
    with cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS count FROM llm_alerts WHERE acknowledged = FALSE"
        )
        row = cur.fetchone()
    return {"count": int(row["count"]) if row else 0}


@router.get("/")
def get_alerts(
    severity: Optional[str] = Query(None),
    include_acked: bool = Query(False),
    limit: int = Query(100, le=500),
) -> list[dict]:
    conditions = ["1=1"]
    params: dict = {}

    if not include_acked:
        conditions.append("acknowledged = FALSE")
    if severity:
        conditions.append("severity = %(severity)s")
        params["severity"] = severity

    params["limit"] = limit

    with cursor() as cur:
        cur.execute(f"""
            SELECT id, generated_at, severity, category,
                   model_id, source, litellm_alias, message,
                   acknowledged, acknowledged_at, ack_note
            FROM llm_alerts
            WHERE {' AND '.join(conditions)}
            ORDER BY
                CASE severity WHEN 'critical' THEN 0 WHEN 'warn' THEN 1 ELSE 2 END,
                generated_at DESC
            LIMIT %(limit)s
        """, params)
        rows = cur.fetchall()

    return [serialize(r) for r in rows]
