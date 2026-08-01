-- Migration: 005_link_existing
-- Feature: 004-relational-spine
-- Links harvested_signals, harvested_items, and knowledge_records to the new
-- clients/accounts tables. Every added column is NULLABLE here — enforcement
-- of "every row must link" is a separate migration (006) that runs only after
-- the one-time backfill has populated these columns (research R6).
--
-- Touches no existing column, index, or constraint on any of the three tables
-- (FR-015). `profile_key` is untouched and stays: it is the raw observed value
-- and the only audit trail for the backfill; account_id is the interpretation.
--
-- Run with: docker exec -i postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" < 005_link_existing.sql

BEGIN;

-- harvested_signals -----------------------------------------------------
ALTER TABLE harvested_signals ADD COLUMN IF NOT EXISTS account_id UUID NULL;
ALTER TABLE harvested_signals ADD COLUMN IF NOT EXISTS run_id UUID NULL;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_signal_account') THEN
        ALTER TABLE harvested_signals
            ADD CONSTRAINT fk_signal_account FOREIGN KEY (account_id) REFERENCES accounts(id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_signal_run') THEN
        ALTER TABLE harvested_signals
            ADD CONSTRAINT fk_signal_run FOREIGN KEY (run_id) REFERENCES runs(id);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS ix_harvested_signals_account ON harvested_signals (account_id);

-- harvested_items ---------------------------------------------------------
ALTER TABLE harvested_items ADD COLUMN IF NOT EXISTS account_id UUID NULL;
ALTER TABLE harvested_items ADD COLUMN IF NOT EXISTS run_id UUID NULL;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_item_account') THEN
        ALTER TABLE harvested_items
            ADD CONSTRAINT fk_item_account FOREIGN KEY (account_id) REFERENCES accounts(id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_item_run') THEN
        ALTER TABLE harvested_items
            ADD CONSTRAINT fk_item_run FOREIGN KEY (run_id) REFERENCES runs(id);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS ix_harvested_items_account ON harvested_items (account_id);

-- knowledge_records ---------------------------------------------------------
-- Stays nullable permanently (unlike the two above) — FR-017 requires an
-- unresolvable record to be retained and reported, not rejected.
ALTER TABLE knowledge_records ADD COLUMN IF NOT EXISTS client_id UUID NULL;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_knowledge_client') THEN
        ALTER TABLE knowledge_records
            ADD CONSTRAINT fk_knowledge_client FOREIGN KEY (client_id) REFERENCES clients(id);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS ix_knowledge_records_client
    ON knowledge_records (client_id)
    WHERE superseded_by IS NULL;

INSERT INTO schema_migrations (version) VALUES ('005_link_existing')
ON CONFLICT (version) DO NOTHING;

COMMIT;
