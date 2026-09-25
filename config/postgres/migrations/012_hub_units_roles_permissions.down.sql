-- Reverses 012_hub_units_roles_permissions.
--
-- Drops the role catalog, Units and permissions, and puts roles back in 010's
-- shape: the old keys (project_manager, qc), Owner with no Noktah Brand. The
-- Noktah Unit has no place in that shape, so its other roles (Sales & Marketing)
-- move to Eskala, and roles 010 never knew (the Venyu developer roles) end.
-- Team roles granted by 012's backfill stay: they are valid 010 roles.
-- This DESTROYS the permissions set in the Hub since 012; take a backup first.
--
-- Run with: docker exec -i postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" < 012_hub_units_roles_permissions.down.sql

BEGIN;

DROP TABLE IF EXISTS person_permissions;
DROP TABLE IF EXISTS person_units;
DROP TABLE IF EXISTS unit_roles;

ALTER TABLE person_roles DROP CONSTRAINT IF EXISTS chk_person_roles_unit;

UPDATE person_roles SET noktah_brand_id = NULL WHERE role = 'owner';
UPDATE person_roles SET noktah_brand_id = (SELECT id FROM noktah_brands WHERE brand_key = 'eskala')
WHERE noktah_brand_id = (SELECT id FROM noktah_brands WHERE brand_key = 'noktah');
UPDATE person_roles SET role = 'project_manager' WHERE role = 'production_manager';
UPDATE person_roles SET role = 'qc' WHERE role = 'quality_assurance';
UPDATE person_roles SET valid_to = COALESCE(valid_to, now()), role = 'content_planner'
WHERE role IN ('frontend_developer', 'backend_developer', 'devops', 'mobile_developer');
UPDATE client_team_assignments SET team_role = 'qc' WHERE team_role = 'quality_assurance';

ALTER TABLE person_roles DROP CONSTRAINT IF EXISTS chk_person_roles_role;
ALTER TABLE person_roles ADD CONSTRAINT chk_person_roles_role CHECK (role IN (
    'owner', 'brand_manager', 'project_manager', 'account_executive', 'sales_marketing',
    'content_planner', 'field_associate', 'content_editor', 'qc'));
ALTER TABLE person_roles ADD CONSTRAINT chk_person_roles_brand_scope
    CHECK ((role = 'owner') = (noktah_brand_id IS NULL));

ALTER TABLE client_team_assignments DROP CONSTRAINT IF EXISTS chk_team_role;
ALTER TABLE client_team_assignments ADD CONSTRAINT chk_team_role CHECK (team_role IN (
    'account_executive', 'content_planner', 'field_associate', 'content_editor', 'qc'));

DELETE FROM registry_changes WHERE entity IN ('person_unit', 'person_permission');
ALTER TABLE registry_changes DROP CONSTRAINT IF EXISTS chk_registry_changes_entity;
ALTER TABLE registry_changes ADD CONSTRAINT chk_registry_changes_entity CHECK (entity IN (
    'client', 'team', 'account_link', 'person', 'person_email', 'person_role'));

DELETE FROM noktah_brands WHERE brand_key = 'noktah';
ALTER TABLE noktah_brands DROP CONSTRAINT IF EXISTS chk_noktah_brands_kind;
ALTER TABLE noktah_brands DROP COLUMN IF EXISTS kind;

DELETE FROM schema_migrations WHERE version = '012_hub_units_roles_permissions';

COMMIT;
