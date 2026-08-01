-- Migration: 002_harvested_items
-- Feature: 004-relational-spine
-- BASELINE: reproduces the harvested_items table exactly as it existed in the
-- running database (feature 002-social-content-harvest), which — like
-- harvested_signals — had no migration file until now. On an existing database
-- this introduces NOTHING; it exists so a database can be rebuilt from the
-- migration chain alone (FR-029) and so the table has a supported evolution
-- path going forward (the audit finding this feature exists to fix).
--
-- Idempotent — safe to run against an existing database.
-- Run with: docker exec -i postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" < 002_harvested_items.sql

BEGIN;

CREATE TABLE IF NOT EXISTS harvested_items (
    id                 BIGSERIAL PRIMARY KEY,
    platform           TEXT NOT NULL,
    profile_key        TEXT NOT NULL,
    content_id         TEXT NOT NULL,
    drive_target       TEXT NOT NULL,
    content_type       TEXT NOT NULL,
    collected_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    drive_file_id      TEXT NULL,
    analysis_status    TEXT NOT NULL DEFAULT 'pending'
        CHECK (analysis_status IN ('pending', 'success', 'failed'))
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_harvested_item
    ON harvested_items (platform, content_id, drive_target);

CREATE INDEX IF NOT EXISTS ix_harvested_target_profile
    ON harvested_items (drive_target, profile_key);

INSERT INTO schema_migrations (version) VALUES ('002_harvested_items')
ON CONFLICT (version) DO NOTHING;

COMMIT;
