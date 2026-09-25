-- Migration: 012_hub_units_roles_permissions
-- Feature: Hub roles, units and permissions (after 008-hub-registry-client-card)
--
-- A Person now has three separate parameters: the Units they work in, their roles,
-- and their feature permissions.
--
--   Unit          Noktah (the company group), Eskala or Venyu. Stored in
--                 noktah_brands, which gains `kind`: 'group' for Noktah, 'brand'
--                 for Eskala and Venyu. Clients still belong to a brand, never the
--                 group (the API refuses it).
--   unit_roles    the central role catalog: which roles exist in which Unit, their
--                 names, whether a Client team has a slot for them, and the
--                 permissions a new holder starts with.
--   person_units  the Units a Person belongs to.
--   person_permissions
--                 what a Person may do in the Hub. Before this, permissions came
--                 from the role alone; now the role only suggests them.
--
-- Data changes, all on the live rows (small: 3 roles, 44 team assignments):
--   - project_manager becomes production_manager and qc becomes quality_assurance,
--     in person_roles and client_team_assignments. Old history rows keep the old
--     keys; the Hub labels both.
--   - Owner and Sales & Marketing move to the Noktah Unit; every role now has a Unit.
--   - Every Person on a Client team gets that team role in the Client's brand, so a
--     team picker lists only the people who hold the role.
--   - Units and permissions are filled from the roles each Person holds, so nobody
--     gains or loses access on the day this runs.
--
-- Constraints are widened by DROP + ADD with a strict superset, and the one new
-- NOT NULL is the schema.md pattern (CHECK NOT VALID, then VALIDATE).
--
-- Run with: docker exec -i postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" < 012_hub_units_roles_permissions.sql

BEGIN;

-- ── Units ───────────────────────────────────────────────────────────────────
ALTER TABLE noktah_brands ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'brand';
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_noktah_brands_kind') THEN
        ALTER TABLE noktah_brands ADD CONSTRAINT chk_noktah_brands_kind CHECK (kind IN ('group', 'brand'));
    END IF;
END $$;

INSERT INTO noktah_brands (brand_key, display_name, slack_managerial_env, kind)
VALUES ('noktah', 'Noktah', 'SLACK_MANAGERIAL_NOKTAH', 'group')
ON CONFLICT (brand_key) DO NOTHING;

-- ── Role catalog ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS unit_roles (
    noktah_brand_id     UUID NOT NULL REFERENCES noktah_brands(id),
    role                TEXT NOT NULL,
    display_name        TEXT NOT NULL,
    sort_order          INTEGER NOT NULL,
    team_slot           BOOLEAN NOT NULL DEFAULT false,
    default_permissions TEXT[] NOT NULL DEFAULT '{}',
    PRIMARY KEY (noktah_brand_id, role)
);

