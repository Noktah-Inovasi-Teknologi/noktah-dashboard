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

-- Relational Spine (feature 004-relational-spine)
-- Applied-version tracking, so a database built here (the greenfield path) and one
-- built from config/postgres/migrations/ (the upgrade path) both record the same
-- history. Every migration through 006 is mirrored below; this table's own
-- creation IS migration 000, so it is recorded first.
CREATE TABLE IF NOT EXISTS schema_migrations (
    version      TEXT PRIMARY KEY,
    applied_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO schema_migrations (version) VALUES ('000_schema_migrations')
ON CONFLICT (version) DO NOTHING;

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

INSERT INTO schema_migrations (version) VALUES ('001_knowledge_records')
ON CONFLICT (version) DO NOTHING;

-- Relational Spine, continued (feature 004-relational-spine, migration 004)
-- clients, client_aliases, accounts, account_handles, client_account_roles,
-- account_follower_observations, runs, briefs. See
-- config/postgres/migrations/004_relational_spine.sql for the migration this
-- mirrors and specs/004-relational-spine/data-model.md for the rationale.

CREATE TABLE IF NOT EXISTS clients (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_key         TEXT NOT NULL UNIQUE,
    display_name       TEXT NOT NULL,
    is_active          BOOLEAN NOT NULL DEFAULT true,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT chk_client_key_nonempty CHECK (btrim(client_key) <> ''),
    CONSTRAINT chk_display_name_nonempty CHECK (btrim(display_name) <> '')
);

CREATE TABLE IF NOT EXISTS client_aliases (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id          UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    alias_key          TEXT NOT NULL,
    alias_text         TEXT NOT NULL,
    source             TEXT NOT NULL CHECK (source IN
                           ('clients_sheet', 'components_block', 'knowledge_base', 'former_name', 'manual')),
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT chk_alias_key_nonempty CHECK (btrim(alias_key) <> '')
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_client_alias_key ON client_aliases (alias_key);
CREATE INDEX IF NOT EXISTS ix_client_aliases_client ON client_aliases (client_id);

CREATE TABLE IF NOT EXISTS accounts (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    platform           TEXT NOT NULL CHECK (platform IN ('instagram', 'tiktok')),
    is_active          BOOLEAN NOT NULL DEFAULT true,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_account_id_platform UNIQUE (id, platform)
);

CREATE TABLE IF NOT EXISTS account_handles (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id         UUID NOT NULL,
    platform           TEXT NOT NULL CHECK (platform IN ('instagram', 'tiktok')),
    handle_key         TEXT NOT NULL,
    handle_text        TEXT NOT NULL,
    identifier_kind    TEXT NOT NULL CHECK (identifier_kind IN ('handle', 'opaque_id')),
    is_current         BOOLEAN NOT NULL DEFAULT true,
    first_seen_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT chk_handle_key_nonempty CHECK (btrim(handle_key) <> ''),
    CONSTRAINT fk_account_handles_account_platform
        FOREIGN KEY (account_id, platform) REFERENCES accounts (id, platform) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_account_handle_platform_key ON account_handles (platform, handle_key);
CREATE UNIQUE INDEX IF NOT EXISTS uq_account_handle_current ON account_handles (account_id) WHERE is_current;
CREATE INDEX IF NOT EXISTS ix_account_handles_account ON account_handles (account_id);

CREATE TABLE IF NOT EXISTS client_account_roles (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id          UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    account_id         UUID NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    role               TEXT NOT NULL CHECK (role IN ('owned', 'competitor', 'reference')),
    is_active          BOOLEAN NOT NULL DEFAULT true,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_client_account_pair ON client_account_roles (client_id, account_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_account_owned_active
    ON client_account_roles (account_id) WHERE role = 'owned' AND is_active;
CREATE INDEX IF NOT EXISTS ix_client_account_roles_account ON client_account_roles (account_id);
CREATE INDEX IF NOT EXISTS ix_client_account_roles_client ON client_account_roles (client_id);

CREATE TABLE IF NOT EXISTS account_follower_observations (
    id                 BIGSERIAL PRIMARY KEY,
    account_id         UUID NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    observed_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    follower_count     BIGINT NOT NULL,
    run_id             UUID NULL
);

CREATE INDEX IF NOT EXISTS ix_follower_observations_account_time
    ON account_follower_observations (account_id, observed_at DESC);

CREATE TABLE IF NOT EXISTS runs (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    kind               TEXT NOT NULL CHECK (kind IN ('collection', 'generation')),
    flow_name          TEXT NOT NULL,
    flow_run_name      TEXT NULL,
    flow_run_id        UUID NULL,
    client_id          UUID NULL REFERENCES clients(id),
    started_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at           TIMESTAMPTZ NULL,
    status             TEXT NULL,
    summary            JSONB NULL
);

CREATE INDEX IF NOT EXISTS ix_runs_client ON runs (client_id);
CREATE INDEX IF NOT EXISTS ix_runs_flow_run_name ON runs (flow_run_name);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_follower_observations_run') THEN
        ALTER TABLE account_follower_observations
            ADD CONSTRAINT fk_follower_observations_run FOREIGN KEY (run_id) REFERENCES runs(id);
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS briefs (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id             UUID NULL REFERENCES runs(id),
    client_id          UUID NOT NULL REFERENCES clients(id),
    bentuk             TEXT NULL,
    topik              TEXT NULL,
    planned_date       DATE NULL,
    payload            JSONB NULL,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_briefs_client ON briefs (client_id);
CREATE INDEX IF NOT EXISTS ix_briefs_run ON briefs (run_id);

INSERT INTO schema_migrations (version) VALUES ('004_relational_spine')
ON CONFLICT (version) DO NOTHING;

-- Relational Spine, continued (migration 005): link knowledge_records to clients.
-- Nullable, unlike the harvest tables' account_id below — FR-017 requires an
-- unresolvable record to stay retained and reported, not rejected.
ALTER TABLE knowledge_records ADD COLUMN IF NOT EXISTS client_id UUID NULL;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_knowledge_client') THEN
        ALTER TABLE knowledge_records
            ADD CONSTRAINT fk_knowledge_client FOREIGN KEY (client_id) REFERENCES clients(id);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS ix_knowledge_records_client
    ON knowledge_records (client_id)
    WHERE superseded_by IS NULL;

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
        CHECK (analysis_status IN ('pending', 'success', 'failed')),
    -- Relational Spine (migration 005): link to accounts/runs. Declared NULL
    -- here and enforced by a named CHECK below (migration 006) rather than a
    -- native NOT NULL, and the FKs are added by explicit-name ALTER rather
    -- than inline REFERENCES, so a database built here and one built via the
    -- migration chain produce byte-identical pg_constraint / is_nullable
    -- (see specs/004-relational-spine/contracts/migrations.md).
    account_id         UUID NULL,
    run_id             UUID NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_harvested_item
    ON harvested_items (platform, content_id, drive_target);

CREATE INDEX IF NOT EXISTS ix_harvested_target_profile
    ON harvested_items (drive_target, profile_key);

CREATE INDEX IF NOT EXISTS ix_harvested_items_account ON harvested_items (account_id);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_item_account') THEN
        ALTER TABLE harvested_items
            ADD CONSTRAINT fk_item_account FOREIGN KEY (account_id) REFERENCES accounts(id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_item_run') THEN
        ALTER TABLE harvested_items
            ADD CONSTRAINT fk_item_run FOREIGN KEY (run_id) REFERENCES runs(id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_item_account') THEN
        ALTER TABLE harvested_items
            ADD CONSTRAINT chk_item_account CHECK (account_id IS NOT NULL);
    END IF;
END $$;

INSERT INTO schema_migrations (version) VALUES ('002_harvested_items')
ON CONFLICT (version) DO NOTHING;

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
    harvested_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Relational Spine (migration 005/006) — see the matching comment on
    -- harvested_items above for why these are added as NULL + explicit-named
    -- ALTER rather than inline NOT NULL / REFERENCES.
    account_id         UUID NULL,
    run_id             UUID NULL
);

-- One signal row per harvested item; re-harvests upsert the freshest metrics.
CREATE UNIQUE INDEX IF NOT EXISTS uq_harvested_signal
    ON harvested_signals (platform, content_id);

-- Rank a handle's top performers by engagement (songbird.signal.top_performers).
CREATE INDEX IF NOT EXISTS ix_signal_profile_engagement
    ON harvested_signals (profile_key, ((COALESCE(likes, 0) + COALESCE(comments, 0))) DESC);

CREATE INDEX IF NOT EXISTS ix_harvested_signals_account ON harvested_signals (account_id);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_signal_account') THEN
        ALTER TABLE harvested_signals
            ADD CONSTRAINT fk_signal_account FOREIGN KEY (account_id) REFERENCES accounts(id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_signal_run') THEN
        ALTER TABLE harvested_signals
            ADD CONSTRAINT fk_signal_run FOREIGN KEY (run_id) REFERENCES runs(id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_signal_account') THEN
        ALTER TABLE harvested_signals
            ADD CONSTRAINT chk_signal_account CHECK (account_id IS NOT NULL);
    END IF;
END $$;

INSERT INTO schema_migrations (version) VALUES ('003_harvested_signals')
ON CONFLICT (version) DO NOTHING;
INSERT INTO schema_migrations (version) VALUES ('005_link_existing')
ON CONFLICT (version) DO NOTHING;
INSERT INTO schema_migrations (version) VALUES ('006_enforce_account_link')
ON CONFLICT (version) DO NOTHING;

-- Fuzzy handle matching (own/competitor lookups may not match casing exactly).
CREATE INDEX IF NOT EXISTS ix_signal_profile_trgm
    ON harvested_signals USING gin (profile_key gin_trgm_ops);