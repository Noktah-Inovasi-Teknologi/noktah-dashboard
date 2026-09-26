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
    -- The CHECK is added by explicit named ALTER below rather than inline, and
    -- carries 'extraction' from the start. Migration 009 widens this constraint
    -- and renames it from the auto-generated `runs_kind_check` to `chk_runs_kind`;
    -- an inline CHECK here would auto-name differently and fail schema parity.
    kind               TEXT NOT NULL,
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

-- Mirrors migration 009's widened constraint, name for name. See the comment on
-- the `kind` column above.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_runs_kind') THEN
        ALTER TABLE runs
            ADD CONSTRAINT chk_runs_kind
            CHECK (kind IN ('collection', 'generation', 'extraction'));
    END IF;
END $$;

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
    run_id             UUID NULL,
    -- Signal Field Coverage (migration 007). Declared LAST because 007 adds it
    -- with ALTER TABLE ADD COLUMN, which appends — ordinal_position must match
    -- between this path and the migration chain or schema parity fails.
    -- Nullable by design: emptiness is resolved by field_availability /
    -- capture_outcomes below, never by a sentinel. 0 shares is a real value.
    shares             BIGINT NULL
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

-- ===========================================================================
-- Signal Field Coverage (feature 005) — mirrors migration 007.
-- Constraints are added by explicit named ALTER, exactly as 007 does, so both
-- build paths produce byte-identical pg_constraint entries (schema.md).
-- ===========================================================================