INSERT INTO unit_roles (noktah_brand_id, role, display_name, sort_order, team_slot, default_permissions)
SELECT b.id, s.role, s.display_name, s.sort_order, s.team_slot, s.default_permissions
FROM (VALUES
    ('noktah', 'owner',               'Owner',               1, false,
        '{hub_access,edit_clients,edit_profil,approve_guideline,edit_requests,run_intake,manage_people}'::text[]),
    ('noktah', 'sales_marketing',     'Sales & Marketing',   2, false, '{hub_access}'::text[]),
    ('eskala', 'brand_manager',       'Brand Manager',       1, false,
        '{hub_access,edit_clients,edit_profil,approve_guideline,edit_requests,run_intake,manage_people}'::text[]),
    ('eskala', 'production_manager',  'Production Manager',  2, false,
        '{hub_access,edit_clients,edit_profil,edit_requests,run_intake}'::text[]),
    ('eskala', 'account_executive',   'Account Executive',   3, true,
        '{hub_access,edit_clients,edit_profil,edit_requests,run_intake}'::text[]),
    ('eskala', 'content_planner',     'Content Planner',     4, true, '{}'::text[]),
    ('eskala', 'field_associate',     'Field Associate',     5, true, '{}'::text[]),
    ('eskala', 'content_editor',      'Content Editor',      6, true, '{}'::text[]),
    ('eskala', 'quality_assurance',   'Quality Assurance',   7, true, '{}'::text[]),
    ('venyu',  'brand_manager',       'Brand Manager',       1, false,
        '{hub_access,edit_clients,edit_profil,approve_guideline,edit_requests,run_intake,manage_people}'::text[]),
    ('venyu',  'production_manager',  'Production Manager',  2, false,
        '{hub_access,edit_clients,edit_profil,edit_requests,run_intake}'::text[]),
    ('venyu',  'quality_assurance',   'Quality Assurance',   3, false, '{}'::text[]),
    ('venyu',  'frontend_developer',  'Front-end Developer', 4, false, '{}'::text[]),
    ('venyu',  'backend_developer',   'Back-end Developer',  5, false, '{}'::text[]),
    ('venyu',  'devops',              'DevOps',              6, false, '{}'::text[]),
    ('venyu',  'mobile_developer',    'Mobile Developer',    7, false, '{}'::text[])
) AS s(brand_key, role, display_name, sort_order, team_slot, default_permissions)
JOIN noktah_brands b ON b.brand_key = s.brand_key
ON CONFLICT (noktah_brand_id, role) DO NOTHING;

-- ── Change log: two new entities ───────────────────────────────────────────
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_registry_changes_entity') THEN
        ALTER TABLE registry_changes DROP CONSTRAINT chk_registry_changes_entity;
    END IF;
    ALTER TABLE registry_changes ADD CONSTRAINT chk_registry_changes_entity CHECK (entity IN (
        'client', 'team', 'account_link', 'person', 'person_email', 'person_role',
        'person_unit', 'person_permission'));
END $$;

-- ── Roles: new keys, every role in a Unit ───────────────────────────────────
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_person_roles_brand_scope') THEN
        ALTER TABLE person_roles DROP CONSTRAINT chk_person_roles_brand_scope;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_person_roles_role') THEN
        ALTER TABLE person_roles DROP CONSTRAINT chk_person_roles_role;
    END IF;
    -- A superset of 010's list: the old keys stay valid for rows already ended.
    ALTER TABLE person_roles ADD CONSTRAINT chk_person_roles_role CHECK (role IN (
        'owner', 'brand_manager', 'project_manager', 'account_executive', 'sales_marketing',
        'content_planner', 'field_associate', 'content_editor', 'qc',
        'production_manager', 'quality_assurance', 'frontend_developer', 'backend_developer',
        'devops', 'mobile_developer'));

    IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_team_role') THEN
        ALTER TABLE client_team_assignments DROP CONSTRAINT chk_team_role;
    END IF;
    ALTER TABLE client_team_assignments ADD CONSTRAINT chk_team_role CHECK (team_role IN (
        'account_executive', 'content_planner', 'field_associate', 'content_editor', 'qc',
        'quality_assurance'));
END $$;

UPDATE person_roles SET role = 'production_manager' WHERE role = 'project_manager';
UPDATE person_roles SET role = 'quality_assurance' WHERE role = 'qc';
UPDATE client_team_assignments SET team_role = 'quality_assurance' WHERE team_role = 'qc';

-- Sales & Marketing held in both brands becomes one role in Noktah: keep the
-- oldest active one, end the rest, so the move can't collide on uq_person_roles_active.
UPDATE person_roles SET valid_to = now()
WHERE role = 'sales_marketing' AND valid_to IS NULL
  AND id NOT IN (SELECT DISTINCT ON (person_id) id FROM person_roles
                 WHERE role = 'sales_marketing' AND valid_to IS NULL ORDER BY person_id, valid_from);
UPDATE person_roles SET noktah_brand_id = (SELECT id FROM noktah_brands WHERE brand_key = 'noktah')
WHERE role IN ('owner', 'sales_marketing')
  AND noktah_brand_id IS DISTINCT FROM (SELECT id FROM noktah_brands WHERE brand_key = 'noktah');

