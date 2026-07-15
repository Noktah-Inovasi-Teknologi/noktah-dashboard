-- Initialize databases
-- Note: Main database is created automatically from POSTGRES_DB env var

-- Create additional databases using environment variables
-- Use psql variables to get environment variable values
\set dev_db `echo "$POSTGRES_DB_DEV"`
\set prefect_db `echo "$POSTGRES_DB_PREFECT"`

-- Create databases
CREATE DATABASE :dev_db;
CREATE DATABASE :prefect_db;

-- Grant privileges to main user
\set main_user `echo "$POSTGRES_USER"`
GRANT ALL PRIVILEGES ON DATABASE :dev_db TO :main_user;
GRANT ALL PRIVILEGES ON DATABASE :prefect_db TO :main_user;

-- Client Knowledge Base (feature 001-client-knowledge-base)
-- Extensions: pgcrypto (gen_random_uuid), pg_trgm (fuzzy client/subject matching)
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

-- At most one current record per client+subject (enforces FR-010 supersession invariant)
CREATE UNIQUE INDEX IF NOT EXISTS uq_current_client_subject
    ON knowledge_records (client_key, subject_key)
    WHERE superseded_by IS NULL;

-- Fast current-record lookups per client
CREATE INDEX IF NOT EXISTS ix_client_current
    ON knowledge_records (client_key)
    WHERE superseded_by IS NULL;

-- Historical / time-range queries
CREATE INDEX IF NOT EXISTS ix_client_timestamp
    ON knowledge_records (client_key, "timestamp" DESC);

-- Fuzzy client + subject + information matching (pg_trgm) for FR-003b and retrieval ranking
CREATE INDEX IF NOT EXISTS ix_client_key_trgm
    ON knowledge_records USING gin (client_key gin_trgm_ops);
CREATE INDEX IF NOT EXISTS ix_subject_trgm
    ON knowledge_records USING gin (subject gin_trgm_ops);
CREATE INDEX IF NOT EXISTS ix_information_trgm
    ON knowledge_records USING gin (information gin_trgm_ops);

-- Social Content Harvest (feature 002-social-content-harvest)
-- Cross-run de-duplication ledger (FR-021). drive_target is the stable per-profile
-- Drive folder id under HARVEST_DRIVE_PARENT_ID, NOT the per-run analysis Sheet.
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

-- Songbird content generation (feature 003-songbird-content-generation)
-- "What hits" signal store. The de-dup ledger (harvested_items) intentionally
-- keeps only status; engagement counts + roach analysis otherwise live only in
-- the per-account Google Sheets. This table mirrors those rich fields into
-- Postgres so songbird can rank top performers by engagement via SQL. Populated
-- by the social-harvest engine alongside each delivered Sheet row.
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
    -- Manual TRUE/FALSE flag: was this content run as a paid ad/boost? Instagram
    -- does not expose this publicly, so it is reviewer-assigned (default false).
    advertisement      BOOLEAN NOT NULL DEFAULT false,
    harvested_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One signal row per harvested item; re-harvests upsert the freshest metrics.
CREATE UNIQUE INDEX IF NOT EXISTS uq_harvested_signal
    ON harvested_signals (platform, content_id);

-- Rank a handle's top performers by engagement (songbird.signal.top_performers).
CREATE INDEX IF NOT EXISTS ix_signal_profile_engagement
    ON harvested_signals (profile_key, ((COALESCE(likes, 0) + COALESCE(comments, 0))) DESC);

-- Fuzzy handle matching (own/competitor lookups may not match casing exactly).
CREATE INDEX IF NOT EXISTS ix_signal_profile_trgm
    ON harvested_signals USING gin (profile_key gin_trgm_ops);