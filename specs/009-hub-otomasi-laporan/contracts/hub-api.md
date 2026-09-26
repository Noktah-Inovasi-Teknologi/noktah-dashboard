# hub-api contract: spec 009

This extends `specs/008-hub-registry-client-card/contracts/hub-api.md`. Errors use the
existing envelope `{error, message}`. `/v1` routes need a signed-in Manager. Every route below
is **Eskala only**; a request for another Noktah Brand's Client is 404 (G-39).

| Gate | Meaning |
|---|---|
| `manage_automation` | "Kelola otomasi" |
| `view_reports` | "Lihat laporan" |
| `view_incentive` | "Lihat poin insentif" |

## /v1/me (changed)

`can` gains `manage_automation`, `view_reports` and `view_incentive` (booleans, checked
against brand `eskala`).

## Otomasi

### `GET /v1/automations` (manage_automation)

The cards (FR-040):

```json
{"automations": [
  {"key": "jira", "label": "Jira", "deployments": ["hub-plan-watch", "hub-jira-create", "hub-jira-sync"],
   "last_run": {"name": "…", "state": "COMPLETED", "started_at": "…", "ended_at": "…", "failures": 0} | null,
   "next_run_at": "…" | null,
   "history": [{"started_at": "…", "state": "COMPLETED", "deployment": "hub-plan-watch"}]},
  {"key": "harvest", "label": "Harvest", "deployments": ["harvest-registry"], "…": "…"}
]}
```

- `history` covers 90 days.
- If Prefect can't be reached, `last_run` is null and `unavailable: true` is set; the call
  never fails.

### `GET /v1/content-plans?month=YYYY-MM` (manage_automation)

One entry per active Eskala Client:

```json
{"month": "2026-10", "scanned_at": "…", "plans": [
  {"id": "…", "client": {"id": "…", "name": "Pelita Delapan"}, "state": "found|missing|ambiguous|unreadable",
   "file_name": "…", "file_url": "…", "problem": null,
   "rows": 12, "issues": 0, "status": "found|greenlit|changed_since_greenlight|issues_created|changed_after_issues|partly_created",
   "greenlit": {"by": "Defila", "at": "…"} | null, "open_flags": 0}
]}
```

### `GET /v1/content-plans/{id}` (manage_automation)

```json
{"id": "…", "client": {…}, "month": "2026-10", "state": "found", "status": "…",
 "quota": {"Post": {"planned": 4, "quota": 4}, "Story": {…}, "Short Video": {…}},
 "blocking": [{"code": "no_field_associate", "message": "Tim klien belum punya Field Associate."}],
 "warnings": [{"code": "quota_mismatch|date_outside_month", "message": "…", "row_number": 5}],
 "rows": [{"row_number": 2, "tanggal": "04/10/2026", "bentuk": "Story", "topik": "…", "issue_key": null, "flags": []}],
 "flags": [{"id": "…", "kind": "changed_after_issue", "issue_key": "ESKL-1", "row_number": 6,
            "changes": [{"column": "Tanggal", "old": "18/10/2026", "new": "22/10/2026"}],
            "detected_at": "…", "comment_state": "posted"}],
 "greenlights": [{"by": "…", "at": "…"}],
 "can_greenlight": true, "can_create": false}
```

Blocking codes:

| Code | When |
|---|---|
| `no_field_associate` | the Client's Team has no Field Associate |
| `no_content_editor` | the Client's Team has no Content Editor |
| `no_jira_account` | the Field Associate or Content Editor has no Jira account on their Person |
| `no_component` | the Client has no Jira component |
| `row_missing_fields` | a row lacks its date, Topik or Bentuk; `row_number` is set |
| `plan_not_found` | no plan file for the month |
| `open_key_flag` | a key-erased or key-duplicated flag is open |

### `POST /v1/content-plans/{id}/rescan` (manage_automation)

Starts `hub-plan-watch` for this plan: `{flow_run_id}` → 202.

### `POST /v1/content-plans/scan-month?month=YYYY-MM` (manage_automation)

"Periksa sekarang": starts `hub-plan-watch` for every plan of that month → 202 `{flow_run_id}`.

### `POST /v1/content-plans/{id}/greenlight` (manage_automation)

- Body: `{"fingerprint": "…"}`, the fingerprint the manager saw.
- 409 `conflict` if the plan has changed since; 422 `invalid` if a blocking problem exists.
- Returns the plan.

### `POST /v1/content-plans/flags/{flag_id}/resolve` (manage_automation)

Marks a flag handled.

### `POST /v1/jira-batches` (manage_automation)

