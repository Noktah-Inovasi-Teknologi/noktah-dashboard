-- Migration: 004_relational_spine
-- Feature: 004-relational-spine
-- The nine new tables that give the schema its relational spine: clients,
-- client_aliases, accounts, account_handles, client_account_roles,
-- account_follower_observations, runs, briefs. schema_migrations already
-- exists (000) but is a hard prerequisite — this migration fails without it.
--
-- Idempotent, transactional, additive. See specs/004-relational-spine/data-model.md
-- and specs/004-relational-spine/research.md R7 for the rationale behind every
-- constraint below — none of these are decorative.
--
-- Run with: docker exec -i postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" < 004_relational_spine.sql

BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ---------------------------------------------------------------------------
-- clients
-- ---------------------------------------------------------------------------
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

-- ---------------------------------------------------------------------------
-- client_aliases — every name a client is known by (FR-002, FR-003, FR-004)
-- ---------------------------------------------------------------------------
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

-- An alias resolves to at most one client (FR-003) — a violation here IS the
-- rejection the spec requires; reconciliation catches it and reports it.
CREATE UNIQUE INDEX IF NOT EXISTS uq_client_alias_key
    ON client_aliases (alias_key);

CREATE INDEX IF NOT EXISTS ix_client_aliases_client
    ON client_aliases (client_id);

-- ---------------------------------------------------------------------------
-- accounts — one presence on one platform, identified by a surrogate id (FR-011)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS accounts (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    platform           TEXT NOT NULL CHECK (platform IN ('instagram', 'tiktok')),
    is_active          BOOLEAN NOT NULL DEFAULT true,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Redundant alone; lets account_handles carry a composite FK guaranteeing a
    -- handle's platform always matches its account's.
    CONSTRAINT uq_account_id_platform UNIQUE (id, platform)
);

-- ---------------------------------------------------------------------------
-- account_handles — handles an account has been observed under (FR-011a/b/c, FR-012)
-- ---------------------------------------------------------------------------
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

-- A handle resolves to one account per platform (FR-011c).
CREATE UNIQUE INDEX IF NOT EXISTS uq_account_handle_platform_key
    ON account_handles (platform, handle_key);

-- Exactly one current handle per account.
CREATE UNIQUE INDEX IF NOT EXISTS uq_account_handle_current
    ON account_handles (account_id)
    WHERE is_current;

CREATE INDEX IF NOT EXISTS ix_account_handles_account
    ON account_handles (account_id);

-- ---------------------------------------------------------------------------
-- client_account_roles — the client-account relationship, and the home of role
-- (FR-007, FR-008, FR-009, FR-010)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS client_account_roles (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id          UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    account_id         UUID NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    role               TEXT NOT NULL CHECK (role IN ('owned', 'competitor', 'reference')),
    is_active          BOOLEAN NOT NULL DEFAULT true,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One relationship per client-account pair (FR-010) — a role is never ambiguous.
CREATE UNIQUE INDEX IF NOT EXISTS uq_client_account_pair
    ON client_account_roles (client_id, account_id);

-- At most one ACTIVE owner per account (FR-009). Scoped to is_active so a
-- handover (old owner deactivated, new owner added) is representable.
CREATE UNIQUE INDEX IF NOT EXISTS uq_account_owned_active
    ON client_account_roles (account_id)
    WHERE role = 'owned' AND is_active;

CREATE INDEX IF NOT EXISTS ix_client_account_roles_account
    ON client_account_roles (account_id);
CREATE INDEX IF NOT EXISTS ix_client_account_roles_client
    ON client_account_roles (client_id);

-- ---------------------------------------------------------------------------
-- account_follower_observations — append-only, observed-only (FR-013, constitution VI/VII)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS account_follower_observations (
    id                 BIGSERIAL PRIMARY KEY,
    account_id         UUID NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    observed_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- NOT NULL by design: no row means no observation. There is deliberately no
    -- is_estimated / interpolated_from column — an estimate must never share
    -- this field with an observation (constitution VI).
    follower_count     BIGINT NOT NULL,
    run_id             UUID NULL
);

CREATE INDEX IF NOT EXISTS ix_follower_observations_account_time
    ON account_follower_observations (account_id, observed_at DESC);

-- ---------------------------------------------------------------------------
-- runs — one execution of a collection or a generation (FR-019)
-- ---------------------------------------------------------------------------
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

-- Now that runs() exists, wire the deferred FK on account_follower_observations.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_follower_observations_run'
    ) THEN
        ALTER TABLE account_follower_observations
            ADD CONSTRAINT fk_follower_observations_run FOREIGN KEY (run_id) REFERENCES runs(id);
    END IF;
END $$;

-- ---------------------------------------------------------------------------
-- briefs — structure only in this feature; zero rows at ship time (SC-005)
-- ---------------------------------------------------------------------------
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

COMMIT;
