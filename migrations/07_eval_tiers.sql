-- Migration 07: add eval_cost_cap_usd to llm_registry for per-model cost cap overrides.
--
-- NULL means "use the global HARD_COST_CAP_USD default" ($0.10).
-- Setting a value enables the paid model to be included in automatic eval rotation
-- without needing the --include-paid CLI flag.

ALTER TABLE llm_registry
    ADD COLUMN IF NOT EXISTS eval_cost_cap_usd NUMERIC(10, 6) DEFAULT NULL;

CREATE INDEX IF NOT EXISTS idx_llm_registry_eval_cost_cap
    ON llm_registry(eval_cost_cap_usd)
    WHERE eval_cost_cap_usd IS NOT NULL;

COMMENT ON COLUMN llm_registry.eval_cost_cap_usd IS
    'Per-model cost cap override for eval runs (USD). '
    'NULL = use global HARD_COST_CAP_USD ($0.10). '
    'Non-NULL paid models are included in auto-rotation without --include-paid.';
