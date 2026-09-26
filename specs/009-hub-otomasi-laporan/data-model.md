# Data model: spec 009 (migration `015_hub_automation_reports`)

Entities use their CONTEXT.md names. All tables are new unless noted. The migration follows
`.claude/rules/backend/schema.md`: idempotent, additive, transactional, self-recording, with
a `.down.sql`, mirrored into `init.sql`, and added to the three migration chains
(`service/api/tests/conftest.py`, `service/prefect/tests/conftest.py`,
`script/verify_schema_parity.sh`).

Writers:
- **hub-api** writes everything except `harvest_attempts`.
- **Prefect** writes `harvest_attempts`, alongside the harvest tables it already writes.

## Changes to existing tables

| Table | Change |
|---|---|
| `person_permissions` | `chk_person_permissions_permission` widened (DROP + ADD, strict superset) with `manage_automation`, `view_reports`, `view_incentive` |
| `unit_roles` | `default_permissions` of `owner` (noktah) and `brand_manager` (eskala, venyu) gain the three keys (data step, `array_append` guarded by `NOT … = ANY`) |
| `person_permissions` (data) | every Person with an active `owner` or `brand_manager` role is granted the three keys (`ON CONFLICT DO NOTHING`) |
| `people` | `started_on DATE` (nullable): "Mulai bekerja" (G-27) |

## Content Plan (Otomasi → Jira)

**`content_plans`**: the latest scan of one Client's Content Plan for one month.

| Column | Type | Notes |
|---|---|---|
| id | uuid PK | |
| client_id | uuid FK clients | |
| month | date | first day of the month |
| state | text | `found`, `missing` (no file), `ambiguous` (two files), `unreadable` (no Tanggal/Bentuk/Topik tab) |
| drive_file_id, file_name, tab_name | text | null unless found |
| rows | jsonb | `[{row_number, cells: {column: value}}]` as last read |
| fingerprint | text | R5: hash of all rows' R4 columns |
| scanned_at | timestamptz | |
| problem | text | Bahasa message for the non-`found` states |

`UNIQUE (client_id, month)`. The table is updated in place: it is a cache of the sheet, not
history. History lives in greenlights and issues.

**`content_plan_greenlights`**: append-only; the current Greenlight is the newest row.

| Column | Type | Notes |
|---|---|---|
| id | uuid PK | |
| plan_id | uuid FK content_plans | |
| fingerprint | text | the plan's fingerprint when greenlit |
| rows | jsonb | the rows as greenlit |
| person_id | uuid FK people | |
| at | timestamptz | |

**`content_plan_issues`**: which row made which issue (FR-016). Never updated except
`last_fingerprint` and `last_seen_at`.

| Column | Type | Notes |
|---|---|---|
| issue_key | text PK | |
| plan_id | uuid FK | |
| row_number | int | at creation |
| fingerprint | text | the row's R4 fingerprint at creation |
| cells | jsonb | the row's cells at creation (the "old" side of old → new) |
| batch_id | uuid FK jira_batches | |
| created_at | timestamptz | |
| last_fingerprint | text | as last seen by the watcher |
| last_seen_at | timestamptz | |

**`content_plan_flags`**: what the watcher found (FR-017, FR-020).

| Column | Type | Notes |
|---|---|---|
| id | uuid PK | |
| plan_id | uuid FK | |
| kind | text | CHECK in (`changed_after_issue`, `deleted_from_plan`, `key_erased`, `key_duplicated`, `key_unknown`) |
| issue_key | text | nullable for `key_unknown` without a match |
| row_number | int | |
| changes | jsonb | `[{column, old, new}]` for `changed_after_issue` |
| detected_at | timestamptz | |
| comment_state | text | `not_needed`, `pending`, `posted`, `failed` |
| commented_at | timestamptz | |
| resolved_at, resolved_by | timestamptz, uuid | a manager marks it handled; a later change raises a new flag |

A partial unique index on `(plan_id, kind, issue_key) WHERE resolved_at IS NULL` prevents the
same open flag twice. A `changed_after_issue` flag for a key whose fingerprint changes again
while still open has its `changes` replaced, and gets a new pending comment.

**`jira_batches`** and **`jira_batch_rows`**: one "Buat issue Jira" press (FR-014, FR-019).

| jira_batches | Type | Notes |
|---|---|---|
| id | uuid PK | |
| requested_by | uuid FK people | |
| requested_at | timestamptz | |
| plan_ids | uuid[] | |
| status | text | `queued`, `running`, `done`, `failed` |
| flow_run_id | text | |
| total, created, failed | int | |
| finished_at | timestamptz | |
| error | text | |

| jira_batch_rows | Type | Notes |
|---|---|---|
| batch_id | uuid FK | |
| plan_id | uuid FK | |
| row_number | int | |
| outcome | text | `created`, `failed`, `refused` |
| issue_key | text | |
| reason | text | Jira's words, or the Hub's refusal |

PK `(batch_id, plan_id, row_number)`.

## Jira copy (Laporan)

**`jira_issues`**: the current state of each ESKL Content and Event issue (upserted).

