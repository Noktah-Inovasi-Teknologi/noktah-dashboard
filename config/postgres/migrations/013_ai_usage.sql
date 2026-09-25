-- Migration: 013_ai_usage
-- Feature: AI model lists (shared/noktah_ai) and the Hub's Biaya AI page
--
-- One append-only row per AI call for callers that have no table of their own to
-- carry the cost. Today that is songbird (case `generation`, written by Prefect's
-- tasks/openrouter_tasks.py); before this its spend was only printed in run logs.
-- The other cases already record cost where their results live:
--   summary, intake, intake_image  ai_ledger (hub-api)
--   image, video                   content_extractions / extraction_quarantine (roach via Prefect)
-- The Hub's GET /v1/ai/costs reads all three per case and month.
--
-- Not counted against the Hub's monthly AI cap: that cap covers ai_ledger only.
--
-- Run with: docker exec -i postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" < 013_ai_usage.sql

BEGIN;

CREATE TABLE IF NOT EXISTS ai_usage (
    id                BIGSERIAL PRIMARY KEY,
    ai_case           TEXT NOT NULL,
    call_site         TEXT NOT NULL,
    client_name       TEXT,
    model             TEXT,
    provider          TEXT,
    prompt_tokens     INTEGER,
    completion_tokens INTEGER,
    cost_usd          NUMERIC(12, 6) NOT NULL DEFAULT 0,
    at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT chk_ai_usage_case CHECK (ai_case IN (
        'summary', 'intake', 'intake_image', 'generation', 'image', 'video'))
);
CREATE INDEX IF NOT EXISTS idx_ai_usage_case_at ON ai_usage (ai_case, at);

INSERT INTO schema_migrations (version) VALUES ('013_ai_usage')
ON CONFLICT (version) DO NOTHING;

COMMIT;
