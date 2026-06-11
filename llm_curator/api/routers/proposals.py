"""Proposals endpoints.

Endpoints:
  GET  /api/proposals/                  — list proposals (latest first)
  GET  /api/proposals/{id}              — full proposal detail
  GET  /api/proposals/{id}/export       — clean JSON export for external integration
  POST /api/proposals/{id}/apply        — mark applied, fire webhook
  POST /api/proposals/{id}/reject       — mark rejected
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from llm_curator.api import serialize
from llm_curator.db import cursor

router = APIRouter()


class ReviewBody(BaseModel):
    note: str = ""


def _fetch_one(proposal_id: int) -> dict:
    with cursor() as cur:
        cur.execute("""
            SELECT id, generated_at, summary, status,
                   n_replacements, n_additions, n_removals,
                   reviewed_at, reviewer_note,
                   proposed_changes, current_snapshot
            FROM llm_proposals WHERE id = %(id)s
        """, {"id": proposal_id})
        row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Proposal not found")
    return serialize(row)


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


@router.get("/{proposal_id}/export")
def export_proposal(proposal_id: int) -> dict:
    """Return a repo-agnostic JSON export of a proposal.

    Consumers can use this to apply routing changes in any codebase
    regardless of whether it uses LiteLLM, direct API calls, or a custom router.
    """
    from llm_curator.webhook import build_export
    return build_export(_fetch_one(proposal_id))


@router.post("/{proposal_id}/apply")
def apply_proposal(proposal_id: int, body: ReviewBody) -> dict:
    """Mark a proposal as applied and fire the configured webhook (if any)."""
    proposal = _fetch_one(proposal_id)
    if proposal["status"] == "applied":
        raise HTTPException(status_code=409, detail="Proposal already applied")

    with cursor() as cur:
        cur.execute("""
            UPDATE llm_proposals
               SET status = 'applied',
                   reviewed_at = NOW(),
                   reviewer_note = %(note)s
             WHERE id = %(id)s
        """, {"id": proposal_id, "note": body.note or None})

    # Fire webhook asynchronously (best-effort — failure never blocks the response)
    try:
        from llm_curator.webhook import deliver
        updated = _fetch_one(proposal_id)
        deliver("proposal.applied", updated)
    except Exception:
        pass

    return {"ok": True, "status": "applied"}


@router.post("/{proposal_id}/reject")
def reject_proposal(proposal_id: int, body: ReviewBody) -> dict:
    """Mark a proposal as rejected."""
    proposal = _fetch_one(proposal_id)
    if proposal["status"] in ("applied", "rejected"):
        raise HTTPException(status_code=409, detail=f"Proposal already {proposal['status']}")

    with cursor() as cur:
        cur.execute("""
            UPDATE llm_proposals
               SET status = 'rejected',
                   reviewed_at = NOW(),
                   reviewer_note = %(note)s
             WHERE id = %(id)s
        """, {"id": proposal_id, "note": body.note or None})

    return {"ok": True, "status": "rejected"}


@router.get("/{proposal_id}")
def get_proposal(proposal_id: int) -> dict:
    return _fetch_one(proposal_id)
