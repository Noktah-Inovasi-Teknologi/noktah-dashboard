-- Migration: 011_client_brand_required
-- Feature: 008-hub-registry-client-card (FR-005, G-10)
-- Every Client belongs to exactly one Noktah Brand. Enforced with the schema.md
-- pattern: a named CHECK added NOT VALID, then VALIDATE (SHARE UPDATE EXCLUSIVE,
-- no table rewrite), rather than SET NOT NULL on a populated table.
--
-- PRECONDITION: FAILS BY DESIGN while any client has noktah_brand_id IS NULL.
-- Run it only after the one-time Registry import (flow hub-registry-import, real
-- run), which gives every Client the Eskala Noktah Brand. A failure here means
-- the import hasn't run, which is exactly when this should stop.
--
-- Run with: docker exec -i postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" < 011_client_brand_required.sql

BEGIN;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_clients_noktah_brand') THEN
        ALTER TABLE clients
            ADD CONSTRAINT chk_clients_noktah_brand CHECK (noktah_brand_id IS NOT NULL) NOT VALID;
    END IF;
END $$;

ALTER TABLE clients VALIDATE CONSTRAINT chk_clients_noktah_brand;

INSERT INTO schema_migrations (version) VALUES ('011_client_brand_required')
ON CONFLICT (version) DO NOTHING;

COMMIT;
