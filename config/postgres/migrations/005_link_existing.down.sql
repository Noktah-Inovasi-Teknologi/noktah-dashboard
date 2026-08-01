-- Migration: 005_link_existing (down)
-- Drops only the five columns (and their indexes/FKs) 005 introduced. Every
-- existing column on harvested_signals, harvested_items, and knowledge_records
-- is untouched (FR-027) — this cannot lose data that predates the migration,
-- since the columns being dropped never held anything but data 005 itself wrote.

BEGIN;

ALTER TABLE harvested_signals DROP CONSTRAINT IF EXISTS fk_signal_account;
ALTER TABLE harvested_signals DROP CONSTRAINT IF EXISTS fk_signal_run;
DROP INDEX IF EXISTS ix_harvested_signals_account;
ALTER TABLE harvested_signals DROP COLUMN IF EXISTS account_id;
ALTER TABLE harvested_signals DROP COLUMN IF EXISTS run_id;

ALTER TABLE harvested_items DROP CONSTRAINT IF EXISTS fk_item_account;
ALTER TABLE harvested_items DROP CONSTRAINT IF EXISTS fk_item_run;
DROP INDEX IF EXISTS ix_harvested_items_account;
ALTER TABLE harvested_items DROP COLUMN IF EXISTS account_id;
ALTER TABLE harvested_items DROP COLUMN IF EXISTS run_id;

ALTER TABLE knowledge_records DROP CONSTRAINT IF EXISTS fk_knowledge_client;
DROP INDEX IF EXISTS ix_knowledge_records_client;
ALTER TABLE knowledge_records DROP COLUMN IF EXISTS client_id;

DELETE FROM schema_migrations WHERE version = '005_link_existing';

COMMIT;
