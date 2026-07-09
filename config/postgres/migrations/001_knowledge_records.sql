-- Migration: 001_knowledge_records
-- Feature: 001-client-knowledge-base
-- Idempotent — safe to run against an existing database that predates this feature.
-- Run with: docker exec -i postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" < 001_knowledge_records.sql

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE IF NOT EXISTS knowledge_records (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_name        TEXT NOT NULL,
    client_key         TEXT NOT NULL,
    subject            TEXT NOT NULL,
    subject_key        TEXT NOT NULL,
    information        TEXT NOT NULL,
    "timestamp"        TIMESTAMPTZ NOT NULL DEFAULT now(),
    superseded_by      UUID NULL REFERENCES knowledge_records(id) DEFERRABLE INITIALLY DEFERRED,
    source_type        TEXT NOT NULL CHECK (source_type IN ('plain_text', 'google_doc', 'google_sheet')),
    source_reference   TEXT NULL,
    created_by         TEXT NULL,
    CONSTRAINT chk_no_self_reference CHECK (superseded_by IS NULL OR superseded_by <> id),
    CONSTRAINT chk_client_name_nonempty CHECK (btrim(client_name) <> ''),
    CONSTRAINT chk_subject_nonempty CHECK (btrim(subject) <> ''),
    CONSTRAINT chk_information_nonempty CHECK (btrim(information) <> ''),
    CONSTRAINT chk_information_length CHECK (char_length(information) <= 4000),
    CONSTRAINT chk_source_reference_required CHECK (
        source_type = 'plain_text' OR source_reference IS NOT NULL
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_current_client_subject
    ON knowledge_records (client_key, subject_key)
    WHERE superseded_by IS NULL;

CREATE INDEX IF NOT EXISTS ix_client_current
    ON knowledge_records (client_key)
    WHERE superseded_by IS NULL;

CREATE INDEX IF NOT EXISTS ix_client_timestamp
    ON knowledge_records (client_key, "timestamp" DESC);

CREATE INDEX IF NOT EXISTS ix_client_key_trgm
    ON knowledge_records USING gin (client_key gin_trgm_ops);
CREATE INDEX IF NOT EXISTS ix_subject_trgm
    ON knowledge_records USING gin (subject gin_trgm_ops);
CREATE INDEX IF NOT EXISTS ix_information_trgm
    ON knowledge_records USING gin (information gin_trgm_ops);
