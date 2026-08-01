-- Migration: 003_harvested_signals
-- Feature: 004-relational-spine
-- BASELINE: reproduces the harvested_signals table exactly as it existed in the
-- running database (feature 003-songbird-content-generation), which had no
-- migration file until now. On an existing database this introduces NOTHING;
-- it exists so a database can be rebuilt from the migration chain alone
-- (FR-029) and so the table has a supported evolution path going forward.
--
-- Idempotent — safe to run against an existing database.
-- Run with: docker exec -i postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" < 003_harvested_signals.sql

BEGIN;

CREATE TABLE IF NOT EXISTS harvested_signals (
    id                 BIGSERIAL PRIMARY KEY,
    platform           TEXT NOT NULL,
    profile_key        TEXT NOT NULL,
    content_id         TEXT NOT NULL,
    content_type       TEXT NOT NULL,
    published_at       TIMESTAMPTZ NULL,
    caption            TEXT NULL,
    hashtags           TEXT NULL,
    views              BIGINT NULL,
    likes              BIGINT NULL,
    comments           BIGINT NULL,
    subtitle           TEXT NULL,
    content_flow       TEXT NULL,
    summary            TEXT NULL,
    advertisement      BOOLEAN NOT NULL DEFAULT false,
    harvested_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_harvested_signal
    ON harvested_signals (platform, content_id);

CREATE INDEX IF NOT EXISTS ix_signal_profile_engagement
    ON harvested_signals (profile_key, ((COALESCE(likes, 0) + COALESCE(comments, 0))) DESC);

CREATE INDEX IF NOT EXISTS ix_signal_profile_trgm
    ON harvested_signals USING gin (profile_key gin_trgm_ops);

INSERT INTO schema_migrations (version) VALUES ('003_harvested_signals')
ON CONFLICT (version) DO NOTHING;

COMMIT;
