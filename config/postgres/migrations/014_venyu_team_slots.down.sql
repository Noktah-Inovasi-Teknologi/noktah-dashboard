-- Reverses 014_venyu_team_slots: Venyu's roles stop being team slots, and Production
-- Managers already placed on a Venyu client's team leave it (the assignment ends; the
-- history stays), so the narrower team-role list can be restored.
--
-- Run with: docker exec -i postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" < 014_venyu_team_slots.down.sql

BEGIN;

UPDATE unit_roles u SET team_slot = false
FROM noktah_brands b
WHERE b.id = u.noktah_brand_id AND b.brand_key = 'venyu'
  AND u.role IN ('production_manager', 'quality_assurance');

UPDATE client_team_assignments SET valid_to = COALESCE(valid_to, now()), team_role = 'account_executive'
WHERE team_role = 'production_manager' AND valid_to IS NULL;
UPDATE client_team_assignments SET team_role = 'account_executive' WHERE team_role = 'production_manager';

ALTER TABLE client_team_assignments DROP CONSTRAINT IF EXISTS chk_team_role;
ALTER TABLE client_team_assignments ADD CONSTRAINT chk_team_role CHECK (team_role IN (
    'account_executive', 'content_planner', 'field_associate', 'content_editor', 'qc',
    'quality_assurance'));

DELETE FROM schema_migrations WHERE version = '014_venyu_team_slots';

COMMIT;
