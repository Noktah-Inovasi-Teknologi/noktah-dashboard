-- Migration: 006_enforce_account_link
-- Feature: 004-relational-spine
-- Turns "every row links to an account" from convention into an enforced
-- invariant (SC-001), without the table rewrite/ACCESS EXCLUSIVE lock a
-- straight `SET NOT NULL` would require on a populated table (research R6).
--
-- PRECONDITION: this migration FAILS BY DESIGN if any harvested_signals or
-- harvested_items row still has account_id IS NULL. It must run only after
-- flows/spine_backfill.py reports zero unlinked rows on both tables. That
-- failure is not a bug — it means the backfill is incomplete, which is
-- exactly when this should stop. See
-- specs/004-relational-spine/contracts/migrations.md.
--
-- Run with: docker exec -i postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" < 006_enforce_account_link.sql

BEGIN;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_signal_account') THEN
        ALTER TABLE harvested_signals
            ADD CONSTRAINT chk_signal_account CHECK (account_id IS NOT NULL) NOT VALID;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_item_account') THEN
        ALTER TABLE harvested_items
            ADD CONSTRAINT chk_item_account CHECK (account_id IS NOT NULL) NOT VALID;
    END IF;
END $$;

-- VALIDATE takes only SHARE UPDATE EXCLUSIVE (does not block reads/writes),
-- and is where the failure-by-design actually happens if any row is unlinked.
ALTER TABLE harvested_signals VALIDATE CONSTRAINT chk_signal_account;
ALTER TABLE harvested_items VALIDATE CONSTRAINT chk_item_account;

INSERT INTO schema_migrations (version) VALUES ('006_enforce_account_link')
ON CONFLICT (version) DO NOTHING;

COMMIT;
