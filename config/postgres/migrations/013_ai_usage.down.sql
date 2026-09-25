-- Reverses 013_ai_usage. Drops the recorded songbird spend; take a backup first.
--
-- Run with: docker exec -i postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" < 013_ai_usage.down.sql

BEGIN;

DROP TABLE IF EXISTS ai_usage;
DELETE FROM schema_migrations WHERE version = '013_ai_usage';

COMMIT;
