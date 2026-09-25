-- Migration: 011_client_brand_required (down)
-- Drops only the CHECK constraint 011 introduced. noktah_brand_id itself (added
-- by 010) and every row are untouched.

BEGIN;

ALTER TABLE clients DROP CONSTRAINT IF EXISTS chk_clients_noktah_brand;

DELETE FROM schema_migrations WHERE version = '011_client_brand_required';

COMMIT;
