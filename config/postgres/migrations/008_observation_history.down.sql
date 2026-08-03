-- Reverses: 008_observation_history
-- Feature: 006-longitudinal-metric-capture
--
-- Like 007's reversal (and unlike the 001-003 baselines, whose down scripts are
-- intentional no-ops), this one is real and safe: both tables and the view are
-- CREATED by 008 and hold only data this feature produced.
--
-- Reversing DOES discard every metric observation captured since the migration,
-- and those cannot be reconstructed — engagement is only ever observable now.
-- The single latest value per item survives in harvested_signals, so the store
-- returns to the pre-feature state: current numbers, no history, no velocity.
--
-- The capture_kind CHECK is restored to its exact original four-value form. Any
-- capture_outcomes row written with capture_kind='metric_refresh' must be
-- removed first, or the ADD CONSTRAINT will fail — deliberately. Silently
-- dropping those rows would discard recorded provenance, which is the one thing
-- this system is least willing to lose.

BEGIN;

DELETE FROM capture_outcomes WHERE capture_kind = 'metric_refresh';

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
            'tiktok_stats'));
END $$;

DROP VIEW IF EXISTS velocity_status;
DROP TABLE IF EXISTS velocity_intervals;
DROP TABLE IF EXISTS metric_observations;

DELETE FROM schema_migrations WHERE version = '008_observation_history';

COMMIT;
