-- Migration 10: add webhook.url and webhook.secret to curator_settings.
--
-- webhook.url    — the endpoint llm-curator POSTs to when a proposal is applied
-- webhook.secret — optional HMAC-SHA256 signing secret; set to skip verification

INSERT INTO curator_settings (key, value, updated_at) VALUES
  ('webhook.url',    NULL, NOW()),
  ('webhook.secret', NULL, NOW())
ON CONFLICT (key) DO NOTHING;