- Body: `{"plan_ids": ["…"]}`. Each plan must be greenlit and unchanged (FR-012, FR-013),
  otherwise 422 with the offending plans.
- Creates the batch and starts `hub-jira-create` with `{batch_id}`.
- Returns 202 `{"batch": {id, status}}`.

### `GET /v1/jira-batches/{id}` (manage_automation)

`{id, status, total, created, failed, requested_by, requested_at, finished_at, rows: [{plan, client, row_number, outcome, issue_key, reason}]}`.
The page polls it for progress.

### `GET /v1/jira-batches?limit=20` (manage_automation)

Recent batches.

### `GET /v1/harvest/accounts` (manage_automation)

```json
{"accounts": [{"id": "…", "platform": "instagram", "handle": "lasikasyik", "url": "…",
  "client": {"id": "…", "name": "…"}, "role": "own|competitor",
  "last_harvested_at": "…" | null, "last_outcome": "collected|nothing_new|blocked|not_found|failed|skipped" | null,
  "last_reason": null, "posts_collected": 12, "status": "ok|blocked|not_found|never", "first_harvest": false}]}
```

An account shared by several Clients appears once per Client.

### `POST /v1/harvest/accounts/{account_id}/run` (manage_automation)

Starts `harvest-registry` with `{account_ids: [id], trigger: "manual"}` → 202
`{flow_run_id}`.

### `GET /v1/harvest/accounts/{account_id}/posts?month=YYYY-MM` (manage_automation)

`{account: {…}, month, posts: [{platform, content_id, url, published_at, content_type, caption, views, likes, comments, shares, marks: ["iklan"]}]}`.

### `PUT /v1/harvest/posts/{platform}/{content_id}/marks` (manage_automation)

- Body: `{"marks": ["iklan", "tidak_relevan"]}`, the full new set.
- Clears removed marks, adds new ones, and keeps `harvested_signals.advertisement` equal to
  "has `iklan`".
- Returns `{marks}`.

## Laporan (view_reports unless stated)

Every response carries `refreshed_at`, the last successful Jira sync (FR-050).

### `GET /v1/reports/delivery?month=YYYY-MM`

```json
{"month": "2026-09", "refreshed_at": "…", "clients": [
  {"client": {…}, "planned": 12, "created": 12, "published": 9, "late": 2, "published_late": 1, "cancelled": 1,
   "late_items": [{"key": "ESKL-11147", "topik": "…", "publication_date": "2026-09-05", "status": "Footages in Progress", "station": "B", "days_late": 21}]}
], "totals": {…}}
```

### `GET /v1/reports/stations?month=YYYY-MM`

```json
{"month": "…", "refreshed_at": "…",
 "stations": [{"station": "C", "label": "Editing", "role": "Content Editor", "in": 40, "out": 36,
   "returns": {"total": 5, "by_origin": {"C": 3, "tanpa_kategori": 2}}, "held_hours": {"median": 18.5, "longest": 96.0},
   "waiting": [{"key": "…", "client": "…", "since": "…", "hours": 30.0}]}],
 "people": [{"person": {"id": "…", "name": "…"} | {"name": "tidak terdaftar", "account_id": "…"}, "station": "C",
   "in": 20, "out": 18, "returns_against": 2, "held_hours": {…}, "waiting": 1}],
 "teams": [{"client": {…}, "first_pass": {"rate": 0.67, "counted": 9, "target": 0.6}, "qa_rounds": {"average": 1.3, "counted": 10, "target": 1.5},
   "both_met": true, "returns": 4}],
 "round_flags": [{"key": "…", "client": "…", "stations": ["C", "D"], "rounds": 4}],
 "returns": [{"key": "…", "at": "…", "from_status": "…", "to_status": "…", "origin": "C" | null, "defect": "C2 …" | null, "reason": "…" | null}],
 "unknown_statuses": ["…"]}
```

### `GET /v1/reports/performance?month=YYYY-MM&client_id=…`

- Without `client_id`: a list of Clients with their summary line.
- With it:

```json
{"client": {…}, "month": "…", "own": {"accounts": [{…}], "posts": 12, "posts_by_type": {"video": 6, "image": 3, "carousel": 3},
   "views": {"total": 1000, "average": 166.7, "n": 6}, "likes": {…}, "comments": {…}, "shares": {…},
   "engagement_rate_views": {"value": 0.05, "n": 6}, "engagement_rate_followers": {"value": null, "unavailable": "Jumlah pengikut Instagram tidak tersedia bulan ini"},
   "top_posts": [{…}], "marked": [{…}], "change": {"views_total": 0.12}},
 "competitors": {"accounts": ["…"], "average": {…}, "n_accounts": 3} | null}
```

