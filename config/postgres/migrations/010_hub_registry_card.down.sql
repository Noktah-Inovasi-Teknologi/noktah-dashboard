-- Reverses 010_hub_registry_card.
--
-- Drops the Hub tables and the columns 010 added to `clients`, in reverse
-- dependency order. This DESTROYS Hub data (Registry edits, People, roles,
-- Client Cards, Intakes); take a backup first (flows/db_backup.py).
--
-- Run with: docker exec -i postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" < 010_hub_registry_card.down.sql

BEGIN;

DROP TABLE IF EXISTS hub_sync_state;
DROP TABLE IF EXISTS hub_alerts_sent;
DROP TABLE IF EXISTS ai_ledger;
DROP TABLE IF EXISTS client_summaries;
DROP TABLE IF EXISTS client_request_events;
DROP TABLE IF EXISTS client_requests;
DROP TABLE IF EXISTS card_values;
DROP TABLE IF EXISTS intake_proposals;
DROP TABLE IF EXISTS intakes;
DROP TABLE IF EXISTS card_definitions;
DROP TABLE IF EXISTS registry_changes;
DROP TABLE IF EXISTS client_team_assignments;

ALTER TABLE clients DROP CONSTRAINT IF EXISTS chk_clients_quotas_nonnegative;
ALTER TABLE clients DROP CONSTRAINT IF EXISTS chk_clients_status;
ALTER TABLE clients DROP CONSTRAINT IF EXISTS fk_clients_noktah_brand;
ALTER TABLE clients DROP COLUMN IF EXISTS card_version;
ALTER TABLE clients DROP COLUMN IF EXISTS version;
ALTER TABLE clients DROP COLUMN IF EXISTS sheet_row_name;
ALTER TABLE clients DROP COLUMN IF EXISTS jira_component_id;
ALTER TABLE clients DROP COLUMN IF EXISTS content_plan_folder_id;
ALTER TABLE clients DROP COLUMN IF EXISTS drive_folder_id;
ALTER TABLE clients DROP COLUMN IF EXISTS quota_short_video;
ALTER TABLE clients DROP COLUMN IF EXISTS quota_story;
ALTER TABLE clients DROP COLUMN IF EXISTS quota_post;
ALTER TABLE clients DROP COLUMN IF EXISTS is_internal;
ALTER TABLE clients DROP COLUMN IF EXISTS status;
ALTER TABLE clients DROP COLUMN IF EXISTS noktah_brand_id;

DROP TABLE IF EXISTS person_roles;
DROP TABLE IF EXISTS person_emails;
DROP TABLE IF EXISTS people;
DROP TABLE IF EXISTS noktah_brands;

DELETE FROM schema_migrations WHERE version = '010_hub_registry_card';

COMMIT;
