-- Migration: 014_venyu_team_slots
-- A Venyu client's team has two slots: Production Manager and Quality Assurance
-- (decided 2026-09-26). Until now Venyu had none, so a Venyu client's Registry showed
-- no team.
--
-- Widens chk_team_role by DROP + ADD with a strict superset (adds production_manager;
-- quality_assurance was already allowed), the one constraint replacement schema.md
-- permits, and marks the two Venyu roles as team slots in the role catalog.
--
-- Run with: docker exec -i postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" < 014_venyu_team_slots.sql

BEGIN;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_team_role') THEN
        ALTER TABLE client_team_assignments DROP CONSTRAINT chk_team_role;
    END IF;
    ALTER TABLE client_team_assignments ADD CONSTRAINT chk_team_role CHECK (team_role IN (
        'account_executive', 'content_planner', 'field_associate', 'content_editor', 'qc',
        'quality_assurance', 'production_manager'));
END $$;

UPDATE unit_roles u SET team_slot = true
FROM noktah_brands b
WHERE b.id = u.noktah_brand_id AND b.brand_key = 'venyu'
  AND u.role IN ('production_manager', 'quality_assurance') AND NOT u.team_slot;

INSERT INTO schema_migrations (version) VALUES ('014_venyu_team_slots')
ON CONFLICT (version) DO NOTHING;

COMMIT;
