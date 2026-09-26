-- Migration: 015_hub_automation_reports
-- Noktah Hub: Otomasi & Laporan (spec 009, specs/009-hub-otomasi-laporan/data-model.md).
--
--   * three permissions: manage_automation ("Kelola otomasi"), view_reports ("Lihat laporan"),
--     view_incentive ("Lihat poin insentif"). Owner and Brand Managers get all three by default
--     and everyone now holding those roles is granted them (stored rows decide access).
--     chk_person_permissions_permission is widened by DROP + ADD with a strict superset, the one
--     constraint replacement schema.md permits.
--   * people.started_on: "Mulai bekerja", for the Incentive Framework's 30-day adaptation.
--   * Content Plans (scan cache, Greenlights, which row made which issue, watcher flags) and
--     "Buat issue Jira" batches.
--   * A copy of ESKL Content/Event issues and their changelog for Laporan (changes append-only).
--   * Harvest attempts (written by Prefect), review marks on harvested posts (with a one-time
--     import of the sheet-reviewed ad flags), sanctions and SP letter numbers.
--
-- Run with: docker exec -i postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" < 015_hub_automation_reports.sql

BEGIN;

-- ── Permissions ─────────────────────────────────────────────────────────────
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_person_permissions_permission') THEN
        ALTER TABLE person_permissions DROP CONSTRAINT chk_person_permissions_permission;
    END IF;
    ALTER TABLE person_permissions ADD CONSTRAINT chk_person_permissions_permission CHECK (permission IN (
        'hub_access', 'edit_clients', 'edit_profil', 'approve_guideline', 'edit_requests',
        'run_intake', 'manage_people', 'manage_automation', 'view_reports', 'view_incentive'));
END $$;

UPDATE unit_roles u
SET default_permissions = u.default_permissions || ARRAY(
    SELECT p FROM unnest(ARRAY['manage_automation', 'view_reports', 'view_incentive']) AS p
    WHERE NOT p = ANY (u.default_permissions))
WHERE u.role IN ('owner', 'brand_manager')
  AND NOT (u.default_permissions @> ARRAY['manage_automation', 'view_reports', 'view_incentive']);

INSERT INTO person_permissions (person_id, permission)
SELECT DISTINCT r.person_id, p
FROM person_roles r
CROSS JOIN unnest(ARRAY['manage_automation', 'view_reports', 'view_incentive']) AS p
WHERE r.valid_to IS NULL AND r.role IN ('owner', 'brand_manager')
ON CONFLICT DO NOTHING;

-- ── People: start date ──────────────────────────────────────────────────────
ALTER TABLE people ADD COLUMN IF NOT EXISTS started_on DATE;

-- ── Content Plans ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS content_plans (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id      UUID NOT NULL REFERENCES clients(id),
    month          DATE NOT NULL,
    state          TEXT NOT NULL,
    drive_file_id  TEXT,
    file_name      TEXT,
    tab_name       TEXT,
    rows           JSONB NOT NULL DEFAULT '[]'::jsonb,
    fingerprint    TEXT,
    problem        TEXT,
    scanned_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_content_plans_client_month UNIQUE (client_id, month),
    CONSTRAINT chk_content_plans_state CHECK (state IN ('found', 'missing', 'ambiguous', 'unreadable')),
    CONSTRAINT chk_content_plans_month_first CHECK (EXTRACT(DAY FROM month) = 1)
);

