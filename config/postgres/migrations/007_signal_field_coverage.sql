-- Migration: 007_signal_field_coverage
-- Feature: 005-signal-field-coverage
--
-- Makes absence legible. Before this migration a NULL engagement value means
-- either "the platform never publishes this" or "our enrichment call failed" —
-- indistinguishable, so a collection regression cannot be told apart from a
-- platform limit. Two records resolve that:
--
--   field_availability  -- "can this ever be known?"   (platform, content_type, field)
--   capture_outcomes    -- "was it known this time?"   (append-only, per attempt)
--
-- Plus harvested_signals.shares, which roach already parses on both TikTok
-- paths (collect.py:450, :545) and which was being discarded at this boundary.
--
-- Additive only: one nullable column, two new tables. No DROP, no ALTER TYPE,
-- no SET NOT NULL on a populated table, no tightening of an existing
-- constraint. Idempotent and transactional.
--
-- Run with: docker exec -i postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" < 007_signal_field_coverage.sql

BEGIN;

-- ---------------------------------------------------------------------------
-- 1. harvested_signals.shares
-- ---------------------------------------------------------------------------
-- Nullable by design. Emptiness is meaningful and is resolved by the two tables
-- below, never by a sentinel. A share count of 0 is a real observation and MUST
-- NOT be conflated with absence (FR-005).
ALTER TABLE harvested_signals ADD COLUMN IF NOT EXISTS shares BIGINT NULL;

-- ---------------------------------------------------------------------------
-- 2. field_availability -- reference data, mirrored from config/field_availability.yaml
-- ---------------------------------------------------------------------------
-- Deliberately NOT foreign-keyed to any observation table: a determination must
-- be updatable without touching a single stored observation (FR-007). Joined to
-- harvested_signals on (platform, content_type) at query time instead.
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

-- Named ALTERs rather than inline column constraints, so this migration and the
-- init.sql mirror produce byte-identical pg_constraint entries (schema.md).
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_field_availability_platform') THEN
        ALTER TABLE field_availability
            ADD CONSTRAINT chk_field_availability_platform
            CHECK (platform IN ('instagram', 'tiktok'));
    END IF;
    -- The CLOSED status vocabulary (FR-002a). Enforced here, at the database
    -- boundary, and not only in the sync task: this is reference data other
    -- systems read, so a sixth value must be impossible to store rather than
    -- merely discouraged.
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
    -- A status without a reason is an assertion, not a determination.
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_field_availability_reason_nonempty') THEN
        ALTER TABLE field_availability
            ADD CONSTRAINT chk_field_availability_reason_nonempty
            CHECK (btrim(reason) <> '');
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS uq_field_availability_triple
    ON field_availability (platform, content_type, field_name);

-- ---------------------------------------------------------------------------
-- 3. capture_outcomes -- append-only per-attempt provenance
-- ---------------------------------------------------------------------------
-- Keyed by (platform, content_id), NOT by harvested_signals.id. A capture can
-- fail BEFORE a signal row exists; keying to the signal row would make exactly
-- those failures unrecordable, silently dropping the failures constitution
-- principle V says must never be dropped.
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
            CHECK (capture_kind IN (
                'instagram_clip_stats',
                'instagram_feed_stats',
                'instagram_profile_info',
                'tiktok_stats'));
    END IF;
    -- `no_match` (pass ran, returned nothing for THIS item) is distinct from
    -- `failed` (the pass itself broke). Collapsing them would leave the corpus
    -- exactly as ambiguous as before, with more tables (FR-003b).
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_capture_outcomes_outcome') THEN
        ALTER TABLE capture_outcomes
            ADD CONSTRAINT chk_capture_outcomes_outcome
            CHECK (outcome IN ('success', 'no_match', 'failed', 'not_attempted'));
    END IF;
    -- A failed capture MUST carry a classified reason (FR-004). "Unknown
    -- failure" is a classification decision for a human reading a traceback,
    -- not something to COALESCE into existence.
    --
    -- The `reason IS NOT NULL` term is NOT redundant. Without it, a row with
    -- outcome='failed' and reason=NULL evaluates to `false OR NULL` = NULL, and
    -- a CHECK constraint PASSES on NULL — so exactly the case this constraint
    -- exists to forbid would have slipped through. Caught by
    -- test_capture_outcome.py::test_failed_without_reason_rejected; do not
    -- "simplify" the term away.
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

COMMIT;
