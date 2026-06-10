-- Migration 08: curator_settings — key/value store for provider API configuration.
--
-- Settings written via the UI are stored here. eval_providers.py reads DB first,
-- falls back to env vars, so existing .env deployments continue to work unchanged.

CREATE TABLE IF NOT EXISTS curator_settings (
    key         TEXT PRIMARY KEY,
    value       TEXT,                           -- NULL = not configured
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);

-- Seed default keys. ON CONFLICT DO NOTHING so re-running is safe.
INSERT INTO curator_settings (key, value) VALUES
    ('openrouter.api_key',    NULL),
    ('ollama.base_url',       NULL),
    ('mistral.api_key',       NULL),
    ('deepseek.api_key',      NULL),
    ('google.api_key',        NULL),
    ('anthropic.api_key',     NULL)
ON CONFLICT (key) DO NOTHING;

COMMENT ON TABLE curator_settings IS
    'Provider API keys and configuration. Managed via the Settings UI. '
    'env vars are the fallback when a key is NULL.';