CREATE TABLE IF NOT EXISTS content_plan_greenlights (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    plan_id      UUID NOT NULL REFERENCES content_plans(id),
    fingerprint  TEXT NOT NULL,
    rows         JSONB NOT NULL,
    person_id    UUID NOT NULL REFERENCES people(id),
    at           TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_content_plan_greenlights_plan ON content_plan_greenlights (plan_id, at DESC);

CREATE TABLE IF NOT EXISTS jira_batches (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    requested_by  UUID NOT NULL REFERENCES people(id),
    requested_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    plan_ids      UUID[] NOT NULL,
    status        TEXT NOT NULL DEFAULT 'queued',
    flow_run_id   TEXT,
    total         INTEGER NOT NULL DEFAULT 0,
    created       INTEGER NOT NULL DEFAULT 0,
    failed        INTEGER NOT NULL DEFAULT 0,
    finished_at   TIMESTAMPTZ,
    error         TEXT,
    CONSTRAINT chk_jira_batches_status CHECK (status IN ('queued', 'running', 'done', 'failed'))
);

CREATE TABLE IF NOT EXISTS jira_batch_rows (
    batch_id    UUID NOT NULL REFERENCES jira_batches(id),
    plan_id     UUID NOT NULL REFERENCES content_plans(id),
    row_number  INTEGER NOT NULL,
    outcome     TEXT NOT NULL,
    issue_key   TEXT,
    reason      TEXT,
    at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (batch_id, plan_id, row_number),
    CONSTRAINT chk_jira_batch_rows_outcome CHECK (outcome IN ('created', 'failed', 'refused'))
);

CREATE TABLE IF NOT EXISTS content_plan_issues (
    issue_key         TEXT PRIMARY KEY,
    plan_id           UUID NOT NULL REFERENCES content_plans(id),
    row_number        INTEGER NOT NULL,
    fingerprint       TEXT NOT NULL,
    cells             JSONB NOT NULL,
    batch_id          UUID REFERENCES jira_batches(id),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_fingerprint  TEXT,
    last_seen_at      TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_content_plan_issues_plan ON content_plan_issues (plan_id);

CREATE TABLE IF NOT EXISTS content_plan_flags (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    plan_id        UUID NOT NULL REFERENCES content_plans(id),
    kind           TEXT NOT NULL,
    issue_key      TEXT,
    row_number     INTEGER,
    changes        JSONB NOT NULL DEFAULT '[]'::jsonb,
    detected_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    comment_state  TEXT NOT NULL DEFAULT 'not_needed',
    commented_at   TIMESTAMPTZ,
    comment_error  TEXT,
    resolved_at    TIMESTAMPTZ,
    resolved_by    UUID REFERENCES people(id),
    CONSTRAINT chk_content_plan_flags_kind CHECK (kind IN (
        'changed_after_issue', 'deleted_from_plan', 'key_erased', 'key_duplicated', 'key_unknown')),
    CONSTRAINT chk_content_plan_flags_comment CHECK (comment_state IN ('not_needed', 'pending', 'posted', 'failed'))
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_content_plan_flags_open
    ON content_plan_flags (plan_id, kind, COALESCE(issue_key, ''), COALESCE(row_number, 0))
    WHERE resolved_at IS NULL;

-- ── Jira copy for Laporan ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS jira_issues (
    key         TEXT PRIMARY KEY,
    issue_id    TEXT NOT NULL,
    issue_type  TEXT NOT NULL,
    status      TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL,
    fields      JSONB NOT NULL DEFAULT '{}'::jsonb,
    links       JSONB NOT NULL DEFAULT '[]'::jsonb,
    synced_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_jira_issues_type ON jira_issues (issue_type, status);

-- Append-only: one row per changelog item; never updated or deleted.
CREATE TABLE IF NOT EXISTS jira_issue_changes (
    id                 BIGSERIAL PRIMARY KEY,
    issue_key          TEXT NOT NULL,
    history_id         TEXT NOT NULL,
    at                 TIMESTAMPTZ NOT NULL,
    author_account_id  TEXT,
    field              TEXT NOT NULL,
    from_value         TEXT,
    to_value           TEXT,
    CONSTRAINT uq_jira_issue_changes_item UNIQUE (history_id, field, issue_key)
);
CREATE INDEX IF NOT EXISTS idx_jira_issue_changes_issue ON jira_issue_changes (issue_key, at);

CREATE TABLE IF NOT EXISTS jira_sync_state (
    id               INTEGER PRIMARY KEY DEFAULT 1,
    cursor_at        TIMESTAMPTZ,
    last_success_at  TIMESTAMPTZ,
    last_error       TEXT,
    CONSTRAINT chk_jira_sync_state_single CHECK (id = 1)
);
INSERT INTO jira_sync_state (id) VALUES (1) ON CONFLICT (id) DO NOTHING;

CREATE TABLE IF NOT EXISTS event_point_comments (
    issue_key  TEXT NOT NULL,
    judged_at  TIMESTAMPTZ NOT NULL,
    points     INTEGER NOT NULL,
    text       TEXT NOT NULL,
    state      TEXT NOT NULL DEFAULT 'pending',
    posted_at  TIMESTAMPTZ,
    error      TEXT,
    PRIMARY KEY (issue_key, judged_at),
    CONSTRAINT chk_event_point_comments_state CHECK (state IN ('pending', 'posted', 'failed'))
);

-- ── Harvest ─────────────────────────────────────────────────────────────────
-- Written by the harvest-registry Prefect flow; append-only.
CREATE TABLE IF NOT EXISTS harvest_attempts (
    id               BIGSERIAL PRIMARY KEY,
    account_id       UUID NOT NULL REFERENCES accounts(id),
    flow_run_id      TEXT,
    trigger          TEXT NOT NULL,
    window_days      INTEGER NOT NULL,
    started_at       TIMESTAMPTZ NOT NULL,
    ended_at         TIMESTAMPTZ,
    outcome          TEXT NOT NULL,
    posts_collected  INTEGER NOT NULL DEFAULT 0,
    reason           TEXT,
    CONSTRAINT chk_harvest_attempts_trigger CHECK (trigger IN ('monthly', 'manual')),
    CONSTRAINT chk_harvest_attempts_outcome CHECK (outcome IN (
        'collected', 'nothing_new', 'blocked', 'not_found', 'failed', 'skipped'))
);
CREATE INDEX IF NOT EXISTS idx_harvest_attempts_account ON harvest_attempts (account_id, started_at DESC);

CREATE TABLE IF NOT EXISTS post_review_marks (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    platform    TEXT NOT NULL,
    content_id  TEXT NOT NULL,
    mark        TEXT NOT NULL,
    source      TEXT NOT NULL DEFAULT 'manual',
    set_by      UUID REFERENCES people(id),
    set_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    cleared_by  UUID REFERENCES people(id),
    cleared_at  TIMESTAMPTZ,
    CONSTRAINT chk_post_review_marks_mark CHECK (mark IN ('iklan', 'tidak_relevan', 'bukan_konten_akun_ini')),
    CONSTRAINT chk_post_review_marks_source CHECK (source IN ('manual', 'sheet_import'))
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_post_review_marks_active
    ON post_review_marks (platform, content_id, mark) WHERE cleared_at IS NULL;

-- One-time import: the ad flags reviewers set in the Account Social Harvest sheets, which the
-- daily sheet sync mirrored into harvested_signals.advertisement.
INSERT INTO post_review_marks (platform, content_id, mark, source)
SELECT s.platform, s.content_id, 'iklan', 'sheet_import'
FROM harvested_signals s
WHERE s.advertisement
  AND NOT EXISTS (SELECT 1 FROM post_review_marks m
                  WHERE m.platform = s.platform AND m.content_id = s.content_id AND m.mark = 'iklan'
                    AND m.cleared_at IS NULL);

-- ── Sanctions ───────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS sanctions (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    person_id      UUID NOT NULL REFERENCES people(id),
    level          TEXT NOT NULL,
    with_pip       BOOLEAN NOT NULL DEFAULT false,
    source         TEXT NOT NULL,
    period         DATE,
    category_code  TEXT,
    event_keys     TEXT[] NOT NULL DEFAULT '{}',
    points         INTEGER,
    state          TEXT NOT NULL,
    hold_until     TIMESTAMPTZ,
    hold_reason    TEXT,
    held_by        UUID REFERENCES people(id),
    issued_on      DATE,
    valid_until    DATE,
    letter_number  TEXT,
    letter_state   TEXT NOT NULL DEFAULT 'not_needed',
    letter_file_id TEXT,
    note           TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by     UUID REFERENCES people(id),
    CONSTRAINT chk_sanctions_level CHECK (level IN (
        'teguran_lisan', 'sp1', 'sp2', 'sp3', 'peringatan_terakhir', 'phk_flag')),
    CONSTRAINT chk_sanctions_source CHECK (source IN ('recorded', 'ladder', 'direct')),
    CONSTRAINT chk_sanctions_state CHECK (state IN ('computed', 'held', 'issued', 'on_appeal', 'flagged')),
    CONSTRAINT chk_sanctions_hold_reason CHECK (
        state <> 'held' OR (hold_reason IS NOT NULL AND btrim(hold_reason) <> '')),
    CONSTRAINT chk_sanctions_phk_never_issued CHECK (level <> 'phk_flag' OR state = 'flagged'),
    CONSTRAINT chk_sanctions_letter_state CHECK (letter_state IN ('not_needed', 'pending', 'written', 'failed')),
    CONSTRAINT uq_sanctions_letter_number UNIQUE (letter_number)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_sanctions_ladder_month
    ON sanctions (person_id, period) WHERE source = 'ladder';
CREATE UNIQUE INDEX IF NOT EXISTS uq_sanctions_direct_event
    ON sanctions (person_id, (event_keys[1])) WHERE source = 'direct';
CREATE INDEX IF NOT EXISTS idx_sanctions_person ON sanctions (person_id, issued_on);

CREATE TABLE IF NOT EXISTS sanction_letter_counters (
    year  INTEGER PRIMARY KEY,
    last  INTEGER NOT NULL DEFAULT 0
);

INSERT INTO schema_migrations (version) VALUES ('015_hub_automation_reports')
ON CONFLICT (version) DO NOTHING;

COMMIT;
