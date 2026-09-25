-- Reversal: 009_structured_extraction
-- Feature: 007-structured-extraction
--
-- A REAL reversal, unlike the deliberate no-ops of baseline migrations 001-003.
-- Every table 009 creates is new, so dropping them destroys nothing that predates
-- the migration. Extraction data IS lost; harvested_signals is untouched, because
-- backfill never wrote to it (research.md R10).
--
-- Order matters: children before parents, because the composite foreign keys
-- (fk_beat_function, fk_beat_extraction_vocab, fk_attribute_term) would otherwise
-- block the drop.
--
-- Run with: docker exec -i postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" < 009_structured_extraction.down.sql

BEGIN;

DROP VIEW IF EXISTS extraction_status;

DROP TABLE IF EXISTS extraction_batch_runs;
DROP TABLE IF EXISTS calibration_sample_members;
DROP TABLE IF EXISTS extraction_quarantine;
DROP TABLE IF EXISTS extraction_attributes;
DROP TABLE IF EXISTS extraction_beats;
DROP TABLE IF EXISTS content_extractions;
DROP TABLE IF EXISTS extraction_vocabulary_terms;

-- Restore runs.kind to its original two-value vocabulary.
--
-- This narrows the constraint, so it FAILS if any kind='extraction' row survives.
-- Those rows are this migration's own — a costed extraction batch could not have
-- been recorded before 009 widened the CHECK — so deleting them here reverses
-- exactly what 009 introduced and nothing else. The extraction_batch_runs rows
-- that referenced them are already gone (dropped above), and the ON DELETE CASCADE
-- on fk_batch_run would have handled any that were not.
DELETE FROM runs WHERE kind = 'extraction';

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_runs_kind') THEN
        ALTER TABLE runs DROP CONSTRAINT chk_runs_kind;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'runs_kind_check') THEN
        ALTER TABLE runs
            ADD CONSTRAINT runs_kind_check
            CHECK (kind IN ('collection', 'generation'));
    END IF;
END $$;

DELETE FROM schema_migrations WHERE version = '009_structured_extraction';

COMMIT;
