-- 007_llm_registry_last_evaluated.sql
-- Phase 2 of LLM Curator — add denormalised last_evaluated_at to llm_registry.
-- The eval rotation picker uses this to find the oldest-evaluated model in O(log n)
-- without joining llm_evals on every pick.

ALTER TABLE llm_registry
    ADD COLUMN IF NOT EXISTS last_evaluated_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_llm_registry_last_evaluated
    ON llm_registry(last_evaluated_at NULLS FIRST);
