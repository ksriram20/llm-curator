-- 006_llm_registry.sql
-- Phase 1 of LLM Curator — registry, evals, and discovery audit tables.
-- The registry holds ALL known LLMs across providers (free + paid, in-use or not).
-- The evals table holds graded performance per model per use case (populated by Phase 2).
-- The discovery_runs table is an audit trail of daily refresh jobs.

-- ── Registry: catalog of all known LLMs ──────────────────────────────────────
CREATE TABLE IF NOT EXISTS llm_registry (
    id                  SERIAL PRIMARY KEY,

    -- Identity
    model_id            TEXT NOT NULL,          -- e.g. 'deepseek/deepseek-v4-flash'
    source              TEXT NOT NULL,          -- 'openrouter' | 'ollama-cloud' | 'deepseek-api' | 'mistral-api'
    provider            TEXT,                   -- 'deepseek' | 'google' | 'meta' | 'qwen' etc.
    display_name        TEXT,                   -- 'DeepSeek V4 Flash'

    -- Capabilities
    context_length      INTEGER,
    max_completion_tokens INTEGER,
    modalities          TEXT[] DEFAULT '{}',    -- ['text','image','audio','video']
    supports_tools      BOOLEAN DEFAULT FALSE,
    supports_reasoning  BOOLEAN DEFAULT FALSE,
    supports_vision     BOOLEAN DEFAULT FALSE,
    supports_audio      BOOLEAN DEFAULT FALSE,

    -- Pricing (USD per token; NULL = unknown)
    pricing_input       NUMERIC(15, 12),
    pricing_output      NUMERIC(15, 12),
    is_free             BOOLEAN DEFAULT FALSE,

    -- Metadata
    knowledge_cutoff    DATE,
    description         TEXT,
    raw_metadata        JSONB,                  -- full payload from source API

    -- PARCON usage tracking
    in_litellm          BOOLEAN DEFAULT FALSE,  -- currently mapped in litellm_config.yaml?
    litellm_alias       TEXT,                   -- our alias if in litellm (e.g. 'deepseek-chat')
    tier_suggestion     TEXT,                   -- 'reasoning' | 'standard' | 'free' | 'sensitive'
    notes               TEXT,                   -- manual notes / curator decisions

    -- Lifecycle
    first_seen          TIMESTAMPTZ DEFAULT NOW(),
    last_seen           TIMESTAMPTZ DEFAULT NOW(),
    deprecated          BOOLEAN DEFAULT FALSE,
    deprecated_at       TIMESTAMPTZ,

    UNIQUE (model_id, source)
);

CREATE INDEX IF NOT EXISTS idx_llm_registry_source       ON llm_registry(source);
CREATE INDEX IF NOT EXISTS idx_llm_registry_is_free      ON llm_registry(is_free);
CREATE INDEX IF NOT EXISTS idx_llm_registry_in_litellm   ON llm_registry(in_litellm);
CREATE INDEX IF NOT EXISTS idx_llm_registry_deprecated   ON llm_registry(deprecated);
CREATE INDEX IF NOT EXISTS idx_llm_registry_provider     ON llm_registry(provider);

-- ── Evals: graded performance per model per use case ────────────────────────
CREATE TABLE IF NOT EXISTS llm_evals (
    id                  SERIAL PRIMARY KEY,
    model_registry_id   INTEGER NOT NULL REFERENCES llm_registry(id) ON DELETE CASCADE,
    use_case            TEXT NOT NULL,          -- 'reasoning' | 'extraction' | 'classification' | 'summarization' | 'tool_use'
    eval_name           TEXT NOT NULL,          -- specific eval (e.g. 'mmlu_subset', 'parcon_msme_extract')
    score               NUMERIC(5, 4),          -- 0.0 to 1.0
    raw_output          TEXT,                   -- model output for inspection
    expected_output     TEXT,                   -- ground truth (when applicable)
    latency_ms          INTEGER,
    tokens_input        INTEGER,
    tokens_output       INTEGER,
    cost_usd            NUMERIC(10, 8),
    error_message       TEXT,
    tested_at           TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_llm_evals_model     ON llm_evals(model_registry_id);
CREATE INDEX IF NOT EXISTS idx_llm_evals_use_case  ON llm_evals(use_case);
CREATE INDEX IF NOT EXISTS idx_llm_evals_tested_at ON llm_evals(tested_at DESC);

-- ── Discovery runs: audit trail of daily refresh jobs ───────────────────────
CREATE TABLE IF NOT EXISTS llm_discovery_runs (
    id                  SERIAL PRIMARY KEY,
    source              TEXT NOT NULL,          -- 'openrouter' | 'ollama-cloud'
    started_at          TIMESTAMPTZ DEFAULT NOW(),
    finished_at         TIMESTAMPTZ,
    models_seen         INTEGER DEFAULT 0,
    models_new          INTEGER DEFAULT 0,
    models_updated      INTEGER DEFAULT 0,
    models_deprecated   INTEGER DEFAULT 0,
    success             BOOLEAN DEFAULT FALSE,
    error_message       TEXT
);

CREATE INDEX IF NOT EXISTS idx_llm_discovery_runs_source ON llm_discovery_runs(source);
CREATE INDEX IF NOT EXISTS idx_llm_discovery_runs_started ON llm_discovery_runs(started_at DESC);
