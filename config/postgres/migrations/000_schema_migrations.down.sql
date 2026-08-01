-- Migration: 000_schema_migrations (down)
-- Drops only the version-tracking table 000 introduced.

BEGIN;

DROP TABLE IF EXISTS schema_migrations;

COMMIT;
