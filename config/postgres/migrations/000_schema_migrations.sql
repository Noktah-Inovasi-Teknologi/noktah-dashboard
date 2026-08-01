-- Migration: 000_schema_migrations
-- Feature: 004-relational-spine
-- Applied-version tracking. Must lead the chain: 004 and 005 record themselves
-- here on success, so this table has to exist before either runs.
-- Idempotent — safe to run against an existing database.
-- Run with: docker exec -i postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" < 000_schema_migrations.sql

BEGIN;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version      TEXT PRIMARY KEY,
    applied_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO schema_migrations (version) VALUES ('000_schema_migrations')
ON CONFLICT (version) DO NOTHING;

COMMIT;
