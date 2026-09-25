-- Migration: 009_structured_extraction
-- Feature: 007-structured-extraction
--
-- Turns one model response from three opaque strings into a validated, versioned,
-- queryable record. Before this migration, content flow was free prose in
-- harvested_signals.content_flow: countable only by text-matching heuristics that
-- misfire on phrasing the model happened to vary.
--
--   content_extractions         -- one VALIDATED extraction (N per item, by version)
--   extraction_beats            -- the ordered flow, one row per beat
--   extraction_attributes       -- at most one value per dimension, own confidence
--   extraction_quarantine       -- append-only; what failed validation twice
--   extraction_vocabulary_terms -- the versioned closed vocabulary (data, not code)
--   calibration_sample_members  -- the fixed, re-runnable agreement sample
--   extraction_batch_runs       -- costed batch detail on runs (projection, outcomes)
--   extraction_status           -- view: exactly one reason per item
--
-- Additive: nothing is added to harvested_signals (research.md R9), no DROP of a
-- table or column, no constraint tightened. The ONE ALTER is a widening of
-- runs.kind by a strict SUPERSET so a costed extraction batch is recordable as a
-- run rather than in a parallel run table — the same replacement migration 008
-- made to chk_capture_outcomes_kind, and the only shape
-- .claude/rules/backend/schema.md permits, because a superset cannot reject an
-- existing row.
--
-- Idempotent and transactional.
--
-- Run with: docker exec -i postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" < 009_structured_extraction.sql

BEGIN;

