-- Add structured, translatable message fields to alerts.
--
-- Alert title/body were baked in English by the prediction/scoring tasks, so
-- the dashboard could never localize them (they arrive as finished English
-- data, not UI strings). We add a stable message_key + message_params (JSONB)
-- so the frontend can render the alert via i18n (t(key, params)). title/body
-- stay as the English fallback and for the WhatsApp/SMS notification path.
--
-- Idempotent: safe to re-run.

ALTER TABLE alerts ADD COLUMN IF NOT EXISTS message_key    TEXT;
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS message_params JSONB;
