-- Reverses 015_hub_automation_reports. Drops Greenlights, batch history, the Jira copy,
-- harvest attempts, review marks and sanctions: take a backup first. The permission CHECK
-- goes back to the 012 list, so the three keys are revoked first.
--
-- Run with: docker exec -i postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" < 015_hub_automation_reports.down.sql

BEGIN;

DROP TABLE IF EXISTS sanction_letter_counters;
DROP TABLE IF EXISTS sanctions;
DROP TABLE IF EXISTS post_review_marks;
DROP TABLE IF EXISTS harvest_attempts;
DROP TABLE IF EXISTS event_point_comments;
DROP TABLE IF EXISTS jira_sync_state;
DROP TABLE IF EXISTS jira_issue_changes;
DROP TABLE IF EXISTS jira_issues;
DROP TABLE IF EXISTS content_plan_flags;
DROP TABLE IF EXISTS content_plan_issues;
DROP TABLE IF EXISTS jira_batch_rows;
DROP TABLE IF EXISTS jira_batches;
DROP TABLE IF EXISTS content_plan_greenlights;
DROP TABLE IF EXISTS content_plans;

ALTER TABLE people DROP COLUMN IF EXISTS started_on;

DELETE FROM person_permissions WHERE permission IN ('manage_automation', 'view_reports', 'view_incentive');
UPDATE unit_roles SET default_permissions = ARRAY(
    SELECT p FROM unnest(default_permissions) AS p
    WHERE p NOT IN ('manage_automation', 'view_reports', 'view_incentive'));

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_person_permissions_permission') THEN
        ALTER TABLE person_permissions DROP CONSTRAINT chk_person_permissions_permission;
    END IF;
    ALTER TABLE person_permissions ADD CONSTRAINT chk_person_permissions_permission CHECK (permission IN (
        'hub_access', 'edit_clients', 'edit_profil', 'approve_guideline', 'edit_requests',
        'run_intake', 'manage_people'));
END $$;

DELETE FROM schema_migrations WHERE version = '015_hub_automation_reports';

COMMIT;