### `GET /v1/reports/incentive?month=YYYY-MM` (view_incentive)

```json
{"month": "…", "refreshed_at": "…", "levels": {"sp1": "SP1", …}, "people": [
  {"person": {…}, "violation_points": -30, "excellence_points": 3,
   "sanctions": [{"id", "level", "level_label", "with_pip", "source", "state", "period", "category_code", "event_keys",
                  "points", "hold_until", "hold_reason", "issued_on", "valid_until", "letter_number", "letter_state",
                  "letter_url", "note"}],
   "sanction": <the newest of `sanctions`> | null,
   "team_reward": {"qualifies": true, "teams_met": 4, "teams": 6},
   "events": [{"key": "ESKL-11200", "type": "Violation", "category": "C2 …", "points": -30, "state": "counted", "reason": null}]}],
 "incomplete": [{"key": "…", "summary": "…", "person": "…", "missing": ["Violation Judgment"]}],
 "unmatched": [{"key": "…", "account_id": "…"}],
 "direct_sanctions_manual": [{"key": "…", "person": "…", "summary": "…"}]}
```

Each event: `{key, summary, type, category, points, state, reason, missing, formula, direct_item, status, created_at, judged_at}`,
`state` ∈ `counted | zero | belum_lengkap | reference | adaptation | on_hold | not_judged`.

### `GET /v1/sanctions?person_id=…` (view_incentive)

The sanction history.

### `POST /v1/sanctions` (view_incentive)

- Records a teguran lisan or SP issued outside the Hub (FR-079).
- Body: `{person_id, level, issued_on, category_code, note}`.
- It becomes `source=recorded`, `state=issued`.

### `POST /v1/sanctions/{id}/hold` (view_incentive)

- Body: `{"reason": "…"}` (required, non-empty).
- 409 if the hold window has passed or the state isn't `computed`.

### `POST /v1/sanctions/{id}/release` (view_incentive)

Undoes a hold before `hold_until` (the sanction goes back to `computed`).

### `PATCH /v1/people/{id}` (changed)

Accepts `started_on` (date or null), with the existing `manage_people` rule.

## /internal (Prefect only; `X-Hub-Internal-Token`)

All are POST, bodies JSON, and idempotent.

| Route | Called by | Body → response |
|---|---|---|
| `automation/plan-targets` | hub-plan-watch | `{month?, plan_ids?}` → `{targets: [{client_id, client_name, month, folder_id, plan_label}]}`: active Eskala Clients with a Content Plan folder |
| `content-plans/scan` | hub-plan-watch | `{client_id, month, state, drive_file_id?, file_name?, tab_name?, rows?, problem?}` → `{plan_id, comments: [{flag_id, issue_key, text}]}` (runs the watcher, R4) |
| `content-plans/comments` | hub-plan-watch | `{results: [{flag_id, ok, error?}]}` → `{}` |
| `jira-batches/claim` | hub-jira-create | `{batch_id}` → `{batch_id, plans: [{plan_id, client_id, client_name, drive_file_id, tab_name, fingerprint, component_id, field_associate_account, content_editor_account, reporter_account, rows_to_create: [{row_number, cells}]}]}`; refuses a plan changed since its Greenlight |
| `jira-batches/result` | hub-jira-create | `{batch_id, rows: [{plan_id, row_number, outcome, issue_key?, reason?, fingerprint?, cells?}], done: bool, error?}` → `{}` |
| `jira/ingest` | hub-jira-sync | `{issues: [{key, id, type, status, created, updated, fields, links, changes: [{history_id, at, author, field, from, to}]}], cursor?, done}` → `{comments: [{issue_key, judged_at, text}]}` (event point comments to post) |
| `jira/comments` | hub-jira-sync | `{results: [{issue_key, judged_at, ok, error?}]}` → `{}` |
| `jira/sync-state` | hub-jira-sync | `{}` → `{cursor, first_run}` |
| `harvest/targets` | harvest-registry | `{account_ids?}` → `{targets: [{account_id, platform, handle, url, clients: ["…"], first_harvest}]}` |
| `sanctions/tick` | hub-sanctions | `{now?}` → `{computed: n, issued: n, letters: [{sanction_id, file_name, html}]}` |
| `sanctions/letters` | hub-sanctions | `{results: [{sanction_id, ok, file_id?, error?}]}` → `{}` |

`harvest_attempts` rows are written by the flow itself (R12), not through /internal.