-- "Can this ever be known?" — reference data, mirrored from
-- config/field_availability.yaml by flows/field_availability_sync.py.
-- Deliberately NOT foreign-keyed to any observation: a determination must be
-- updatable without touching a stored observation (FR-007).
CREATE TABLE IF NOT EXISTS field_availability (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    platform           TEXT NOT NULL,
    content_type       TEXT NOT NULL,
    field_name         TEXT NOT NULL,
    status             TEXT NOT NULL,
    reason             TEXT NOT NULL,
    evidence           TEXT NULL,
    determined_on      DATE NOT NULL,
    source_version     TEXT NOT NULL,
    synced_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_field_availability_platform') THEN
        ALTER TABLE field_availability
            ADD CONSTRAINT chk_field_availability_platform
            CHECK (platform IN ('instagram', 'tiktok'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_field_availability_status') THEN
        ALTER TABLE field_availability
            ADD CONSTRAINT chk_field_availability_status
            CHECK (status IN (
                'available',
                'unavailable_platform_limit',
                'not_collected_by_decision',
                'inconclusive',
                'undetermined'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_field_availability_reason_nonempty') THEN
        ALTER TABLE field_availability
            ADD CONSTRAINT chk_field_availability_reason_nonempty
            CHECK (btrim(reason) <> '');
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS uq_field_availability_triple
    ON field_availability (platform, content_type, field_name);

-- "Was it known this time?" — append-only, one row per supplementary capture
-- attempt. Keyed by (platform, content_id) rather than harvested_signals.id
-- because a capture can fail BEFORE a signal row exists, and those are exactly
-- the failures that must not be silently dropped.
CREATE TABLE IF NOT EXISTS capture_outcomes (
    id                 BIGSERIAL PRIMARY KEY,
    platform           TEXT NOT NULL,
    content_id         TEXT NOT NULL,
    capture_kind       TEXT NOT NULL,
    outcome            TEXT NOT NULL,
    reason             TEXT NULL,
    account_id         UUID NULL,
    run_id             UUID NULL,
    observed_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_capture_outcomes_platform') THEN
        ALTER TABLE capture_outcomes
            ADD CONSTRAINT chk_capture_outcomes_platform
            CHECK (platform IN ('instagram', 'tiktok'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_capture_outcomes_kind') THEN
        ALTER TABLE capture_outcomes
            ADD CONSTRAINT chk_capture_outcomes_kind
            -- 'metric_refresh' added by migration 008. Value ORDER must match
            -- 008's re-added constraint exactly: Postgres stores the IN list as
            -- an ordered ARRAY, so pg_get_constraintdef() differs between the
            -- two paths if the order differs, and schema parity fails.
            CHECK (capture_kind IN (
                'instagram_clip_stats',
                'instagram_feed_stats',
                'instagram_profile_info',
                'tiktok_stats',
                'metric_refresh'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_capture_outcomes_outcome') THEN
        ALTER TABLE capture_outcomes
            ADD CONSTRAINT chk_capture_outcomes_outcome
            CHECK (outcome IN ('success', 'no_match', 'failed', 'not_attempted'));
    END IF;
    -- `reason IS NOT NULL` is load-bearing: without it, outcome='failed' with a
    -- NULL reason evaluates to `false OR NULL` = NULL, and a CHECK passes on
    -- NULL — letting through exactly the case being forbidden.
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_capture_outcomes_reason') THEN
        ALTER TABLE capture_outcomes
            ADD CONSTRAINT chk_capture_outcomes_reason
            CHECK (
                outcome <> 'failed'
                OR (reason IS NOT NULL
                    AND reason IN ('not_found', 'private', 'deleted', 'blocked',
                                   'parse_failure', 'timeout', 'unexpected_structure'))
            );
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_capture_outcomes_account') THEN
        ALTER TABLE capture_outcomes
            ADD CONSTRAINT fk_capture_outcomes_account FOREIGN KEY (account_id) REFERENCES accounts(id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_capture_outcomes_run') THEN
        ALTER TABLE capture_outcomes
            ADD CONSTRAINT fk_capture_outcomes_run FOREIGN KEY (run_id) REFERENCES runs(id);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS ix_capture_outcomes_item
    ON capture_outcomes (platform, content_id, observed_at DESC);
CREATE INDEX IF NOT EXISTS ix_capture_outcomes_run
    ON capture_outcomes (run_id);
CREATE INDEX IF NOT EXISTS ix_capture_outcomes_account
    ON capture_outcomes (account_id, capture_kind, observed_at DESC);

INSERT INTO schema_migrations (version) VALUES ('007_signal_field_coverage')
ON CONFLICT (version) DO NOTHING;

-- ============================================================================
-- Longitudinal Metric Capture (feature 006) -- mirrors migration
-- config/postgres/migrations/008_observation_history.sql
--
-- Read that file for the rationale; this is the greenfield copy and must stay
-- structurally identical to it. Constraints are declared as named ALTERs, not
-- inline REFERENCES, so pg_constraint entries match the migrated path exactly
-- (see .claude/rules/backend/schema.md § Mirroring into init.sql).
-- ============================================================================

CREATE TABLE IF NOT EXISTS metric_observations (
    id                 BIGSERIAL PRIMARY KEY,
    platform           TEXT NOT NULL,
    content_id         TEXT NOT NULL,
    observed_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    provenance         TEXT NOT NULL DEFAULT 'captured',
    views              BIGINT NULL,
    likes              BIGINT NULL,
    comments           BIGINT NULL,
    shares             BIGINT NULL,
    account_id         UUID NULL,
    run_id             UUID NULL
);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_metric_observations_platform') THEN
        ALTER TABLE metric_observations
            ADD CONSTRAINT chk_metric_observations_platform
            CHECK (platform IN ('instagram', 'tiktok'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_metric_observations_provenance') THEN
        ALTER TABLE metric_observations
            ADD CONSTRAINT chk_metric_observations_provenance
            CHECK (provenance IN ('captured', 'legacy'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_metric_observations_has_a_value') THEN
        ALTER TABLE metric_observations
            ADD CONSTRAINT chk_metric_observations_has_a_value
            CHECK (views IS NOT NULL OR likes IS NOT NULL
                   OR comments IS NOT NULL OR shares IS NOT NULL);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_metric_observations_account') THEN
        ALTER TABLE metric_observations
            ADD CONSTRAINT fk_metric_observations_account FOREIGN KEY (account_id) REFERENCES accounts(id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_metric_observations_run') THEN
        ALTER TABLE metric_observations
            ADD CONSTRAINT fk_metric_observations_run FOREIGN KEY (run_id) REFERENCES runs(id);
    END IF;
END $$;

-- Serves every read of this table; see the note in migration 008 for why there
-- is no second index on the same three columns.
CREATE UNIQUE INDEX IF NOT EXISTS uq_metric_observation_capture
    ON metric_observations (platform, content_id, observed_at);

CREATE INDEX IF NOT EXISTS ix_metric_observations_run
    ON metric_observations (run_id);
CREATE INDEX IF NOT EXISTS ix_metric_observations_account
    ON metric_observations (account_id, observed_at DESC);

CREATE TABLE IF NOT EXISTS velocity_intervals (
    id                    BIGSERIAL PRIMARY KEY,
    platform              TEXT NOT NULL,
    content_id            TEXT NOT NULL,
    from_observation_id   BIGINT NOT NULL,
    to_observation_id     BIGINT NOT NULL,
    elapsed_seconds       BIGINT NOT NULL,
    delta_views           BIGINT NULL,
    delta_likes           BIGINT NULL,
    delta_comments        BIGINT NULL,
    delta_shares          BIGINT NULL,
    rate_views_per_day    DOUBLE PRECISION NULL,
    rate_likes_per_day    DOUBLE PRECISION NULL,
    rate_comments_per_day DOUBLE PRECISION NULL,
    rate_shares_per_day   DOUBLE PRECISION NULL,
    rate_withheld_reason  TEXT NULL,
    is_plateau            BOOLEAN NOT NULL DEFAULT false,
    is_acceleration       BOOLEAN NOT NULL DEFAULT false,
    plateau_interval_id   BIGINT NULL,
    derived_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    config_version        TEXT NOT NULL DEFAULT 'v1'
);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_velocity_intervals_platform') THEN
        ALTER TABLE velocity_intervals
            ADD CONSTRAINT chk_velocity_intervals_platform
            CHECK (platform IN ('instagram', 'tiktok'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_velocity_elapsed_positive') THEN
        ALTER TABLE velocity_intervals
            ADD CONSTRAINT chk_velocity_elapsed_positive
            CHECK (elapsed_seconds > 0);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_velocity_rate_withheld') THEN
        ALTER TABLE velocity_intervals
            ADD CONSTRAINT chk_velocity_rate_withheld
            CHECK (rate_withheld_reason IS NULL
                   OR rate_withheld_reason IN ('interval_too_short'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_velocity_acceleration_has_plateau') THEN
        ALTER TABLE velocity_intervals
            ADD CONSTRAINT chk_velocity_acceleration_has_plateau
            CHECK (is_acceleration = false OR plateau_interval_id IS NOT NULL);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_velocity_from_observation') THEN
        ALTER TABLE velocity_intervals
            ADD CONSTRAINT fk_velocity_from_observation
            FOREIGN KEY (from_observation_id) REFERENCES metric_observations(id) ON DELETE CASCADE;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_velocity_to_observation') THEN
        ALTER TABLE velocity_intervals
            ADD CONSTRAINT fk_velocity_to_observation
            FOREIGN KEY (to_observation_id) REFERENCES metric_observations(id) ON DELETE CASCADE;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_velocity_plateau_interval') THEN
        ALTER TABLE velocity_intervals
            ADD CONSTRAINT fk_velocity_plateau_interval
            FOREIGN KEY (plateau_interval_id) REFERENCES velocity_intervals(id) ON DELETE SET NULL;
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS uq_velocity_interval_pair
    ON velocity_intervals (from_observation_id, to_observation_id);

CREATE INDEX IF NOT EXISTS ix_velocity_intervals_item
    ON velocity_intervals (platform, content_id);
CREATE INDEX IF NOT EXISTS ix_velocity_intervals_acceleration
    ON velocity_intervals (is_acceleration) WHERE is_acceleration;

CREATE OR REPLACE VIEW velocity_status AS
WITH items AS (
    SELECT platform, content_id FROM harvested_signals
    UNION
    SELECT platform, content_id FROM metric_observations
), obs AS (
    SELECT platform, content_id,
           count(*)                                        AS observation_count,
           count(*) FILTER (WHERE provenance = 'captured') AS captured_count
    FROM metric_observations
    GROUP BY platform, content_id
), iv AS (
    SELECT platform, content_id,
           count(*)                                             AS interval_count,
           count(*) FILTER (WHERE rate_withheld_reason IS NULL) AS rated_count
    FROM velocity_intervals
    GROUP BY platform, content_id
), last_outcome AS (
    SELECT DISTINCT ON (platform, content_id) platform, content_id, outcome, reason
    FROM capture_outcomes
    WHERE capture_kind = 'metric_refresh'
    ORDER BY platform, content_id, observed_at DESC
)
SELECT i.platform,
       i.content_id,
       COALESCE(o.observation_count, 0) AS observation_count,
       COALESCE(iv.rated_count, 0) > 0  AS has_velocity,
       CASE
           WHEN COALESCE(iv.rated_count, 0) > 0        THEN 'has_velocity'
           WHEN COALESCE(o.observation_count, 0) = 0   THEN 'never_observed'
           WHEN o.observation_count = 1
                AND o.captured_count = 0               THEN 'legacy_only'
           WHEN o.observation_count = 1                THEN 'observed_once'
           WHEN iv.interval_count IS NULL              THEN 'not_yet_derived'
           WHEN iv.rated_count = 0                     THEN 'all_intervals_too_short'
           WHEN lo.reason IN ('aged_out_of_listing',
                              'absent_within_reach',
                              'deleted')               THEN 'no_longer_observable'
           ELSE 'UNRESOLVED'
       END AS reason
FROM items i
LEFT JOIN obs o ON o.platform = i.platform AND o.content_id = i.content_id
LEFT JOIN iv ON iv.platform = i.platform AND iv.content_id = i.content_id
LEFT JOIN last_outcome lo ON lo.platform = i.platform AND lo.content_id = i.content_id;

INSERT INTO schema_migrations (version) VALUES ('008_observation_history')
ON CONFLICT (version) DO NOTHING;

-- ===========================================================================
-- 009_structured_extraction (feature 007-structured-extraction)
-- ===========================================================================
-- Mirrors config/postgres/migrations/009_structured_extraction.sql in the same
-- order, with constraint names matching EXACTLY. The runs.kind widening that
-- migration performs is mirrored at the runs table above (it already carries
-- 'extraction'), not here.
--
-- Verify with: script/verify_schema_parity.sh

CREATE TABLE IF NOT EXISTS extraction_vocabulary_terms (
    version            TEXT NOT NULL,
    dimension          TEXT NOT NULL,
    term               TEXT NOT NULL,
    is_residual        BOOLEAN NOT NULL DEFAULT false,
    ordinal            SMALLINT NULL,
    description        TEXT NOT NULL,
    frozen_at          TIMESTAMPTZ NULL,
    synced_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT pk_vocabulary_term PRIMARY KEY (version, dimension, term)
);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_vocabulary_description') THEN
        ALTER TABLE extraction_vocabulary_terms
            ADD CONSTRAINT chk_vocabulary_description CHECK (btrim(description) <> '');
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS uq_vocabulary_residual
    ON extraction_vocabulary_terms (version, dimension) WHERE is_residual;

CREATE TABLE IF NOT EXISTS content_extractions (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    platform           TEXT NOT NULL,
    content_id         TEXT NOT NULL,
    account_id         UUID NULL,
    run_id             UUID NULL,
    purpose            TEXT NOT NULL DEFAULT 'production',
    content_type       TEXT NOT NULL,
    media_path         TEXT NOT NULL,
    subtitle           TEXT NULL,
    subtitle_absence   TEXT NULL,
    summary            TEXT NOT NULL,
    model_requested    TEXT NOT NULL,
    model_served       TEXT NOT NULL,
    provider           TEXT NULL,
    prompt_version     TEXT NOT NULL,
    schema_version     TEXT NOT NULL,
    vocabulary_version TEXT NOT NULL,
    content_hash       TEXT NOT NULL,
    prompt_tokens      INTEGER NULL,
    completion_tokens  INTEGER NULL,
    cost_usd           NUMERIC(12,6) NULL,
    attempts           SMALLINT NOT NULL DEFAULT 1,
    extracted_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_extraction_platform') THEN
        ALTER TABLE content_extractions
            ADD CONSTRAINT chk_extraction_platform CHECK (platform IN ('instagram', 'tiktok'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_extraction_purpose') THEN
        ALTER TABLE content_extractions
            ADD CONSTRAINT chk_extraction_purpose CHECK (purpose IN ('production', 'calibration'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_extraction_media_path') THEN
        ALTER TABLE content_extractions
            ADD CONSTRAINT chk_extraction_media_path CHECK (media_path IN ('video', 'image'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_extraction_subtitle_absence') THEN
        ALTER TABLE content_extractions
            ADD CONSTRAINT chk_extraction_subtitle_absence
            CHECK (subtitle IS NOT NULL OR subtitle_absence IS NOT NULL);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_extraction_absence_vocab') THEN
        ALTER TABLE content_extractions
            ADD CONSTRAINT chk_extraction_absence_vocab
            CHECK (subtitle_absence IS NULL
                   OR subtitle_absence IN ('not_applicable_no_audio', 'attempted_none_found'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_extraction_summary_nonempty') THEN
        ALTER TABLE content_extractions
            ADD CONSTRAINT chk_extraction_summary_nonempty CHECK (btrim(summary) <> '');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_extraction_attempts') THEN
        ALTER TABLE content_extractions
            ADD CONSTRAINT chk_extraction_attempts CHECK (attempts BETWEEN 1 AND 2);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_extraction_account') THEN
        ALTER TABLE content_extractions
            ADD CONSTRAINT fk_extraction_account FOREIGN KEY (account_id) REFERENCES accounts(id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_extraction_run') THEN
        ALTER TABLE content_extractions
            ADD CONSTRAINT fk_extraction_run FOREIGN KEY (run_id) REFERENCES runs(id);
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS uq_extraction_version
    ON content_extractions (platform, content_id, purpose, model_requested,
                            prompt_version, schema_version, vocabulary_version);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_extraction_id_vocab') THEN
        ALTER TABLE content_extractions
            ADD CONSTRAINT uq_extraction_id_vocab UNIQUE (id, vocabulary_version);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS ix_extractions_item
    ON content_extractions (platform, content_id);
CREATE INDEX IF NOT EXISTS ix_extractions_run
    ON content_extractions (run_id);
CREATE INDEX IF NOT EXISTS ix_extractions_cost_month
    ON content_extractions (extracted_at) WHERE cost_usd IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_extractions_hash
    ON content_extractions (content_hash);

CREATE TABLE IF NOT EXISTS extraction_beats (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    extraction_id      UUID NOT NULL,
    vocabulary_version TEXT NOT NULL,
    dimension          TEXT NOT NULL DEFAULT 'beat_function',
    position           SMALLINT NOT NULL,
    function           TEXT NOT NULL,
    description        TEXT NOT NULL
);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_beat_position') THEN
        ALTER TABLE extraction_beats
            ADD CONSTRAINT chk_beat_position CHECK (position >= 1);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_beat_description') THEN
        ALTER TABLE extraction_beats
            ADD CONSTRAINT chk_beat_description CHECK (btrim(description) <> '');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_beat_dimension') THEN
        ALTER TABLE extraction_beats
            ADD CONSTRAINT chk_beat_dimension CHECK (dimension = 'beat_function');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_beat_function') THEN
        ALTER TABLE extraction_beats
            ADD CONSTRAINT fk_beat_function
            FOREIGN KEY (vocabulary_version, dimension, function)
            REFERENCES extraction_vocabulary_terms (version, dimension, term);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_beat_extraction_vocab') THEN
        ALTER TABLE extraction_beats
            ADD CONSTRAINT fk_beat_extraction_vocab
            FOREIGN KEY (extraction_id, vocabulary_version)
            REFERENCES content_extractions (id, vocabulary_version) ON DELETE CASCADE;
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS uq_beat_position
    ON extraction_beats (extraction_id, position);
CREATE INDEX IF NOT EXISTS ix_beats_function
    ON extraction_beats (function);

CREATE TABLE IF NOT EXISTS extraction_attributes (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    extraction_id      UUID NOT NULL,
    dimension          TEXT NOT NULL,
    value              TEXT NOT NULL,
    confidence         TEXT NOT NULL,
    vocabulary_version TEXT NOT NULL
);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_attribute_confidence') THEN
        ALTER TABLE extraction_attributes
            ADD CONSTRAINT chk_attribute_confidence CHECK (confidence IN ('high', 'medium', 'low'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_attribute_extraction') THEN
        ALTER TABLE extraction_attributes
            ADD CONSTRAINT fk_attribute_extraction
            FOREIGN KEY (extraction_id) REFERENCES content_extractions (id) ON DELETE CASCADE;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_attribute_term') THEN
        ALTER TABLE extraction_attributes
            ADD CONSTRAINT fk_attribute_term
            FOREIGN KEY (vocabulary_version, dimension, value)
            REFERENCES extraction_vocabulary_terms (version, dimension, term);
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS uq_attribute_dimension
    ON extraction_attributes (extraction_id, dimension);

CREATE TABLE IF NOT EXISTS extraction_quarantine (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    platform           TEXT NOT NULL,
    content_id         TEXT NOT NULL,
    run_id             UUID NULL,
    purpose            TEXT NOT NULL DEFAULT 'production',
    failure_kind       TEXT NOT NULL,
    raw_output         TEXT NULL,
    validation_errors  JSONB NOT NULL DEFAULT '[]'::jsonb,
    model_requested    TEXT NOT NULL,
    model_served       TEXT NULL,
    prompt_version     TEXT NOT NULL,
    schema_version     TEXT NOT NULL,
    vocabulary_version TEXT NOT NULL,
    prompt_tokens      INTEGER NULL,
    completion_tokens  INTEGER NULL,
    cost_usd           NUMERIC(12,6) NULL,
    quarantined_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_quarantine_platform') THEN
        ALTER TABLE extraction_quarantine
            ADD CONSTRAINT chk_quarantine_platform CHECK (platform IN ('instagram', 'tiktok'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_quarantine_purpose') THEN
        ALTER TABLE extraction_quarantine
            ADD CONSTRAINT chk_quarantine_purpose CHECK (purpose IN ('production', 'calibration'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_quarantine_failure_kind') THEN
        ALTER TABLE extraction_quarantine
            ADD CONSTRAINT chk_quarantine_failure_kind
            CHECK (failure_kind IN (
                'schema_invalid',
                'truncated',
                'empty_content',
                'out_of_vocabulary',
                'provider_error',
                'rate_limited',
                'media_unavailable',
                'unsupported_media',
                'spend_ceiling_reached'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_quarantine_run') THEN
        ALTER TABLE extraction_quarantine
            ADD CONSTRAINT fk_quarantine_run FOREIGN KEY (run_id) REFERENCES runs(id);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS ix_quarantine_item
    ON extraction_quarantine (platform, content_id);
CREATE INDEX IF NOT EXISTS ix_quarantine_counts
    ON extraction_quarantine (failure_kind, model_requested, prompt_version, schema_version);
CREATE INDEX IF NOT EXISTS ix_quarantine_run
    ON extraction_quarantine (run_id);
CREATE INDEX IF NOT EXISTS ix_quarantine_cost_month
    ON extraction_quarantine (quarantined_at) WHERE cost_usd IS NOT NULL;

CREATE TABLE IF NOT EXISTS calibration_sample_members (
    sample_key         TEXT NOT NULL,
    platform           TEXT NOT NULL,
    content_id         TEXT NOT NULL,
    media_class        TEXT NOT NULL,
    added_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT pk_calibration_member PRIMARY KEY (sample_key, platform, content_id)
);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_calibration_platform') THEN
        ALTER TABLE calibration_sample_members
            ADD CONSTRAINT chk_calibration_platform CHECK (platform IN ('instagram', 'tiktok'));
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS extraction_batch_runs (
    run_id                 UUID PRIMARY KEY,
    batch_kind             TEXT NOT NULL,
    mode                   TEXT NOT NULL,
    scope_description      TEXT NOT NULL,
    items_in_scope         INTEGER NOT NULL DEFAULT 0,
    projection_basis       TEXT NOT NULL DEFAULT 'uncalibrated',
    projection_usd         NUMERIC(12,6) NULL,
    projection_sample_size INTEGER NULL,
    ceiling_usd            NUMERIC(12,6) NULL,
    month_to_date_usd      NUMERIC(12,6) NULL,
    confirmed              BOOLEAN NOT NULL DEFAULT false,
    actual_usd             NUMERIC(12,6) NULL,
    items_extracted        INTEGER NOT NULL DEFAULT 0,
    items_cached           INTEGER NOT NULL DEFAULT 0,
    items_quarantined      INTEGER NOT NULL DEFAULT 0,
    items_skipped          INTEGER NOT NULL DEFAULT 0,
    items_unclassified     INTEGER NOT NULL DEFAULT 0
);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_batch_kind') THEN
        ALTER TABLE extraction_batch_runs
            ADD CONSTRAINT chk_batch_kind CHECK (batch_kind IN ('backfill', 'calibration'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_batch_mode') THEN
        ALTER TABLE extraction_batch_runs
            ADD CONSTRAINT chk_batch_mode CHECK (mode IN ('dry_run', 'pilot', 'real'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_batch_projection') THEN
        ALTER TABLE extraction_batch_runs
            ADD CONSTRAINT chk_batch_projection
            CHECK (projection_basis <> 'measured'
                   OR (projection_usd IS NOT NULL AND projection_sample_size IS NOT NULL));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_batch_projection_basis') THEN
        ALTER TABLE extraction_batch_runs
            ADD CONSTRAINT chk_batch_projection_basis
            CHECK (projection_basis IN ('measured', 'uncalibrated'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_batch_run') THEN
        ALTER TABLE extraction_batch_runs
            ADD CONSTRAINT fk_batch_run FOREIGN KEY (run_id) REFERENCES runs(id) ON DELETE CASCADE;
    END IF;
END $$;

CREATE OR REPLACE VIEW extraction_status AS
WITH items AS (
    SELECT platform, content_id FROM harvested_signals
    UNION
    SELECT platform, content_id FROM content_extractions
), ex AS (
    SELECT platform, content_id,
           count(*)                                          AS extraction_count,
           count(*) FILTER (WHERE purpose = 'production')     AS production_count,
           count(*) FILTER (WHERE purpose = 'calibration')    AS calibration_count,
           max(schema_version) FILTER (WHERE purpose = 'production')     AS max_schema_version,
           max(vocabulary_version) FILTER (WHERE purpose = 'production') AS max_vocab_version
    FROM content_extractions
    GROUP BY platform, content_id
), q AS (
    SELECT DISTINCT ON (platform, content_id) platform, content_id, failure_kind, quarantined_at
    FROM extraction_quarantine
    ORDER BY platform, content_id, quarantined_at DESC
), cur AS (
    SELECT max(version) AS version FROM extraction_vocabulary_terms
)
SELECT i.platform,
       i.content_id,
       COALESCE(ex.production_count, 0) AS production_extractions,
       CASE
           WHEN COALESCE(ex.production_count, 0) > 0
                AND ex.max_vocab_version >= (SELECT version FROM cur) THEN 'extracted'
           WHEN COALESCE(ex.extraction_count, 0) = 0
                AND q.failure_kind IS NULL                            THEN 'never_attempted'
           WHEN q.failure_kind = 'media_unavailable'
                AND COALESCE(ex.production_count, 0) = 0              THEN 'media_unavailable'
           WHEN q.failure_kind = 'spend_ceiling_reached'
                AND COALESCE(ex.production_count, 0) = 0              THEN 'ceiling_deferred'
           WHEN COALESCE(ex.production_count, 0) = 0
                AND COALESCE(ex.calibration_count, 0) > 0             THEN 'calibration_only'
           WHEN COALESCE(ex.production_count, 0) = 0
                AND q.failure_kind IS NOT NULL                        THEN 'quarantined'
           WHEN COALESCE(ex.production_count, 0) > 0                  THEN 'superseded_version_only'
           ELSE 'UNRESOLVED'
       END AS reason
FROM items i
LEFT JOIN ex ON ex.platform = i.platform AND ex.content_id = i.content_id
LEFT JOIN q ON q.platform = i.platform AND q.content_id = i.content_id;

INSERT INTO schema_migrations (version) VALUES ('009_structured_extraction')
ON CONFLICT (version) DO NOTHING;

-- ════════════════════════════════════════════════════════════════════════
-- 010_hub_registry_card (feature 008-hub-registry-client-card): mirrored verbatim
-- from config/postgres/migrations/010_hub_registry_card.sql. Keep in sync;
-- script/verify_schema_parity.sh proves it.
-- ════════════════════════════════════════════════════════════════════════

-- ── Noktah Brands ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS noktah_brands (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    brand_key            TEXT NOT NULL UNIQUE,
    display_name         TEXT NOT NULL,
    slack_managerial_env TEXT
);

-- ── People ──────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS people (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    display_name    TEXT NOT NULL,
    jira_account_id TEXT,
    slack_user_id   TEXT,
    status          TEXT NOT NULL DEFAULT 'active',
    left_at         TIMESTAMPTZ,
    version         INTEGER NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT chk_people_status CHECK (status IN ('active', 'left')),
    CONSTRAINT chk_people_display_name_nonempty CHECK (btrim(display_name) <> '')
);

CREATE TABLE IF NOT EXISTS person_emails (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    person_id  UUID NOT NULL REFERENCES people(id),
    email      TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_person_emails_email UNIQUE (email),
    CONSTRAINT chk_person_emails_lower CHECK (email = lower(btrim(email)) AND email <> '')
);
CREATE INDEX IF NOT EXISTS idx_person_emails_person ON person_emails (person_id);

CREATE TABLE IF NOT EXISTS person_roles (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    person_id       UUID NOT NULL REFERENCES people(id),
    role            TEXT NOT NULL,
    noktah_brand_id UUID REFERENCES noktah_brands(id),
    valid_from      TIMESTAMPTZ NOT NULL DEFAULT now(),
    valid_to        TIMESTAMPTZ,
    granted_by      UUID REFERENCES people(id),
    CONSTRAINT chk_person_roles_role CHECK (role IN (
        'owner', 'brand_manager', 'project_manager', 'account_executive', 'sales_marketing',
        'content_planner', 'field_associate', 'content_editor', 'qc')),
    -- Owner is company-wide; every other role belongs to exactly one Noktah Brand.
    CONSTRAINT chk_person_roles_brand_scope CHECK ((role = 'owner') = (noktah_brand_id IS NULL)),
    CONSTRAINT chk_person_roles_period CHECK (valid_to IS NULL OR valid_to >= valid_from)
);
CREATE INDEX IF NOT EXISTS idx_person_roles_person ON person_roles (person_id) WHERE valid_to IS NULL;
-- One active Brand Manager per Noktah Brand.
CREATE UNIQUE INDEX IF NOT EXISTS uq_person_roles_one_brand_manager
    ON person_roles (noktah_brand_id) WHERE role = 'brand_manager' AND valid_to IS NULL;
-- The same role can't be held twice at once by one Person in one Noktah Brand.
CREATE UNIQUE INDEX IF NOT EXISTS uq_person_roles_active
    ON person_roles (person_id, role, COALESCE(noktah_brand_id, '00000000-0000-0000-0000-000000000000'::uuid))
    WHERE valid_to IS NULL;

-- ── Clients: additive columns ───────────────────────────────────────────────
ALTER TABLE clients ADD COLUMN IF NOT EXISTS noktah_brand_id UUID;
ALTER TABLE clients ADD COLUMN IF NOT EXISTS status TEXT;
ALTER TABLE clients ADD COLUMN IF NOT EXISTS is_internal BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE clients ADD COLUMN IF NOT EXISTS quota_post INTEGER;
ALTER TABLE clients ADD COLUMN IF NOT EXISTS quota_story INTEGER;
ALTER TABLE clients ADD COLUMN IF NOT EXISTS quota_short_video INTEGER;
ALTER TABLE clients ADD COLUMN IF NOT EXISTS drive_folder_id TEXT;
ALTER TABLE clients ADD COLUMN IF NOT EXISTS content_plan_folder_id TEXT;
ALTER TABLE clients ADD COLUMN IF NOT EXISTS jira_component_id TEXT;
ALTER TABLE clients ADD COLUMN IF NOT EXISTS sheet_row_name TEXT;
ALTER TABLE clients ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 0;
ALTER TABLE clients ADD COLUMN IF NOT EXISTS card_version INTEGER NOT NULL DEFAULT 0;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_clients_noktah_brand') THEN
        ALTER TABLE clients ADD CONSTRAINT fk_clients_noktah_brand
            FOREIGN KEY (noktah_brand_id) REFERENCES noktah_brands(id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_clients_status') THEN
        ALTER TABLE clients ADD CONSTRAINT chk_clients_status
            CHECK (status IS NULL OR status IN ('pending', 'active', 'inactive'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_clients_quotas_nonnegative') THEN
        ALTER TABLE clients ADD CONSTRAINT chk_clients_quotas_nonnegative
            CHECK (COALESCE(quota_post, 0) >= 0 AND COALESCE(quota_story, 0) >= 0
                   AND COALESCE(quota_short_video, 0) >= 0);
    END IF;
END $$;

-- ── Team ────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS client_team_assignments (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id  UUID NOT NULL REFERENCES clients(id),
    team_role  TEXT NOT NULL,
    person_id  UUID NOT NULL REFERENCES people(id),
    valid_from TIMESTAMPTZ NOT NULL DEFAULT now(),
    valid_to   TIMESTAMPTZ,
    set_by     UUID REFERENCES people(id),
    CONSTRAINT chk_team_role CHECK (team_role IN (
        'account_executive', 'content_planner', 'field_associate', 'content_editor', 'qc'))
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_team_one_active_per_role
    ON client_team_assignments (client_id, team_role) WHERE valid_to IS NULL;

-- ── Registry change log (append-only) ──────────────────────────────────────
CREATE TABLE IF NOT EXISTS registry_changes (
    id        BIGSERIAL PRIMARY KEY,
    entity    TEXT NOT NULL,
    entity_id UUID,
    client_id UUID REFERENCES clients(id),
    field     TEXT NOT NULL,
    old_value JSONB,
    new_value JSONB,
    person_id UUID REFERENCES people(id),
    at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT chk_registry_changes_entity CHECK (entity IN (
        'client', 'team', 'account_link', 'person', 'person_email', 'person_role'))
);
CREATE INDEX IF NOT EXISTS idx_registry_changes_client ON registry_changes (client_id, at DESC);

-- ── Card definition ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS card_definitions (
    version    TEXT PRIMARY KEY,
    body       JSONB NOT NULL,
    synced_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    frozen_at  TIMESTAMPTZ
);

-- ── Intake ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS intakes (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id      UUID REFERENCES clients(id),
    kind           TEXT NOT NULL,
    raw_text       TEXT,
    raw_blob       BYTEA,
    raw_mime       TEXT,
    raw_purged_at  TIMESTAMPTZ,
    source_ref     TEXT,
    content_hash   TEXT NOT NULL,
    submitted_by   UUID REFERENCES people(id),
    submitted_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    status         TEXT NOT NULL DEFAULT 'processing',
    failure_reason TEXT,
    model          TEXT,
    provider       TEXT,
    cost_usd       NUMERIC(12, 6),
    prompt_version TEXT,
    no_card_home   JSONB,
    CONSTRAINT chk_intakes_kind CHECK (kind IN ('text', 'image', 'gdoc', 'pdf_text', 'pdf_scanned', 'old_note')),
    CONSTRAINT chk_intakes_status CHECK (status IN (
        'processing', 'ready', 'nothing_found', 'failed', 'unmatched', 'discarded')),
    -- `IS NOT NULL` is load-bearing: a NULL reason would make the CHECK pass (schema.md).
    CONSTRAINT chk_intakes_failure_reason CHECK (status <> 'failed' OR (failure_reason IS NOT NULL AND failure_reason IN (
        'validation_failed', 'truncated', 'timeout', 'provider_error', 'cap_reached', 'unreadable'))),
    CONSTRAINT chk_intakes_client_required CHECK (client_id IS NOT NULL OR kind = 'old_note')
);
CREATE INDEX IF NOT EXISTS idx_intakes_client ON intakes (client_id, submitted_at DESC);
-- The content-hash cache (constitution XI): one non-failed Intake per content per Client.
CREATE UNIQUE INDEX IF NOT EXISTS uq_intakes_content_per_client
    ON intakes (client_id, content_hash) WHERE status <> 'failed' AND client_id IS NOT NULL;
-- Old notes are processed once each (idempotent by source record).
CREATE UNIQUE INDEX IF NOT EXISTS uq_intakes_old_note_source
    ON intakes (source_ref) WHERE kind = 'old_note';

CREATE TABLE IF NOT EXISTS intake_proposals (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    intake_id      UUID NOT NULL REFERENCES intakes(id),
    target         TEXT NOT NULL,
    field_key      TEXT,
    proposed_value JSONB NOT NULL,
    excerpt        TEXT NOT NULL,
    speaker        TEXT,
    spoke_at       DATE,
    valid_until    DATE,
    flags          TEXT[] NOT NULL DEFAULT '{}',
    outcome        TEXT NOT NULL DEFAULT 'pending',
    final_value    JSONB,
    ticks          JSONB NOT NULL DEFAULT '{}'::jsonb,
    decided_by     UUID REFERENCES people(id),
    decided_at     TIMESTAMPTZ,
    CONSTRAINT chk_intake_proposals_target CHECK (target IN ('profil', 'guideline', 'request')),
    CONSTRAINT chk_intake_proposals_field CHECK ((target = 'request') = (field_key IS NULL)),
    CONSTRAINT chk_intake_proposals_outcome CHECK (outcome IN (
        'pending', 'accepted', 'edited', 'rejected', 'tidak_masuk_kartu')),
    CONSTRAINT chk_intake_proposals_flags CHECK (flags <@ ARRAY[
        'unverified', 'from_image', 'not_pic', 'price_incomplete', 'other_client', 'contradicts', 'not_bahasa']::text[])
);
CREATE INDEX IF NOT EXISTS idx_intake_proposals_intake ON intake_proposals (intake_id);

-- ── Client Card values (append-only history) ───────────────────────────────
CREATE TABLE IF NOT EXISTS card_values (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id             UUID NOT NULL REFERENCES clients(id),
    part                  TEXT NOT NULL,
    field_key             TEXT NOT NULL,
    definition_version    TEXT NOT NULL REFERENCES card_definitions(version),
    value                 JSONB NOT NULL,
    valid_until           DATE,
    source                JSONB NOT NULL DEFAULT '{}'::jsonb,
    state                 TEXT NOT NULL,
    replaced_by           UUID REFERENCES card_values(id),
    set_by                UUID NOT NULL REFERENCES people(id),
    set_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    decided_by            UUID REFERENCES people(id),
    decided_at            TIMESTAMPTZ,
    reject_reason         TEXT,
    from_proposal_id      UUID REFERENCES intake_proposals(id),
    copied_from_client_id UUID REFERENCES clients(id),
    CONSTRAINT chk_card_values_part CHECK (part IN ('profil', 'guideline')),
    CONSTRAINT chk_card_values_state CHECK (state IN ('current', 'pending', 'superseded', 'corrected', 'rejected')),
    CONSTRAINT chk_card_values_reject_reason CHECK (state <> 'rejected' OR reject_reason IS NOT NULL)
);
-- At most ONE current value and ONE pending change per Client field (research R2).
CREATE UNIQUE INDEX IF NOT EXISTS uq_card_values_one_current
    ON card_values (client_id, part, field_key) WHERE state = 'current';
CREATE UNIQUE INDEX IF NOT EXISTS uq_card_values_one_pending
    ON card_values (client_id, part, field_key) WHERE state = 'pending';
CREATE INDEX IF NOT EXISTS idx_card_values_history ON card_values (client_id, part, field_key, set_at DESC);

-- ── Requests ───────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS client_requests (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id        UUID NOT NULL REFERENCES clients(id),
    requested_on     DATE NOT NULL,
    text             TEXT NOT NULL,
    requested_by     TEXT,
    is_pic           BOOLEAN NOT NULL DEFAULT false,
    channel          TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'baru',
    reject_reason    TEXT,
    link             TEXT,
    from_proposal_id UUID REFERENCES intake_proposals(id),
    created_by       UUID NOT NULL REFERENCES people(id),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    version          INTEGER NOT NULL DEFAULT 0,
    CONSTRAINT chk_client_requests_channel CHECK (channel IN ('whatsapp_group', 'meeting', 'email', 'lainnya')),
    CONSTRAINT chk_client_requests_status CHECK (status IN ('baru', 'diproses', 'selesai', 'ditolak')),
    CONSTRAINT chk_client_requests_reject_reason CHECK (status <> 'ditolak' OR reject_reason IS NOT NULL),
    CONSTRAINT chk_client_requests_text_nonempty CHECK (btrim(text) <> '')
);
CREATE INDEX IF NOT EXISTS idx_client_requests_client ON client_requests (client_id, requested_on DESC);

CREATE TABLE IF NOT EXISTS client_request_events (
    id          BIGSERIAL PRIMARY KEY,
    request_id  UUID NOT NULL REFERENCES client_requests(id),
    from_status TEXT,
    to_status   TEXT NOT NULL,
    reason      TEXT,
    person_id   UUID REFERENCES people(id),
    at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── Summaries ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS client_summaries (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id          UUID NOT NULL REFERENCES clients(id),
    body               JSONB NOT NULL,
    generated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    source_fingerprint TEXT NOT NULL,
    is_current         BOOLEAN NOT NULL DEFAULT true,
    model              TEXT,
    cost_usd           NUMERIC(12, 6)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_client_summaries_one_current
    ON client_summaries (client_id) WHERE is_current;

-- ── AI spend ───────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS ai_ledger (
    id                BIGSERIAL PRIMARY KEY,
    call_site         TEXT NOT NULL,
    client_id         UUID REFERENCES clients(id),
    intake_id         UUID REFERENCES intakes(id),
    model             TEXT,
    provider          TEXT,
    prompt_tokens     INTEGER,
    completion_tokens INTEGER,
    cost_usd          NUMERIC(12, 6) NOT NULL DEFAULT 0,
    at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT chk_ai_ledger_call_site CHECK (call_site IN ('hub.intake', 'hub.summary', 'hub.notes'))
);
CREATE INDEX IF NOT EXISTS idx_ai_ledger_at ON ai_ledger (at);

CREATE TABLE IF NOT EXISTS hub_alerts_sent (
    month TEXT NOT NULL,
    kind  TEXT NOT NULL,
    at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (month, kind)
);

-- ── Sheet copy progress (single row) ───────────────────────────────────────
CREATE TABLE IF NOT EXISTS hub_sync_state (
    id              INTEGER PRIMARY KEY DEFAULT 1,
    last_change_id  BIGINT NOT NULL DEFAULT 0,
    last_success_at TIMESTAMPTZ,
    last_check_at   TIMESTAMPTZ,
    last_error      TEXT,
    CONSTRAINT chk_hub_sync_state_single_row CHECK (id = 1)
);

-- ── Seeds ──────────────────────────────────────────────────────────────────
INSERT INTO noktah_brands (brand_key, display_name, slack_managerial_env) VALUES
    ('eskala', 'Eskala', 'SLACK_MANAGERIAL_ESKALA'),
    ('venyu',  'Venyu',  'SLACK_MANAGERIAL_NOKTAH')
ON CONFLICT (brand_key) DO NOTHING;

INSERT INTO hub_sync_state (id) VALUES (1) ON CONFLICT (id) DO NOTHING;

-- First roles (G-15). Each seed is created only if its email is not yet known.
DO $$
DECLARE
    seed RECORD;
    pid  UUID;
BEGIN
    FOR seed IN
        SELECT * FROM (VALUES
            ('core@noktah.co',   'Noktah (core)',          'owner',         NULL),
            ('bagas@noktah.co',  'Bagas',                  'brand_manager', 'venyu'),
            ('defila@noktah.co', 'Defila Priana Falarima', 'brand_manager', 'eskala')
        ) AS s(email, display_name, role, brand_key)
    LOOP
        SELECT person_id INTO pid FROM person_emails WHERE email = seed.email;
        IF pid IS NULL THEN
            INSERT INTO people (display_name) VALUES (seed.display_name) RETURNING id INTO pid;
            INSERT INTO person_emails (person_id, email) VALUES (pid, seed.email);
        END IF;
        IF NOT EXISTS (
            SELECT 1 FROM person_roles r
            WHERE r.person_id = pid AND r.role = seed.role AND r.valid_to IS NULL
        ) THEN
            INSERT INTO person_roles (person_id, role, noktah_brand_id)
            VALUES (pid, seed.role, (SELECT id FROM noktah_brands WHERE brand_key = seed.brand_key));
        END IF;
    END LOOP;
END $$;

INSERT INTO schema_migrations (version) VALUES ('010_hub_registry_card')
ON CONFLICT (version) DO NOTHING;


-- 011_client_brand_required (feature 008): mirrored from
-- config/postgres/migrations/011_client_brand_required.sql. The table starts
-- empty here, so NOT VALID → VALIDATE ends in the same validated constraint a
-- plain CHECK would (schema.md), and the parity check sees no difference.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_clients_noktah_brand') THEN
        ALTER TABLE clients
            ADD CONSTRAINT chk_clients_noktah_brand CHECK (noktah_brand_id IS NOT NULL) NOT VALID;
    END IF;
END $$;
ALTER TABLE clients VALIDATE CONSTRAINT chk_clients_noktah_brand;

INSERT INTO schema_migrations (version) VALUES ('011_client_brand_required')
ON CONFLICT (version) DO NOTHING;

-- 012_hub_units_roles_permissions: mirrored verbatim from
-- config/postgres/migrations/012_hub_units_roles_permissions.sql. Keep in sync;
-- script/verify_schema_parity.sh proves the two paths agree. On a fresh database
-- the data steps only move the seeded Owner into the Noktah Unit.
-- ── Units ───────────────────────────────────────────────────────────────────
ALTER TABLE noktah_brands ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'brand';
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_noktah_brands_kind') THEN
        ALTER TABLE noktah_brands ADD CONSTRAINT chk_noktah_brands_kind CHECK (kind IN ('group', 'brand'));
    END IF;
END $$;

INSERT INTO noktah_brands (brand_key, display_name, slack_managerial_env, kind)
VALUES ('noktah', 'Noktah', 'SLACK_MANAGERIAL_NOKTAH', 'group')
ON CONFLICT (brand_key) DO NOTHING;

-- ── Role catalog ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS unit_roles (
    noktah_brand_id     UUID NOT NULL REFERENCES noktah_brands(id),
    role                TEXT NOT NULL,
    display_name        TEXT NOT NULL,
    sort_order          INTEGER NOT NULL,
    team_slot           BOOLEAN NOT NULL DEFAULT false,
    default_permissions TEXT[] NOT NULL DEFAULT '{}',
    PRIMARY KEY (noktah_brand_id, role)
);

INSERT INTO unit_roles (noktah_brand_id, role, display_name, sort_order, team_slot, default_permissions)
SELECT b.id, s.role, s.display_name, s.sort_order, s.team_slot, s.default_permissions
FROM (VALUES
    ('noktah', 'owner',               'Owner',               1, false,
        '{hub_access,edit_clients,edit_profil,approve_guideline,edit_requests,run_intake,manage_people}'::text[]),
    ('noktah', 'sales_marketing',     'Sales & Marketing',   2, false, '{hub_access}'::text[]),
    ('eskala', 'brand_manager',       'Brand Manager',       1, false,
        '{hub_access,edit_clients,edit_profil,approve_guideline,edit_requests,run_intake,manage_people}'::text[]),
    ('eskala', 'production_manager',  'Production Manager',  2, false,
        '{hub_access,edit_clients,edit_profil,edit_requests,run_intake}'::text[]),
    ('eskala', 'account_executive',   'Account Executive',   3, true,
        '{hub_access,edit_clients,edit_profil,edit_requests,run_intake}'::text[]),
    ('eskala', 'content_planner',     'Content Planner',     4, true, '{}'::text[]),
    ('eskala', 'field_associate',     'Field Associate',     5, true, '{}'::text[]),
    ('eskala', 'content_editor',      'Content Editor',      6, true, '{}'::text[]),
    ('eskala', 'quality_assurance',   'Quality Assurance',   7, true, '{}'::text[]),
    ('venyu',  'brand_manager',       'Brand Manager',       1, false,
        '{hub_access,edit_clients,edit_profil,approve_guideline,edit_requests,run_intake,manage_people}'::text[]),
    ('venyu',  'production_manager',  'Production Manager',  2, false,
        '{hub_access,edit_clients,edit_profil,edit_requests,run_intake}'::text[]),
    ('venyu',  'quality_assurance',   'Quality Assurance',   3, false, '{}'::text[]),
    ('venyu',  'frontend_developer',  'Front-end Developer', 4, false, '{}'::text[]),
    ('venyu',  'backend_developer',   'Back-end Developer',  5, false, '{}'::text[]),
    ('venyu',  'devops',              'DevOps',              6, false, '{}'::text[]),
    ('venyu',  'mobile_developer',    'Mobile Developer',    7, false, '{}'::text[])
) AS s(brand_key, role, display_name, sort_order, team_slot, default_permissions)
JOIN noktah_brands b ON b.brand_key = s.brand_key
ON CONFLICT (noktah_brand_id, role) DO NOTHING;

-- ── Change log: two new entities ───────────────────────────────────────────
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_registry_changes_entity') THEN
        ALTER TABLE registry_changes DROP CONSTRAINT chk_registry_changes_entity;
    END IF;
    ALTER TABLE registry_changes ADD CONSTRAINT chk_registry_changes_entity CHECK (entity IN (
        'client', 'team', 'account_link', 'person', 'person_email', 'person_role',
        'person_unit', 'person_permission'));
END $$;

-- ── Roles: new keys, every role in a Unit ───────────────────────────────────
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_person_roles_brand_scope') THEN
        ALTER TABLE person_roles DROP CONSTRAINT chk_person_roles_brand_scope;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_person_roles_role') THEN
        ALTER TABLE person_roles DROP CONSTRAINT chk_person_roles_role;
    END IF;
    -- A superset of 010's list: the old keys stay valid for rows already ended.
    ALTER TABLE person_roles ADD CONSTRAINT chk_person_roles_role CHECK (role IN (
        'owner', 'brand_manager', 'project_manager', 'account_executive', 'sales_marketing',
        'content_planner', 'field_associate', 'content_editor', 'qc',
        'production_manager', 'quality_assurance', 'frontend_developer', 'backend_developer',
        'devops', 'mobile_developer'));

    IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_team_role') THEN
        ALTER TABLE client_team_assignments DROP CONSTRAINT chk_team_role;
    END IF;
    ALTER TABLE client_team_assignments ADD CONSTRAINT chk_team_role CHECK (team_role IN (
        'account_executive', 'content_planner', 'field_associate', 'content_editor', 'qc',
        'quality_assurance'));
END $$;

UPDATE person_roles SET role = 'production_manager' WHERE role = 'project_manager';
UPDATE person_roles SET role = 'quality_assurance' WHERE role = 'qc';
UPDATE client_team_assignments SET team_role = 'quality_assurance' WHERE team_role = 'qc';

-- Sales & Marketing held in both brands becomes one role in Noktah: keep the
-- oldest active one, end the rest, so the move can't collide on uq_person_roles_active.
UPDATE person_roles SET valid_to = now()
WHERE role = 'sales_marketing' AND valid_to IS NULL
  AND id NOT IN (SELECT DISTINCT ON (person_id) id FROM person_roles
                 WHERE role = 'sales_marketing' AND valid_to IS NULL ORDER BY person_id, valid_from);
UPDATE person_roles SET noktah_brand_id = (SELECT id FROM noktah_brands WHERE brand_key = 'noktah')
WHERE role IN ('owner', 'sales_marketing')
  AND noktah_brand_id IS DISTINCT FROM (SELECT id FROM noktah_brands WHERE brand_key = 'noktah');

-- An active role its Unit's catalog doesn't list (e.g. Account Executive in Venyu)
-- ends here, logged, rather than living on outside the catalog.
WITH ended AS (
    UPDATE person_roles r SET valid_to = now()
    WHERE r.valid_to IS NULL
      AND NOT EXISTS (SELECT 1 FROM unit_roles u WHERE u.noktah_brand_id = r.noktah_brand_id AND u.role = r.role)
    RETURNING r.person_id, r.role, r.noktah_brand_id
)
INSERT INTO registry_changes (entity, entity_id, field, old_value, new_value, person_id)
SELECT 'person_role', e.person_id, e.role, jsonb_build_object('role', e.role, 'noktah_brand', b.brand_key),
       NULL, NULL
FROM ended e LEFT JOIN noktah_brands b ON b.id = e.noktah_brand_id;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_person_roles_unit') THEN
        ALTER TABLE person_roles
            ADD CONSTRAINT chk_person_roles_unit CHECK (noktah_brand_id IS NOT NULL) NOT VALID;
    END IF;
END $$;
ALTER TABLE person_roles VALIDATE CONSTRAINT chk_person_roles_unit;

-- ── Team members hold their team role ───────────────────────────────────────
WITH missing AS (
    SELECT DISTINCT t.person_id, t.team_role, c.noktah_brand_id
    FROM client_team_assignments t
    JOIN clients c ON c.id = t.client_id
    JOIN people p ON p.id = t.person_id AND p.status = 'active'
    JOIN unit_roles u ON u.noktah_brand_id = c.noktah_brand_id AND u.role = t.team_role
    WHERE t.valid_to IS NULL
      AND NOT EXISTS (SELECT 1 FROM person_roles r
                      WHERE r.person_id = t.person_id AND r.role = t.team_role
                        AND r.noktah_brand_id = c.noktah_brand_id AND r.valid_to IS NULL)
), granted AS (
    INSERT INTO person_roles (person_id, role, noktah_brand_id)
    SELECT person_id, team_role, noktah_brand_id FROM missing
    RETURNING person_id, role, noktah_brand_id
)
INSERT INTO registry_changes (entity, entity_id, field, old_value, new_value, person_id)
SELECT 'person_role', g.person_id, g.role, NULL, jsonb_build_object('role', g.role, 'noktah_brand', b.brand_key), NULL
FROM granted g JOIN noktah_brands b ON b.id = g.noktah_brand_id;

-- ── A Person's Units ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS person_units (
    person_id       UUID NOT NULL REFERENCES people(id),
    noktah_brand_id UUID NOT NULL REFERENCES noktah_brands(id),
    added_by        UUID REFERENCES people(id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (person_id, noktah_brand_id)
);

INSERT INTO person_units (person_id, noktah_brand_id)
SELECT DISTINCT person_id, noktah_brand_id FROM person_roles WHERE valid_to IS NULL
ON CONFLICT DO NOTHING;

-- ── A Person's permissions ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS person_permissions (
    person_id  UUID NOT NULL REFERENCES people(id),
    permission TEXT NOT NULL,
    granted_by UUID REFERENCES people(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (person_id, permission),
    CONSTRAINT chk_person_permissions_permission CHECK (permission IN (
        'hub_access', 'edit_clients', 'edit_profil', 'approve_guideline', 'edit_requests',
        'run_intake', 'manage_people'))
);

INSERT INTO person_permissions (person_id, permission)
SELECT DISTINCT r.person_id, unnest(u.default_permissions)
FROM person_roles r
JOIN unit_roles u ON u.noktah_brand_id = r.noktah_brand_id AND u.role = r.role
WHERE r.valid_to IS NULL
ON CONFLICT DO NOTHING;

INSERT INTO schema_migrations (version) VALUES ('012_hub_units_roles_permissions')
ON CONFLICT (version) DO NOTHING;

-- 013_ai_usage: mirrored verbatim from config/postgres/migrations/013_ai_usage.sql.
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

-- 014_venyu_team_slots: mirrored verbatim from config/postgres/migrations/014_venyu_team_slots.sql.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_team_role') THEN
        ALTER TABLE client_team_assignments DROP CONSTRAINT chk_team_role;
    END IF;
    ALTER TABLE client_team_assignments ADD CONSTRAINT chk_team_role CHECK (team_role IN (
        'account_executive', 'content_planner', 'field_associate', 'content_editor', 'qc',
        'quality_assurance', 'production_manager'));
END $$;

UPDATE unit_roles u SET team_slot = true
FROM noktah_brands b
WHERE b.id = u.noktah_brand_id AND b.brand_key = 'venyu'
  AND u.role IN ('production_manager', 'quality_assurance') AND NOT u.team_slot;

INSERT INTO schema_migrations (version) VALUES ('014_venyu_team_slots')
ON CONFLICT (version) DO NOTHING;
