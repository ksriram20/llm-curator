"""Proposals endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Query
from llm_curator.db import cursor
from llm_curator.api import serialize

router = APIRouter()


@router.get("/")
def get_proposals(limit: int = Query(20, le=100)) -> list[dict]:
    with cursor() as cur:
        cur.execute("""
            SELECT id, generated_at, summary, status,
                   n_replacements, n_additions, n_removals,
                   reviewed_at, reviewer_note, proposed_changes
            FROM llm_proposals
            ORDER BY generated_at DESC
            LIMIT %(limit)s
        """, {"limit": limit})
        rows = cur.fetchall()
    return [serialize(r) for r in rows]


@router.get("/{proposal_id}")
def get_proposal(proposal_id: int) -> dict:
    with cursor() as cur:
        cur.execute("""
            SELECT id, generated_at, summary, status,
                   n_replacements, n_additions, n_removals,
                   reviewed_at, reviewer_note,
                   proposed_changes, current_snapshot
            FROM llm_proposals
            WHERE id = %(id)s
        """, {"id": proposal_id})
        row = cur.fetchone()
    if not row:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Proposal not found")
    return serialize(row)
