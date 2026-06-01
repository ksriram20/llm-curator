-- 009_llm_alerts.sql
-- Phase 4 of LLM Curator — alerts table.
--
-- An alert is created when the discovery pipeline detects a change that
-- affects PARCON's actually-running stack (a model in litellm_config moves
-- to deprecated, or an Ollama Cloud model we use silently flips to paid).
-- Alerts are categorised by severity; CRITICAL ones get Telegram-relayed
-- immediately, INFO ones just sit in the dashboard.
--
-- Acknowledgement is manual (CLI or dashboard) — keeps the unack queue
-- meaningful as a real attention surface.

CREATE TABLE IF NOT EXISTS llm_alerts (
    id              SERIAL PRIMARY KEY,
    generated_at    TIMESTAMPTZ DEFAULT NOW(),

    severity        TEXT NOT NULL
                    CHECK (severity IN ('info','warn','critical')),
    category        TEXT NOT NULL,          -- IN_USE_DEPRECATED | IN_USE_PAID | NEW_FREE_MODEL | MODEL_RESURRECTED ...
    model_id        TEXT,                   -- model the alert is about (NULL for aggregate)
    source          TEXT,                   -- openrouter | ollama-cloud
    litellm_alias   TEXT,                   -- which alias references this model (if any)

    message         TEXT NOT NULL,          -- human-readable summary

    acknowledged    BOOLEAN DEFAULT FALSE,
    acknowledged_at TIMESTAMPTZ,
    ack_note        TEXT
);

CREATE INDEX IF NOT EXISTS idx_llm_alerts_unack    ON llm_alerts(acknowledged, generated_at DESC);
CREATE INDEX IF NOT EXISTS idx_llm_alerts_severity ON llm_alerts(severity, generated_at DESC);
CREATE INDEX IF NOT EXISTS idx_llm_alerts_category ON llm_alerts(category);
