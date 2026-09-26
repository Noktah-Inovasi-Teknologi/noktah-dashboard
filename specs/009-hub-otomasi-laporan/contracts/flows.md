# Prefect flows: spec 009

Every flow:
- uses `@flow(name=…, **alert_hooks(…))`;
- returns `{start_time, end_time, data, summary, error}` and never raises;
- supports `--validate-only` on the command line (constitution: flows that write to outside
  systems);
- reaches hub-api only through `hub.internal.call`.

Tasks calling outside APIs use `retries=2, retry_delay_seconds=30`.

| Flow (deployment) | Schedule | Parameters | Outside writes |
|---|---|---|---|
| `hub-plan-watch` | `*/15 * * * *` | `month: str\|None` (default: this and next month), `plan_ids: list\|None`, `validate_only=False` | Jira comments on changed issues |
| `hub-jira-create` | none (started by hub-api) | `batch_id: str`, `validate_only=False` | Jira bulk create (≤45 per call) with the `noktah.plan-row` property; Key cells in the plan |
| `hub-jira-sync` | `*/15 * * * *` | `validate_only=False` | Jira comments with Event points |
| `harvest-registry` | `30 17 1 * *` (Asia/Jakarta) | `account_ids: list\|None`, `trigger="monthly"`, `validate_only=False` | as `run_harvest` (Drive, harvest tables); no sheets |
| `hub-sanctions` | `5 * * * *` | `validate_only=False` | Google Docs letters in `HUB_SP_LETTER_FOLDER_ID` |

Alert channel: `eskala` for all five. `summary.failures` and `summary.items_failed` drive the
alert (G-31: one message only when rows failed).

## hub-plan-watch
1. `hub.internal.call("automation/plan-targets", {month, plan_ids})`.
2. For each target:
   - find the plan file (`google_filter_files_in_folder` + `pick_plan_file`) and read its tab
     (`Sheet1` or the first), with a 2–4 s delay between plans;
   - post `content-plans/scan`;
   - post each returned comment to Jira (`jira.issue.comment`), then report
     `content-plans/comments`.

## hub-jira-create
1. `jira-batches/claim` returns the plans and rows to create.
2. Per plan:
   - re-read the sheet; if its fingerprint differs from the claimed one, refuse the plan
     (every row `refused`, "Plan berubah setelah Greenlight");
   - build each issue with `convert_content_plan_row_to_jira_issue`, passing the Registry
     ids explicitly and adding property `noktah.plan-row`;
   - drop the `metadata` key before sending;
   - `create_issues_bulk` in chunks of 45;
   - read back each created key's property to map it to its row (R3);
   - write `=HYPERLINK(url, key)` into each row's **Key** cell;
   - post `jira-batches/result` after each plan (progress).
3. A final `result` with `done: true`.

## hub-jira-sync
1. `jira/sync-state` returns the cursor.
2. Fetch `GET /field` to map field names.
3. Search `project = ESKL AND issuetype in (Content, Event) AND updated >= cursor` (the
   first run uses `created >= 2026-01-01`), pages of 50, with `expand=changelog`, plus the
   full changelog when it is truncated.
4. `jira/ingest` per page; the last page carries `done: true` and the new cursor (the sync's
   start time minus 5 minutes).
5. Post the returned Event comments, then `jira/comments`.

## harvest-registry
1. `harvest/targets` returns the accounts.
2. For each account, one at a time:
   - `run_harvest([url], selector(days = 90 if first_harvest else 31), harvest_name=None,
     list_depth=150)`;
   - append a `harvest_attempts` row;
   - `asyncio.sleep(1800)` between accounts when there is more than one and it isn't the
     last.
3. `validate_only` lists the targets and the window each would get, and harvests nothing.

## hub-sanctions
1. `sanctions/tick` returns the letters to write (hub-api decides which working day it is).
2. Upload each letter as a Google Doc (Drive `files.create`, `mimeType`
   `application/vnd.google-apps.document`, media `text/html`) into
   `HUB_SP_LETTER_FOLDER_ID`.
3. Report `sanctions/letters`.
