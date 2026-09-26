# Research: Noktah Hub, Otomasi & Laporan (spec 009)

Each decision: what was chosen, why, what else was considered. Facts are from the codebase
survey of 2026-09-26 (hub-api, service/web, service/prefect) and the grill ledger.

## R1. Who does what: hub-api holds state, Prefect does the outside writes

- **Decision**: hub-api stores every piece of state the Hub shows and every decision a manager
  makes (Greenlights, batches, review marks, sanctions), and does the deterministic
  computing for the reports and points. Prefect flows do every write to an outside system
  (Jira issues and comments, the Content Plan's Key cells, Google Docs letters) and every
  scheduled read of one (Drive/Sheets plan scans, the Jira sync, the harvest). Flows reach
  hub-api only through `/internal/*` (`hub.internal.call`), as the existing `hub-*` flows do.
- **Rationale**: Constitution I (automation logic in Prefect flows, which never raise),
  CLAUDE.md ("hub-api is the only writer of company data"), and the existing split (hub-api
  has no Jira credentials; Prefect has Jira, Drive and roach). Retries, alerts
  (`alert_hooks`) and `--validate-only` all come for free on the Prefect side.
- **Alternatives**: hub-api calling Jira directly for batch creation, rejected because it
  duplicates the ADF/sanitize code in `tasks/jira_adf.py` and gives hub-api Jira
  credentials, retries and long-running requests it has never needed.

## R2. How hub-api starts a flow run

- **Decision**: hub-api starts deployments through the Prefect REST API it already reaches
  (`PREFECT_API_URL`, used today to pause roster-sync). It reads the deployment by name
  (`GET /deployments/name/{flow}/{deployment}`), then creates a run
  (`POST /deployments/{id}/create_flow_run` with `parameters`). A new `app/prefect_api.py`
  wraps this, plus a read of recent and next flow runs for the Otomasi cards
  (`POST /flow_runs/filter`).
- **Rationale**: this needs no new service or credential; the call is on the Docker
  network only.
- **Alternatives**: a work queue table polled by a Prefect flow every minute, rejected: it
  adds a minute of latency to every button and a poller that runs all day.

## R3. Mapping created Jira issues back to plan rows

- **Decision**: every issue in a bulk create carries an issue **entity property**
  `noktah.plan-row` = `{plan_id, row_number, batch_id}` (Jira's `IssueUpdateDetails.properties`,
  accepted by the bulk endpoint). After the bulk call, the flow reads each created issue's
  property (`GET /issue/{key}/properties/noktah.plan-row`) and maps key → row from it,
  never from response order. Batches stay ≤ 45 issues (constitution II).
- **Rationale**: the survey found that bulk create returns created keys without the row they
  came from; only failures carry `failedElementNumber`. The response order is not
  guaranteed, and SC-001 (zero duplicates) and FR-016 (key into the right row) depend on
  this mapping being exact.
- **Alternatives**:
  - creating issues one at a time, rejected under constitution II ("Bulk Jira creation
    MUST use the REST bulk endpoint");
  - matching by summary and date, rejected because two rows can share a Topik;
  - a visible label, rejected because it clutters the board.

## R4. Row identity and the Key column

- **Decision**: a row is identified by its **Key** cell once it has an issue, and by its
  sheet row number before that. The Hub records, per created issue, the plan, the row
  number, the key and a fingerprint of the row's content (`content_plan_issues`). The flow
  writes the key into **Key** as `=HYPERLINK(url, key)` (USER_ENTERED). **TicketID** is
  never written (G-12).
- **Watcher rules** (every 15 minutes, per plan with issues):
  - a Key cell whose key the Hub made → compare the row's fingerprint; a change flags
    "berubah setelah issue dibuat" with old → new per column, and one Jira comment;
  - a key the Hub made that no row carries → the row with that key's recorded content is
    looked up; if found without a Key it is "key erased", else "dihapus dari plan";
  - the same key on two rows → "key duplicated";
  - a Key not made by the Hub → "key unknown" (flagged, never acted on);
  - a row without a Key → waiting for the next "Buat issue Jira" (no flag).

  Creation refuses any plan with an open key-erased or key-duplicated flag, so an erased key
  can never produce a second issue (G-12).
- **Fingerprint**: the columns the issue is built from (`Tanggal`, `Waktu`, `Bentuk`, `Topik`,
  `Creator`, `Format`, `Purpose/Theme`, `Strategic Application`, `Kebutuhan Personil`,
  `Known Facts`, `Shoot Guide`, `Visualisasi Konten`, `Asset`, `Caption`, `Link Referensi`),
  whitespace-normalised. `Approval`, `Keterangan`, `Key`, `TicketID` and `No.` are left out,
  so writing the Key never looks like a change.

## R5. Greenlight staleness

- **Decision**: a Greenlight stores the plan's fingerprint (all rows, R4 columns) at the
  moment it was given. The plan is "Berubah sejak disetujui" when the latest scan's
  fingerprint differs and some rows still lack issues. The scan runs every 15 minutes, and
  opening a plan in the Hub or pressing "Buat issue Jira" first runs a fresh scan of that
  plan (the create flow re-scans before creating and refuses a changed plan).
- **Rationale**: FR-013 and SC-001 hold even if the sheet changed a minute ago.

## R6. The Jira sync for Laporan

- **Decision**: flow `hub-jira-sync` (every 15 minutes) reads ESKL Content and Event issues
  updated since the last cursor. The first run covers `created >= 2026-01-01`. It uses
  `POST /rest/api/3/search/jql` with `expand=changelog,names`, and
  `GET /issue/{key}/changelog` pages when a history is longer than one page. It posts
  them to hub-api in chunks of 50.
- **Storage**: hub-api upserts `jira_issues` (current fields, **keyed by field name**, so new
  fields like "Violation Judgment" need no id) and appends `jira_issue_changes` (one row per
  changelog item, unique on history id + field; never updated).
- **Rationale**: FR-050, G-16 and G-40. Names instead of ids survive the Jira screen changes
  the user is still making (G-43).
- **Alternatives**: webhooks, rejected because hub-api has no public endpoint for Jira to
  call, and polling every 15 minutes meets G-40.

## R7. Stations, returns and rounds

- **Decision**: a status table in code (`app/reports/stations.py`) orders the Content
  workflow:

  | Order | Status | Station |
  |---|---|---|
  | 1 | Plan | A |
  | 2 | Footages in Progress | B |
  | 3 | Footage Taken | C |
  | 4 | Designs in Progress | C |
  | 5 | Designs in Review | D |
  | 6 | Internally Reviewed | E |
  | 7 | In Review by Client | E |
  | 8 | Reviewed by Client | E |
  | 9 | Scheduled | E |
  | 10 | Published, Need Review | D |
  | 11 | Done | – |

  Shelved is terminal and counts as cancelled. Old names are aliases: *Reviewed* → 6,
  *Published & Reviewed* → 10. An unknown status is reported as "status tidak dikenal",
  never guessed.
- **Return**: a transition to a lower order (Shelved excepted). Its origin station is the
  letter of the Defect Category set in the same changelog entry; with none, it is
  "tanpa kategori" under the sending station (G-35).
- **Rounds between two stations**: returns per content per unordered station pair;
  **more than 3** is flagged (G-21).
- **QA round**: each entry into *Designs in Review* (G-36).
- **Client first pass**: content that entered *In Review by Client* once and reached order
  ≥ 8 with no return from order 7/8 (G-36).
- **Month**: content belongs to the month of its publication date (customfield "Publication
  date").

## R8. People at stations

- **Decision**: B, C and E use the issue's **Field Associate** / **Content Editor** account
  ids, matched through `people.jira_account_id`. A and D use `client_team_assignments`
  (`content_planner`; `quality_assurance`, or the legacy `qc`) valid at the time of the
  transition. An account id with no Person is shown as "tidak terdaftar" (G-19).

## R9. Points (pure function, `app/incentive/points.py`)

- **Decision**:

  ```
  compute(event) -> {state: 'counted'|'zero'|'belum_lengkap'|'reference'|'adaptation'|'on_hold', points, reason}
  ```

  following G-26, G-27, G-34, G-43, G-45 and G-47:
  - Violation/Self-report: Judgment ≠ Kelalaian → 0. Otherwise the Event needs Observer
    (exactly one), Own Mistake, Problem Solved and Known by the Person. P3 is ×0 if Own
    Mistake = Yes and Problem Solved = Yes; ×2 if Known = Yes; else ×1. Points =
    −10 × P2 × P3.
  - Excellence: exactly one Excellence Category; X1, X2, X3, X8 → +1; X4–X7 → +3.
  - The Person field (or else the Assignee) must match a Hub Person.
  - Judged before 2026-09-27 → reference. Created in the Person's first 30 days → adaptation
    (0), unless the category is H2. Status Appealed → on hold.
- **Month**: the month the Event was created (v2.1 §6.7: ticket time is the reporting time).
- **Comment**: one per (issue, judgement timestamp). A new judgement after an appeal gets
  its own comment.

## R10. Sanctions (ADR-0001)

- **Decision**: the ladder is a pure function (`app/incentive/ladder.py`) over a person's
  month points, the recorded or issued sanctions still valid (90 days), and same-code
  teguran counts in the last 90 days. The heaviest row wins.
- **Schedule**: an hourly flow `hub-sanctions` calls `/internal/sanctions/tick`, which is
  idempotent:
  - on working day 2 (≥ 08:00 WIB) it computes last month's sanctions once and posts one
    Slack message to Eskala's managerial channel (`SLACK_MANAGERIAL_ESKALA`, via
    `noktah_brands.slack_managerial_env`);
  - after working day 3 ends it issues every sanction not held and not on appeal, assigns
    letter numbers (`{urut}/SP/ESK/{roman}/{year}`, sequence per year) and returns the
    letters to write.
- **Direct sanctions**: computed as soon as a judged H2 Event carries its item, with a hold
  deadline at the end of the next working day.
- **PIP**: recorded with the SP. "Proses PHK" is a flag, never issued.
- **Working days**: Mon–Fri minus the dates in `config/hub/holidays.yaml` (maintained by
  hand; see quickstart).

## R11. SP letters

- **Decision**: hub-api renders the letter (template in `config/hub/sp_letter.html`, filled
  from `sp-letter-template.md`) to HTML. The `hub-sanctions` flow uploads it to Drive as a
  Google Doc (Drive `files.create` with conversion) in the folder `HUB_SP_LETTER_FOLDER_ID`
  (Company > HR > Surat Peringatan), then reports the file id back. Nothing is sent to the
  employee.
- **Rationale**: Prefect already holds the Drive write credentials; hub-api's Google scope
  is Sheets plus Docs read-only.

## R12. Harvest from the Registry

- **Decision**: one flow, `harvest-registry(account_ids=None, days=None, validate_only=False)`.
  - It asks hub-api (`/internal/harvest/targets`) for the active Eskala Clients' own and
    competitor accounts, each with a `first_harvest` flag, then calls the existing
    `run_harvest` for one account at a time.
  - Each account gets 31 days, or 90 on a first harvest, and waits **30 minutes** between
    accounts (none after the last).
  - Each account's outcome is appended to `harvest_attempts` (written by Prefect, like
    `runs` and `harvested_signals`).
- **Deployments**: `harvest-registry` runs monthly on the 1st at 17:30 WIB. "Jalankan
  sekarang" starts the same deployment with `account_ids=[id]` (no spacing for one account).
- **Changes**: the 22 `harvest-monthly-*` deployments are removed from `prefect.yaml` and
  must be deleted from the Prefect server once (quickstart).
- **First harvest**: no successful `harvest_attempts` row and no `harvested_signals` row for
  the account.

## R13. Review marks, and retiring the harvest sheets

- **Decision**: `post_review_marks` holds one row per (post, mark) with set/cleared history.
  hub-api writes it, and keeps `harvested_signals.advertisement` equal to "has an active
  Iklan mark" so every existing reader stays correct.
- **Songbird**: `songbird.signal.top-performers` additionally excludes posts with any active
  mark (G-17); this is the only songbird change.
- **One-time import**: migration 015 creates an Iklan mark (source `sheet_import`) for every
  `harvested_signals.advertisement = true`, which the daily sheet sync already mirrors from
  the Account Social Harvest sheets.
- **Retiring the sheets**:
  - `social-harvest-sync` is removed from `prefect.yaml` (otherwise it would overwrite the
    Hub's marks from the sheets);
  - `run_harvest` stops appending to the account and detail sheets;
  - Drive delivery of media is unchanged.

## R14. Permissions

- **Decision**: three stored permission keys, `manage_automation`, `view_reports` and
  `view_incentive`. Migration 015 widens `chk_person_permissions_permission` (DROP + ADD
  superset) and adds them to `unit_roles.default_permissions` (Owner and Brand Managers get
  all three).
  - Everyone who currently holds the Owner role or an active Brand Manager role is granted
    them, since stored rows decide access.
  - Production Managers get none by default but can be ticked (G-18).
  - The actions are checked against brand `eskala` (G-39).
- `/v1/me` gains `can.manage_automation`, `can.view_reports` and `can.view_incentive`.

## R15. Constitution amendment

- **Decision**: MINOR amendment 1.1.0 → **1.2.0** to Data Management: the Hub Registry is the
  source of clients, teams, social accounts, Jira components and Content Plan folders for
  the Jira automation and the harvest; `hashmap.py` remains the source only for songbird
  until DEFERRED A-1 closes. Made in this feature's PR (T-early), before the flows switch.

## R16. Performance report and evidence discipline

- **Decision**: per Client per month:
  - own accounts vs the competitors' average, computed from `harvested_signals` by
    `published_at` month, excluding marked posts;
  - follower counts from `account_follower_observations`, taking the latest in the month;
    a missing count says "tidak tersedia" (constitution VI);
  - every average shows the number of posts it rests on (constitution VIII);
  - the competitors' average is the mean of per-account figures, and names its accounts.
