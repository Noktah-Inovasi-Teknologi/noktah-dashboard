-- Migration: 008_observation_history
-- Feature: 006-longitudinal-metric-capture
--
-- Makes engagement accumulation measurable. Before this migration a content
-- item's counts were captured once and frozen: harvested_signals is UNIQUE on
-- (platform, content_id) with a single row per post, so the schema had no slot
-- for a second observation. A post that earned 5,000 likes in six hours and one
-- that earned 5,000 over three weeks were indistinguishable in the store,
-- though they are entirely different creative outcomes.
--
--   metric_observations  -- "what was seen, and when"   (append-only, N per item)
--   velocity_intervals   -- "how fast it accrued"       (derived from each pair)
--   velocity_status      -- "why is there no velocity"  (view, exactly one reason)
--
-- Plus one widened vocabulary on capture_outcomes so a missed metric refresh is
-- recordable there rather than in a duplicate table.
--
-- Additive except for that CHECK replacement, which is a strict SUPERSET —
-- every currently-valid value stays valid, so it cannot reject an existing row.
-- Justified in specs/006-longitudinal-metric-capture/plan.md § Complexity
-- Tracking. Idempotent and transactional.
--
-- Run with: docker exec -i postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" < 008_observation_history.sql

BEGIN;

-- ---------------------------------------------------------------------------
-- 1. metric_observations -- append-only observation history
-- ---------------------------------------------------------------------------
-- Keyed by (platform, content_id), NOT by harvested_signals.id — the same
-- decision, for the same reason, as capture_outcomes in migration 007. The
-- identity of a content item is the platform pair; harvested_signals rows are
-- reachable by the failure-retry purge path, and an observation must outlive
-- that purge (FR-005). The media was bad; the past measurements were not.
--
-- NULL metrics are load-bearing: they mean "not published / not captured", and
-- field_availability + capture_outcomes say which. A zero is a real observation.
-- Measured: Instagram publishes likes on 791/793 rows here but views/comments on
-- only 338, so sparse columns are the expected state, not a defect.
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
    -- 'legacy' marks a value inherited from the pre-feature single-row record,
    -- observed at an unknown point in the item's life. A velocity interval
    -- anchored on one must stay identifiable as such (FR-023a).
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_metric_observations_provenance') THEN
        ALTER TABLE metric_observations
            ADD CONSTRAINT chk_metric_observations_provenance
            CHECK (provenance IN ('captured', 'legacy'));
    END IF;
    -- An observation recording nothing is not an observation. The correct
    -- record for "the capture produced no counts" is a capture_outcomes row
    -- with outcome='no_match' — storing an all-NULL observation instead would
    -- make the series look longer than the evidence supports.
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

-- Makes the harvest observation pass safely retryable: a re-run at the same
-- instant cannot double-insert. Distinct instants remain distinct rows, which
-- is what lets two runs happen in one day — the rate for that pair is then
-- withheld by the minimum-interval rule rather than being suppressed here.
-- Also serves every read of this table: the series walk, the "latest
-- observation" lookup, and the consecutive-pair ordering in velocity derivation.
-- A unique B-tree scans identically to a plain one, so a separate index on the
-- same three columns would only double write cost on the fastest-growing table
-- in the schema.
CREATE UNIQUE INDEX IF NOT EXISTS uq_metric_observation_capture
    ON metric_observations (platform, content_id, observed_at);

CREATE INDEX IF NOT EXISTS ix_metric_observations_run
    ON metric_observations (run_id);
CREATE INDEX IF NOT EXISTS ix_metric_observations_account
    ON metric_observations (account_id, observed_at DESC);

-- ---------------------------------------------------------------------------
-- 2. velocity_intervals -- derived, recomputed in place
-- ---------------------------------------------------------------------------
-- NOT an observation. Constitution VII governs observations; a derived row is a
-- pure function of two immutable endpoints plus configuration, and the spec
-- requires it to be re-derivable after a threshold change (FR-016c). Overwriting
-- one destroys nothing that cannot be reproduced.
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
    -- Equal timestamps are impossible (uq_metric_observation_capture forbids
    -- two observations of one item at the same instant), so a zero elapsed is a
    -- data defect. Fail loudly here rather than defending against it in code
    -- with a guard clause that would hide the bug.
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
    -- An acceleration verdict must name the plateau it accelerated from
    -- (FR-015). A flag with no antecedent is an assertion, not a finding.
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

-- This is what makes re-derivation idempotent (FR-016c): the upsert targets it.
CREATE UNIQUE INDEX IF NOT EXISTS uq_velocity_interval_pair
    ON velocity_intervals (from_observation_id, to_observation_id);

CREATE INDEX IF NOT EXISTS ix_velocity_intervals_item
    ON velocity_intervals (platform, content_id);
CREATE INDEX IF NOT EXISTS ix_velocity_intervals_acceleration
    ON velocity_intervals (is_acceleration) WHERE is_acceleration;

-- ---------------------------------------------------------------------------
-- 3. capture_outcomes -- widen capture_kind (STRICT SUPERSET)
-- ---------------------------------------------------------------------------
-- Adds 'metric_refresh'. All four original kinds are retained verbatim; this
-- cannot reject an existing row. Reusing capture_outcomes rather than adding a
-- parallel table keeps "why is this value missing" answerable in ONE place —
-- a UNION across two tables would reintroduce exactly the ambiguity feature 005
-- removed.
--
-- No change is needed to chk_capture_outcomes_reason: it constrains the reason
-- vocabulary only when outcome='failed', and an item beyond listing reach is
-- outcome='not_attempted'. Aging out is not a failure.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_capture_outcomes_kind') THEN
        ALTER TABLE capture_outcomes DROP CONSTRAINT chk_capture_outcomes_kind;
    END IF;
    ALTER TABLE capture_outcomes
        ADD CONSTRAINT chk_capture_outcomes_kind
        CHECK (capture_kind IN (
            'instagram_clip_stats',
            'instagram_feed_stats',
            'instagram_profile_info',
            'tiktok_stats',
            'metric_refresh'));
END $$;

-- ---------------------------------------------------------------------------
-- 4. velocity_status -- exactly one reason per item (FR-024)
-- ---------------------------------------------------------------------------
-- A VIEW, not a stored column. History status is derived (FR-021a): a stored
-- flag would need maintaining on every observation write and could silently
-- disagree with the observations it describes. A view cannot drift.
--
-- Branch order is load-bearing. 'legacy_only' is tested before 'observed_once'
-- because both have exactly one observation and only provenance separates them;
-- the captured_count = 0 term is the discriminator. Dropping it would silently
-- reclassify every pre-feature item as merely under-observed.
--
-- The terminal 'UNRESOLVED' branch must always return zero rows. A non-zero
-- count is a defect, asserted by test_absence_classification.py.
-- The item universe is the UNION of both tables, not just the observation
-- table. An item carrying no metric anywhere has nothing to seed, so it has
-- zero observations — and if the view were driven by observations alone it
-- would vanish from the classification entirely, lacking a velocity AND a
-- reason. That is precisely the state FR-024 forbids. Measured on the
-- rehearsal clone: 2 of 819 rows are in exactly that position.
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
           -- Before the single-observation branches: an item with NO
           -- observation is not an item observed once.
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

COMMIT;
