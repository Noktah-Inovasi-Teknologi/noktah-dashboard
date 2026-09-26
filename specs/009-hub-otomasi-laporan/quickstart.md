# Quickstart: validating spec 009

Nothing here writes to Jira, Drive or the live database until the step says so. Rehearse on a
clone first (`script/db_rehearsal.sh create`).

## 0. Prerequisites

- **Migration 015** on the rehearsal clone, then the live database:

  ```bash
  docker exec -i postgres psql -U "$POSTGRES_USER" -d spine_rehearsal < config/postgres/migrations/015_hub_automation_reports.sql
  script/verify_schema_parity.sh
  ```
- **`.env`**:
  - `HUB_SP_LETTER_FOLDER_ID` = the Drive folder id of Company > HR > Surat Peringatan;
  - `SLACK_MANAGERIAL_ESKALA` is already set.
- **Jira**: the verdict screen as built (G-43). Events carry no Defect Category, and Event
  Observer and Excellence Category stay multi-choice (G-50, G-51). See `jira-fields.md`.
- **`config/hub/holidays.yaml`**: check it lists the Indonesian national holidays for the
  months ahead. Working days are Monday to Friday minus these dates.
- **Rebuild and redeploy**:

  ```bash
  docker-compose up -d --build api
  docker exec prefect prefect deployment delete --name 'social-harvest-window/harvest-monthly-*'   # one by one; see list below
  docker exec prefect prefect deployment delete 'social-harvest-sync/social-harvest-sync'
  docker exec prefect prefect deploy --all
  ```

  The 22 old `harvest-monthly-*` deployments must be deleted from the Prefect server;
  removing them from `prefect.yaml` alone does not unschedule them. List them with
  `docker exec prefect prefect deployment ls | grep harvest-monthly`.

## 1. Tests (no network)

```bash
cd service/api && uv run pytest                       # points, ladder, stations, watcher, permissions, routes
cd service/prefect && ./.venv/Scripts/python.exe -m pytest tests/ -k "plan_watch or jira_create or jira_sync or harvest_registry or sanctions"
cd service/web && bun run test:ui && bun run ui:gate    # new pages on fixtures
```

## 2. Dry runs against real systems (read-only)

```bash
docker exec prefect python flows/hub_plan_watch.py --validate-only --month 2026-10
docker exec prefect python flows/hub_jira_sync.py --validate-only        # reads Jira; posts nothing
docker exec prefect python flows/harvest_registry.py --validate-only     # lists targets + windows
docker exec prefect python flows/hub_sanctions.py --validate-only        # renders letters; uploads nothing
```

Expected results:
- **Plan watch**: every active Eskala Client with a folder is listed as found, missing or
  ambiguous.
- **Jira sync**: a count of Content and Event issues since 2026-01-01.
- **Harvest**: exactly the Registry's active own and competitor accounts, with 90 days for
  the new ones (SC-005).

## 3. End to end, one Client, one month (writes)

1. In the Hub, open **Otomasi → Jira**, pick a test month and one Client whose plan is
   ready. Check the quota, the blockers and the warnings, then press **Greenlight**.
2. Select the plan and press **Buat issue Jira**. Watch the progress.
3. Expected:
   - one issue per row;
   - each row's **Key** is a link to its issue;
   - **TicketID** is untouched;
   - pressing again creates nothing (SC-001).
4. Edit a keyed row's Tanggal in the sheet. Within 15 minutes the plan shows "berubah
   setelah issue dibuat", and the issue has a comment listing the change. Its fields are
   unchanged (SC-003).

## 4. Reports

- **Laporan → Delivery**, September 2026: compare Published, Late and cancelled per Client
  with JQL `project = ESKL AND issuetype = Content AND "Publication date[Date]" >= 2026-09-01
  AND "Publication date[Date]" <= 2026-09-30` (SC-006).
- **Laporan → Stations**: pick three issues and check their station times and returns by
  hand against their Jira history.
- **Laporan → Insentif**: judge a test Event with each combination of v2.1 §4.1.1. Expected:
  0 / −10 / −20 / −30 / −60, one comment each (SC-007).

## 5. Sanctions (in a rehearsal month)

With `now` overridden (`POST /internal/sanctions/tick {"now": "…"}` on the rehearsal clone):
- **Working day 2, 08:00**: computed sanctions, one Slack message.
- **Working day 3, before its end**: hold one sanction with a reason.
- **The day after**: the unheld ones are issued with letter numbers, and the letters appear
  in the folder with blank signature lines. A person with "proses PHK" is flagged, with no
  letter (SC-009, SC-010).
