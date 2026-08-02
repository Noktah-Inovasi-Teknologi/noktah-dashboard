-- Reverses: 007_signal_field_coverage
-- Feature: 005-signal-field-coverage
--
-- Unlike the 001-003 baselines (whose .down.sql is an intentional no-op because
-- DROP would destroy data predating the migration), this reversal is real and
-- safe: all three objects are CREATED by 007 and hold only data this feature
-- produced. Nothing that predates the migration is touched.
--
-- Reversing does lose every recorded capture outcome and availability
-- determination — that is provenance, not observations. The underlying
-- harvested_signals rows survive untouched; they simply return to being
-- ambiguous about why a value is empty, which is the pre-feature state.

BEGIN;

DROP TABLE IF EXISTS capture_outcomes;
DROP TABLE IF EXISTS field_availability;

ALTER TABLE harvested_signals DROP COLUMN IF EXISTS shares;

DELETE FROM schema_migrations WHERE version = '007_signal_field_coverage';

COMMIT;
