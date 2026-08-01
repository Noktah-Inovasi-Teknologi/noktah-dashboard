-- Migration: 006_enforce_account_link (down)
-- Drops only the two CHECK constraints 006 introduced. account_id itself
-- (added by 005) is untouched, and no row is affected.

BEGIN;

ALTER TABLE harvested_signals DROP CONSTRAINT IF EXISTS chk_signal_account;
ALTER TABLE harvested_items DROP CONSTRAINT IF EXISTS chk_item_account;

DELETE FROM schema_migrations WHERE version = '006_enforce_account_link';

COMMIT;