-- An active role its Unit's catalog doesn't list (e.g. Account Executive in Venyu)
-- ends here, logged, rather than living on outside the catalog.
WITH ended AS (
    UPDATE person_roles r SET valid_to = now()
    WHERE r.valid_to IS NULL
      AND NOT EXISTS (SELECT 1 FROM unit_roles u WHERE u.noktah_brand_id = r.noktah_brand_id AND u.role = r.role)
    RETURNING r.person_id, r.role, r.noktah_brand_id
)
INSERT INTO registry_changes (entity, entity_id, field, old_value, new_value, person_id)
SELECT 'person_role', e.person_id, e.role, jsonb_build_object('role', e.role, 'noktah_brand', b.brand_key),
       NULL, NULL
FROM ended e LEFT JOIN noktah_brands b ON b.id = e.noktah_brand_id;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_person_roles_unit') THEN
        ALTER TABLE person_roles
            ADD CONSTRAINT chk_person_roles_unit CHECK (noktah_brand_id IS NOT NULL) NOT VALID;
    END IF;
END $$;
ALTER TABLE person_roles VALIDATE CONSTRAINT chk_person_roles_unit;

-- ── Team members hold their team role ───────────────────────────────────────
WITH missing AS (
    SELECT DISTINCT t.person_id, t.team_role, c.noktah_brand_id
    FROM client_team_assignments t
    JOIN clients c ON c.id = t.client_id
    JOIN people p ON p.id = t.person_id AND p.status = 'active'
    JOIN unit_roles u ON u.noktah_brand_id = c.noktah_brand_id AND u.role = t.team_role
    WHERE t.valid_to IS NULL
      AND NOT EXISTS (SELECT 1 FROM person_roles r
                      WHERE r.person_id = t.person_id AND r.role = t.team_role
                        AND r.noktah_brand_id = c.noktah_brand_id AND r.valid_to IS NULL)
), granted AS (
    INSERT INTO person_roles (person_id, role, noktah_brand_id)
    SELECT person_id, team_role, noktah_brand_id FROM missing
    RETURNING person_id, role, noktah_brand_id
)
INSERT INTO registry_changes (entity, entity_id, field, old_value, new_value, person_id)
SELECT 'person_role', g.person_id, g.role, NULL, jsonb_build_object('role', g.role, 'noktah_brand', b.brand_key), NULL
FROM granted g JOIN noktah_brands b ON b.id = g.noktah_brand_id;

-- ── A Person's Units ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS person_units (
    person_id       UUID NOT NULL REFERENCES people(id),
    noktah_brand_id UUID NOT NULL REFERENCES noktah_brands(id),
    added_by        UUID REFERENCES people(id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (person_id, noktah_brand_id)
);

INSERT INTO person_units (person_id, noktah_brand_id)
SELECT DISTINCT person_id, noktah_brand_id FROM person_roles WHERE valid_to IS NULL
ON CONFLICT DO NOTHING;

-- ── A Person's permissions ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS person_permissions (
    person_id  UUID NOT NULL REFERENCES people(id),
    permission TEXT NOT NULL,
    granted_by UUID REFERENCES people(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (person_id, permission),
    CONSTRAINT chk_person_permissions_permission CHECK (permission IN (
        'hub_access', 'edit_clients', 'edit_profil', 'approve_guideline', 'edit_requests',
        'run_intake', 'manage_people'))
);

INSERT INTO person_permissions (person_id, permission)
SELECT DISTINCT r.person_id, unnest(u.default_permissions)
FROM person_roles r
JOIN unit_roles u ON u.noktah_brand_id = r.noktah_brand_id AND u.role = r.role
WHERE r.valid_to IS NULL
ON CONFLICT DO NOTHING;

INSERT INTO schema_migrations (version) VALUES ('012_hub_units_roles_permissions')
ON CONFLICT (version) DO NOTHING;

COMMIT;
