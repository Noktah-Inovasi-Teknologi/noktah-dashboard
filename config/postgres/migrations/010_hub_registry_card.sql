-- Migration: 010_hub_registry_card
-- Feature: 008-hub-registry-client-card (Noktah Hub v1)
--
-- The Hub becomes the one home for the Registry, the people list with roles,
-- and each Client's Client Card (Profil, Guideline, Requests, Summary), filled
-- through AI Intake. Tables:
--
--   noktah_brands            -- Eskala, Venyu (seeded)
--   people / person_emails / person_roles
--                            -- a Person, their emails (unique), their roles per
--                               Noktah Brand (append-only periods)
--   client_team_assignments  -- AE / CP / FA / CE / QC per Client, from People
--   registry_changes         -- append-only change log (who, what, old → new)
--   card_definitions         -- versioned card definition (config/hub/card_vN.yaml)
--   intakes / intake_proposals
--                            -- raw input (purged after 12 months) → Proposals
--   card_values              -- append-only history; ONE current + ONE pending per field
--   client_requests / client_request_events
--   client_summaries         -- AI summary; one current per Client
--   ai_ledger / hub_alerts_sent
--                            -- AI spend per call; monthly cap alerts sent once
--   hub_sync_state           -- the sheet copy's progress (single row)
--
-- `clients` gains ADDITIVE, nullable columns only. `noktah_brand_id` is made
-- mandatory later (migration 011, NOT VALID then VALIDATE) after the import
-- has filled it (schema.md "Making a column mandatory").
--
-- Seeds the first roles decided in the grill (G-15): Owner core@noktah.co,
-- Brand Manager of Venyu bagas@noktah.co, Brand Manager of Eskala
-- defila@noktah.co. Idempotent: re-running inserts nothing twice.
--
-- Run with: docker exec -i postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" < 010_hub_registry_card.sql

BEGIN;

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

COMMIT;
