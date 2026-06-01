-- 008_llm_proposals.sql
-- Phase 3 of LLM Curator — proposals table.
--
-- The curator agent runs fortnightly, reads eval scores from llm_evals, and
-- produces a structured proposal describing what should change in
-- litellm_config.yaml. The proposal is NEVER auto-applied in Phase 3 —
-- it's surfaced via CLI / dashboard / Telegram for human review.
--
-- A proposal lifecycle:
--   pending → applied | rejected | superseded
--   (superseded = a newer proposal replaces it without explicit decision)

CREATE TABLE IF NOT EXISTS llm_proposals (
    id                SERIAL PRIMARY KEY,
    generated_at      TIMESTAMPTZ DEFAULT NOW(),

    -- One-line summary suitable for Telegram / dashboard chip
    summary           TEXT NOT NULL,

    -- Snapshot of the current config at the moment of generation
    -- (parsed into a list of {alias, model, fallbacks[]} dicts)
    current_snapshot  JSONB NOT NULL,

    -- Proposed changes: list of objects
    -- Each: {kind: 'replace'|'add'|'remove', alias, old_model?, new_model?,
    --        rationale, evidence: {use_case, current_score, new_score, n_evals}}
    proposed_changes  JSONB NOT NULL,

    -- Aggregate counts (denormalised for quick dashboard reads)
    n_replacements    INTEGER NOT NULL DEFAULT 0,
    n_additions       INTEGER NOT NULL DEFAULT 0,
    n_removals        INTEGER NOT NULL DEFAULT 0,

    -- Lifecycle
    status            TEXT NOT NULL DEFAULT 'pending'
                      CHECK (status IN ('pending','applied','rejected','superseded')),
    reviewed_at       TIMESTAMPTZ,
    reviewer_note     TEXT
);

CREATE INDEX IF NOT EXISTS idx_llm_proposals_status     ON llm_proposals(status);
CREATE INDEX IF NOT EXISTS idx_llm_proposals_generated  ON llm_proposals(generated_at DESC);