-- ---------------------------------------------------------------------------
-- 1. extraction_vocabulary_terms -- the versioned closed vocabulary
-- ---------------------------------------------------------------------------
-- Created FIRST: extraction_beats and extraction_attributes both carry composite
-- foreign keys into it, which is what makes SC-002 ("every beat function is a
-- member of the vocabulary version that extraction recorded") structurally true
-- rather than a query someone remembers to run.
--
-- Reference data, synced from the version-controlled config/extraction/
-- vocabulary_v1.yaml — the same relationship field_availability has with
-- config/field_availability.yaml (feature 005). Rows are updated in place by the
-- sync, which does NOT breach Principle VII: a vocabulary term is reference data,
-- not an observation.
CREATE TABLE IF NOT EXISTS extraction_vocabulary_terms (
    version            TEXT NOT NULL,
    dimension          TEXT NOT NULL,
    term               TEXT NOT NULL,
    is_residual        BOOLEAN NOT NULL DEFAULT false,
    ordinal            SMALLINT NULL,
    description        TEXT NOT NULL,
    -- Set once ANY extraction records this version. From that moment the sync
    -- flow must refuse to alter it: silently mutating a recorded version makes
    -- FR-030 ("interpretable against the version it recorded") retroactively
    -- false for every row already stored. Enforced in the task, not here —
    -- a constraint cannot express "unless nothing references it yet".
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

-- At most one residual per dimension per version. This bounds the count from
-- ABOVE only — nothing here requires a residual to EXIST, and FR-003 ("no
-- discernible structure is recorded as one beat carrying the residual function")
-- is unsatisfiable without one. That existence check lives in the sync task,
-- which fails loudly on a dimension that publishes none.
CREATE UNIQUE INDEX IF NOT EXISTS uq_vocabulary_residual
    ON extraction_vocabulary_terms (version, dimension) WHERE is_residual;

-- NOTE: there is deliberately NO UNIQUE (version, term). An earlier draft carried
-- one to support a shorter beats FK, but it forbids two dimensions sharing a term
-- string inside one version — which would make FR-043a false the moment the
-- external attribute vocabulary (S-05) publishes a dimension containing a value
-- like 'hook'. Both FKs below target the full primary key instead.

-- ---------------------------------------------------------------------------
-- 2. content_extractions -- one row per VALIDATED extraction
-- ---------------------------------------------------------------------------
-- Nothing that failed validation ever appears here; that is extraction_quarantine.
--
-- New table rather than columns on harvested_signals, which is UNIQUE
-- (platform, content_id) — exactly one row per item. This feature needs SEVERAL
-- extractions per item: one per schema version (FR-029), one more when S-05 lands
-- and the second backfill runs (FR-043b), and two at once for every calibration
-- pair (FR-027). A one-row-per-item table has no slot for any of that.
--
-- Keyed by (platform, content_id), NOT harvested_signals.id — the same decision
-- migrations 007 and 008 made, for the same reason: the failure-retry purge can
-- delete a signal row, and extraction history must outlive it.
CREATE TABLE IF NOT EXISTS content_extractions (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    platform           TEXT NOT NULL,
    content_id         TEXT NOT NULL,
    account_id         UUID NULL,
    run_id             UUID NULL,
    -- Load-bearing. Every calibration item is extracted twice; if both copies sat
    -- here as 'production', every distribution over the calibration sample would
    -- be double-weighted (SC-018). Every analytical query filters on this.
    purpose            TEXT NOT NULL DEFAULT 'production',
    content_type       TEXT NOT NULL,
    -- The path ACTUALLY taken, derived from the media analysed — not the item's
    -- content_type label. A mixed carousel routes to the image path today
    -- regardless of the label, and 6 rows in the live corpus are carousels
    -- holding real transcripts because of it (FR-024, research.md R1).
    media_path         TEXT NOT NULL,
    subtitle           TEXT NULL,
    subtitle_absence   TEXT NULL,
    summary            TEXT NOT NULL,
    model_requested    TEXT NOT NULL,
    -- What OpenRouter ACTUALLY served. Provider routing runs with
    -- allow_fallbacks: true, so what was asked for and what answered are not the
    -- same question (FR-024).
    model_served       TEXT NOT NULL,
    provider           TEXT NULL,
    prompt_version     TEXT NOT NULL,
    schema_version     TEXT NOT NULL,
    vocabulary_version TEXT NOT NULL,
    -- Hash of the media actually sent — the cache key that makes FR-039's
    -- "unchanged input is not re-extracted" checkable.
    content_hash       TEXT NOT NULL,
    prompt_tokens      INTEGER NULL,
    completion_tokens  INTEGER NULL,
    -- MEASURED, from the provider's reported usage (FR-038). An estimate must
    -- never be written here: once stored it is indistinguishable from a
    -- measurement forever after (Constitution VI).
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
    -- FR-010's teeth. An empty string alone can no longer stand for three
    -- different facts. Either there is a transcript, or there is a recorded
    -- reason there is not. The third case ("extraction did not complete") is not
    -- representable here at all — such a run produces a quarantine row instead.
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
    -- Exactly one retry (FR-018): 2 means the retry succeeded. A third would be
    -- spend without evidence it helps.
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

-- THIS is FR-036's idempotence: re-running a backfill at the same version
-- conflicts and is skipped BEFORE any model call, so a bug in the skip logic
-- cannot cause a double charge. model_requested is in the key because a
-- calibration extracts the same item at the same version under a different model.
CREATE UNIQUE INDEX IF NOT EXISTS uq_extraction_version
    ON content_extractions (platform, content_id, purpose, model_requested,
                            prompt_version, schema_version, vocabulary_version);

-- Redundant as a uniqueness statement (id is already the PK) and exists solely
-- because Postgres requires a unique constraint covering exactly the columns a
-- foreign key references. It is what lets extraction_beats be held to its
-- parent's vocabulary version declaratively rather than by trigger.
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

-- ---------------------------------------------------------------------------
-- 3. extraction_beats -- the ordered flow (FR-001)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS extraction_beats (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    extraction_id      UUID NOT NULL,
    -- Denormalised from the parent PURELY so fk_beat_function is expressible;
    -- held equal to the parent's by fk_beat_extraction_vocab.
    vocabulary_version TEXT NOT NULL,
    -- Constant by construction. Present only so the FK can reach the vocabulary's
    -- full primary key (version, dimension, term) instead of a shorter key that
    -- would force term strings to be globally unique per version.
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
    -- A beat function outside its recorded vocabulary version CANNOT BE INSERTED.
    -- This is what makes SC-002 a property of the schema instead of a query
    -- someone remembers to run, and it is why vocabulary_version is denormalised
    -- onto the beat row at all.
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_beat_function') THEN
        ALTER TABLE extraction_beats
            ADD CONSTRAINT fk_beat_function
            FOREIGN KEY (vocabulary_version, dimension, function)
            REFERENCES extraction_vocabulary_terms (version, dimension, term);
    END IF;
    -- Holds the beat's vocabulary_version equal to its extraction's. A row-level
    -- CHECK cannot reference another table, so this is an FK; the alternative is
    -- a trigger, which is neither declarative nor visible to the parity check.
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

-- Contiguity (no gaps) is NOT expressible as a row constraint — a gap looks like
-- perfectly valid data to every per-row check. It is enforced at validation time
-- in roach, re-checked at the storage boundary, and asserted over the whole table
-- by this query, which must return zero rows:
--
--   SELECT extraction_id FROM extraction_beats
--   GROUP BY extraction_id
--   HAVING count(*) <> max(position) OR min(position) <> 1;
--
-- FR-003's "at least one beat" is likewise a cross-row invariant, asserted as a
-- zero-row query against content_extractions with no beats.

-- ---------------------------------------------------------------------------
-- 4. extraction_attributes -- one value per dimension, own confidence
-- ---------------------------------------------------------------------------
-- EMPTY until S-05 publishes dimensions. That is FR-043's inert-but-correct
-- state, not a defect.
CREATE TABLE IF NOT EXISTS extraction_attributes (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    extraction_id      UUID NOT NULL,
    dimension          TEXT NOT NULL,
    value              TEXT NOT NULL,
    -- An ordered scale, stored as text, NOT a float. A model's self-reported 0.83
    -- implies a calibration it does not have; Constitution VI forbids presenting
    -- false precision, and Constitution VIII requires filtering by a MINIMUM,
    -- which an ordered scale supports exactly as well.
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

-- FR-012 enforced at the database boundary, not merely in the validator.
CREATE UNIQUE INDEX IF NOT EXISTS uq_attribute_dimension
    ON extraction_attributes (extraction_id, dimension);

-- ---------------------------------------------------------------------------
-- 5. extraction_quarantine -- append-only record of every failed attempt
-- ---------------------------------------------------------------------------
-- Never joined into an analytical query. It exists to be COUNTED (FR-020) and
-- read by a human. Keyed by (platform, content_id) for the same reason as
-- content_extractions: a capture can fail before any signal row exists.
CREATE TABLE IF NOT EXISTS extraction_quarantine (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    platform           TEXT NOT NULL,
    content_id         TEXT NOT NULL,
    -- Which run produced this outcome. Without it a quarantine cannot be
    -- attributed to the batch that caused it, and SC-010 ("every backfill item
    -- resolves to exactly one outcome") becomes unanswerable once the run ends.
    run_id             UUID NULL,
    purpose            TEXT NOT NULL DEFAULT 'production',
    failure_kind       TEXT NOT NULL,
    -- What the model actually returned. NULL only when nothing arrived.
    raw_output         TEXT NULL,
    -- Both attempts' errors, ordered. A count without the evidence behind it
    -- would tell you the rate was rising and nothing about why.
    validation_errors  JSONB NOT NULL DEFAULT '[]'::jsonb,
    model_requested    TEXT NOT NULL,
    model_served       TEXT NULL,
    prompt_version     TEXT NOT NULL,
    schema_version     TEXT NOT NULL,
    vocabulary_version TEXT NOT NULL,
    prompt_tokens      INTEGER NULL,
    completion_tokens  INTEGER NULL,
    -- Spend still HAPPENED. Tokens were burned whether or not the output was
    -- usable; omitting the cost would make the measured baseline systematically
    -- low and let a pathological item that quarantines every time look free.
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
    -- CLOSED vocabulary, enforced at the database boundary and not only in the
    -- task. There is deliberately NO 'other': an escape hatch would quietly
    -- absorb exactly the novel failures worth noticing, and this is what FR-020's
    -- counts are grouped by. A tenth kind must be added by migration, on purpose.
    --
    -- Three of these record an outcome where NO model call happened at all
    -- (media_unavailable, spend_ceiling_reached, and unsupported_media where the
    -- provider refused before generating). raw_output and model_served are
    -- nullable precisely for that: this table is the record of every attempt's
    -- outcome, not only of bad model output.
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

-- ---------------------------------------------------------------------------
-- 6. calibration_sample_members -- the fixed, re-runnable sample (FR-027b)
-- ---------------------------------------------------------------------------
-- Membership is DATA so the same items are re-measured across model and version
-- changes. A LIMIT or a random draw would mean a changed agreement rate could be
-- the new model or the new sample, and no reading could tell them apart.
--
-- Agreement itself is NOT stored: it is computed from the purpose='calibration'
-- extractions on demand, so it can never disagree with the extractions it
-- summarises, and re-deriving after a vocabulary change needs no migration.
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

-- ---------------------------------------------------------------------------
-- 7. runs.kind -- widen to accept 'extraction' (STRICT SUPERSET)
-- ---------------------------------------------------------------------------
-- Both original kinds are retained verbatim; this cannot reject an existing row.
-- A costed extraction batch IS a run — it has a flow name, a start, an end, a
-- status and a summary, all of which runs already holds. Standing up a parallel
-- run table would duplicate every one of them.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'runs_kind_check') THEN
        ALTER TABLE runs DROP CONSTRAINT runs_kind_check;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_runs_kind') THEN
        ALTER TABLE runs DROP CONSTRAINT chk_runs_kind;
    END IF;
    ALTER TABLE runs
        ADD CONSTRAINT chk_runs_kind
        CHECK (kind IN ('collection', 'generation', 'extraction'));
END $$;

-- ---------------------------------------------------------------------------
-- 8. extraction_batch_runs -- costed batch detail ON runs
-- ---------------------------------------------------------------------------
-- The spec's "Backfill batch" entity. A DETAIL table on runs, not a run table of
-- its own. Serves backfill AND calibration, because FR-027e puts calibration on
-- backfill's terms: same dry-run projection, same threshold confirmation, same
-- measured cost.
--
-- Persisted rather than reported at run time because the projection must survive
-- to be compared against actual spend (SC-008) and the per-item outcomes must
-- survive to be counted (SC-010). A flow summary that vanishes when the process
-- exits makes both answerable only while watching.
CREATE TABLE IF NOT EXISTS extraction_batch_runs (
    run_id                 UUID PRIMARY KEY,
    batch_kind             TEXT NOT NULL,
    mode                   TEXT NOT NULL,
    -- HOW the set was selected, never a literal count: the corpus was 656 items
    -- at the audit snapshot and 819 signals when this feature was planned
    -- (research.md R1). Scope is "everything present when the run starts".
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
    -- Forbids the one thing Constitution VI actually forbids here: a projection
    -- claiming a measured basis while carrying no measurement. Note both terms
    -- are IS NOT NULL predicates, which never evaluate to NULL — the trap that
    -- lets a CHECK pass on NULL (and which chk_capture_outcomes_reason had to
    -- guard against in feature 005) does not apply.
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

-- THREE things deliberately have no constraint here:
--
--   * items_unclassified = 0 is NOT a CHECK. SC-010 wants unclassified outcomes
--     COUNTED; a constraint would make the defect unrecordable — the run would
--     fail to write its own summary rather than reporting the problem.
--   * The outcome counts summing to items_in_scope is NOT a CHECK. It is only
--     true once the run finishes, and the finish signal lives on runs.ended_at in
--     another table. Asserted by query, the same way beat contiguity is:
--
--       SELECT b.run_id FROM extraction_batch_runs b JOIN runs r ON r.id = b.run_id
--       WHERE r.ended_at IS NOT NULL AND b.mode <> 'dry_run'
--         AND b.items_extracted + b.items_cached + b.items_quarantined
--           + b.items_skipped + b.items_unclassified <> b.items_in_scope;
--
--   * actual_usd within +/-25% of projection_usd is NOT a CHECK. SC-008 permits
--     divergence WITH ITS CAUSE REPORTED; a constraint would reject the run
--     rather than the finding.

-- ---------------------------------------------------------------------------
-- 9. extraction_status -- exactly one reason per item (SC-010)
-- ---------------------------------------------------------------------------
-- "Why does this item have no production extraction at the current version?"
-- A VIEW, not a stored column. This is the third occurrence of this shape
-- (feature 005's why-empty query, feature 006's velocity_status) and it exists
-- for the same reason: a stored status column needs maintaining on every write
-- and can silently disagree with the rows it describes. A view cannot drift.
--
-- The item universe is the UNION of harvested_signals and content_extractions,
-- NOT extractions alone. An item never successfully extracted has zero extraction
-- rows; driven by extractions alone it would vanish from the view, having neither
-- a result nor a reason — precisely what FR-021 and SC-010 forbid. Feature 006
-- learned this against real data: 2 of 819 rows sat in exactly that gap.
--
-- Branch order is load-bearing. 'never_attempted' MUST be tested before the
-- version-comparison branches, or an item with no rows falls through into
-- 'superseded_version_only'.
--
-- The terminal 'UNRESOLVED' branch must always return zero rows.
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
    -- The current version pair is whatever the vocabulary table's highest
    -- version is. Derived rather than hardcoded so a v2 sync moves every
    -- already-extracted row into 'superseded_version_only' with no code change.
    SELECT max(version) AS version FROM extraction_vocabulary_terms
)
SELECT i.platform,
       i.content_id,
       COALESCE(ex.production_count, 0) AS production_extractions,
       CASE
           WHEN COALESCE(ex.production_count, 0) > 0
                AND ex.max_vocab_version >= (SELECT version FROM cur) THEN 'extracted'
           -- Before the version-comparison branches: an item with NO rows at all
           -- is not an item extracted under an older version.
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

COMMIT;