| Column | Type | Notes |
|---|---|---|
| key | text PK | |
| issue_id | text | |
| issue_type | text | `Content`, `Event` |
| status | text | |
| created_at, updated_at | timestamptz | |
| fields | jsonb | `{field name: value}`: user fields as `{account_id, name}`, options as their value string, cascading as `{parent, child}` |
| links | jsonb | `[{type, key}]` |
| synced_at | timestamptz | |

**`jira_issue_changes`**: append-only; one row per changelog item.

| Column | Type | Notes |
|---|---|---|
| id | bigserial PK | |
| issue_key | text | |
| history_id | text | |
| at | timestamptz | |
| author_account_id | text | |
| field | text | |
| from_value, to_value | text | display strings |

`UNIQUE (history_id, field, issue_key)`. Plain row inserts with `ON CONFLICT DO NOTHING`;
never updated or deleted.

**`jira_sync_state`**: a single row (`id = 1`) with `cursor timestamptz`,
`last_success_at timestamptz` and `last_error text`.

**`event_point_comments`**: one comment per judgement (FR-074).

| Column | Type | Notes |
|---|---|---|
| issue_key | text | |
| judged_at | timestamptz | the Judged transition's time |
| points | int | |
| text | text | |
| state | text | `pending`, `posted`, `failed` |
| posted_at | timestamptz | |

PK `(issue_key, judged_at)`.

## Harvest

**`harvest_attempts`**: written by Prefect, append-only.

| Column | Type | Notes |
|---|---|---|
| id | bigserial PK | |
| account_id | uuid FK accounts | |
| flow_run_id | text | |
| trigger | text | `monthly`, `manual` |
| window_days | int | 31 or 90 |
| started_at, ended_at | timestamptz | |
| outcome | text | CHECK in (`collected`, `nothing_new`, `blocked`, `not_found`, `failed`, `skipped`) |
| posts_collected | int | |
| reason | text | |

**`post_review_marks`**

| Column | Type | Notes |
|---|---|---|
| id | uuid PK | |
| platform | text | |
| content_id | text | |
| mark | text | CHECK in (`iklan`, `tidak_relevan`, `bukan_konten_akun_ini`) |
| source | text | `manual`, `sheet_import` |
| set_by | uuid FK people | null for the import |
| set_at | timestamptz | |
| cleared_by | uuid | |
| cleared_at | timestamptz | |

A partial unique index on `(platform, content_id, mark) WHERE cleared_at IS NULL`. The data
step imports `harvested_signals.advertisement = true` as `iklan` / `sheet_import`, guarded by
`NOT EXISTS`.

## Sanctions

**`sanctions`**

| Column | Type | Notes |
|---|---|---|
| id | uuid PK | |
| person_id | uuid FK people | |
| level | text | CHECK in (`teguran_lisan`, `sp1`, `sp2`, `sp3`, `peringatan_terakhir`, `phk_flag`) |
| with_pip | bool | 30-day PIP attached |
| source | text | `recorded` (a manager entered it), `ladder` (month calculation), `direct` (§4.3) |
| period | date | the month it answers (ladder), null otherwise |
| category_code | text | the code that drove it; for same-code teguran counts |
| event_keys | text[] | |
| points | int | the month's Violation points, negative |
| state | text | CHECK in (`computed`, `held`, `issued`, `on_appeal`, `flagged`) |
| hold_until | timestamptz | |
| hold_reason, held_by | text, uuid | `chk_sanctions_hold_reason`: `state <> 'held' OR (hold_reason IS NOT NULL AND btrim(hold_reason) <> '')` |
| issued_on | date | |
| valid_until | date | `issued_on + 90 days` |
| letter_number | text | UNIQUE |
| letter_state | text | `not_needed`, `pending`, `written`, `failed` |
| letter_file_id | text | |
| created_at | timestamptz | |
| created_by | uuid | null when computed |

- A partial unique index on `(person_id, period, source) WHERE source = 'ladder'` makes the
  month calculation idempotent.
- `sanction_letter_counters (year int PK, last int)` issues letter numbers.
- `hub_alerts_sent` (existing, `(month, kind)`) de-duplicates the working-day-2 Slack message
  (kind `sanctions_ready`).

## State transitions

- **Content Plan**: `missing` / `ambiguous` / `unreadable` / `found`; a scan moves it
  between them. Greenlit when its newest Greenlight's fingerprint equals the current one;
  "Berubah sejak disetujui" when they differ while rows still lack issues.
- **Batch**: `queued` → `running` → `done` (even with row failures) or `failed` (the flow
  itself failed).
- **Flag**: open (`resolved_at` null) → resolved. A comment goes `pending` → `posted` or
  `failed` (retried at the next scan).
- **Sanction**:
  - `computed` → `held` (a manager, with reason, before `hold_until`) or `issued` (after
    `hold_until`);
  - `computed` → `on_appeal` when any of its Events is Appealed; back to `computed`, with a
    new `hold_until`, when judged again;
  - `phk_flag` is always `flagged`, never `issued`;
  - `recorded` rows are created directly as `issued`.
