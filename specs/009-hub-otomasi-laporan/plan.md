# Implementation Plan: Noktah Hub: Otomasi & Laporan

**Branch**: `feat/project/Hub-automations-and-reports` | **Date**: 2026-09-26 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/009-hub-otomasi-laporan/spec.md` (grill ledger: [grill.md](grill.md))

## Summary

Two Hub menus for Eskala:
- **Otomasi**: Greenlight Content Plans, create their Jira issues in batches with the key
  written back and never a duplicate, watch plans for changes, run the Harvest from the
  Registry, and review harvested posts.
- **Laporan**: delivery, stations and people, account performance, and Incentive Framework
  v2.1 points and automatic sanctions with SP letters.

The work splits along the repo's existing lines (research R1):
- **hub-api** keeps state and computes the reports, points and ladder deterministically.
- **Prefect** flows do every outside read or write (Drive and Sheets scans, Jira
  create/comment/sync, harvest, Google Docs letters) and talk to hub-api through `/internal`.
- **hub-api** starts flow runs through the Prefect API.
- **The web app** adds two pages and their sub-pages.

## Technical Context

**Language/Version**:
- Python 3.14 (hub-api on FastAPI; Prefect 3.6.9 flows);
- TypeScript (Nuxt 4 + Nuxt UI, Bun).

**Primary Dependencies**:
- hub-api: asyncpg, httpx (Prefect API), google-api-python-client (existing);
- Prefect: atlassian/requests for Jira (existing `tasks/jira_tasks.py`), Google tasks,
  `run_harvest`.

**Storage**: PostgreSQL 15 (`noktah_dashboard`), migration `015_hub_automation_reports` (see
[data-model.md](data-model.md)).

**Testing**:
- pytest (hub-api; real-Postgres tests marked `schema`);
- pytest (Prefect; mocked outside calls);
- bun test + the Playwright UI sweep on fixtures.

**Target Platform**:
- Docker on the office Windows PC (api, prefect, prefect-worker);
- Cloudflare Worker (web).

**Project Type**: web service + scheduled workers + web front end (existing monorepo
services).

**Performance Goals**:
- Plan watch and Jira sync each finish inside their 15-minute interval.
- Report endpoints answer in under 2 s for one month (~200 Content issues, ~20 Clients).
- A 60-row batch completes in under 5 minutes.

**Constraints**:
- Jira bulk ≤ 45 per call.
- Random 2–4 s delays between sheet reads.
- The Harvest spaces accounts 30 minutes apart.
- No issue field is changed after creation.
- Nothing is issued as PHK.
- Everything that writes outside supports `--validate-only`.

**Scale/Scope**:
- ~20 Eskala Clients, ~26 harvested accounts, ~1,500 Content issues and a few hundred
  Events per year;
- 6 new pages, ~20 endpoints, 5 flows, 1 migration.

## Constitution Check

*GATE: checked before Phase 0 and again after Phase 1.*

| Principle | How this plan complies |
|---|---|
| I. Prefect orchestration | Every automation is a Prefect flow (5 new) returning the standard dict, never raising, runnable via `docker exec prefect python flows/<name>.py`. hub-api only schedules nothing; it starts deployments on a manager's press and holds state (the same split as the existing `hub-*` flows). |
| II. External API standards | Jira bulk create ≤ 45 per call. Tasks calling Jira and Google use `retries=2, retry_delay_seconds=30`. Random delays between sheet reads. No model calls (XI not engaged). |
| III. Docker-first | No new service; new env var `HUB_SP_LETTER_FOLDER_ID` for prefect/worker. |
| IV. Credentials | No secrets in code; the Jira token stays in Prefect; hub-api gains none. |
| V. Observability | Batch rows, flags, harvest attempts and letters each carry a classified outcome and reason; `alert_hooks("eskala")` on all five flows; `summary.failures` drives alerts. |
| VI. Data honesty | Missing follower count → explicit "tidak tersedia"; Events missing a field → "belum lengkap", never guessed; unknown Jira status → reported, not mapped. |
| VII. Append-only observations | `jira_issue_changes`, `harvest_attempts`, greenlights, review-mark history and sanctions are append-only or state-machined; the plan scan cache is not an observation. |
| VIII. Evidence discipline | Averages in the performance report show their post count; the team thresholds show how many contents they rest on. No effect estimates are produced. |
| IX. Identity chain | Plan row → issue key is recorded exactly (R3, R4), never inferred by order. |
| X. Polite collection | The Harvest keeps `run_harvest`'s pacing, backoff and blocked-profile stop; spacing between accounts is unchanged (30 minutes). |
| XI. Model governance | Not engaged: no AI in this feature. |
| Data Management (`hashmap.py` is the source) | **Amendment required** (R15): v1.2.0 names the Registry as the source for the Jira automation and the Harvest. Done as the first task, before any flow switches. |
| Validate-only | All five flows take `--validate-only`. |
| DEFERRED register | The spec's Inherited & Deferred section accounts for A-1 (claimed for Jira and Harvest, narrowed to songbird), H-2 (re-deferred), S-1 (resolved) and H-3 (new). |

**Result**: passes, with the one amendment done as a task. Re-checked after design: still
passes.

## Project Structure

### Documentation (this feature)

```text
specs/009-hub-otomasi-laporan/
├── brief.md  grill.md  spec.md  plan.md  research.md  data-model.md  quickstart.md
├── jira-fields.md  sp-letter-template.md
├── contracts/hub-api.md  contracts/flows.md
├── checklists/requirements.md
└── tasks.md
```

### Source Code

```text
config/postgres/migrations/015_hub_automation_reports{,.down}.sql   config/postgres/init.sql
config/hub/holidays.yaml   config/hub/sp_letter.html
.specify/memory/constitution.md                                     (v1.2.0)
service/api/app/
├── permissions.py  people/routes.py (+ can flags, started_on)  people/service.py
├── prefect_api.py                  # start deployments, read flow runs
├── workdays.py                     # working days + holidays
├── automation/                     # content plans, batches, harvest accounts, review marks
│   ├── plans.py  watcher.py  batches.py  harvest.py  marks.py  routes.py  internal.py
├── reports/                        # stations table, delivery, stations, performance
│   ├── stations.py  jira_store.py  delivery.py  station_report.py  performance.py  routes.py
├── incentive/                      # points, ladder, sanctions, letters
│   ├── points.py  ladder.py  sanctions.py  letters.py  routes.py
└── internal.py (+ includes the new internal routers)
service/api/tests/test_{automation_permissions,plan_fingerprint,watcher,batches,harvest_targets,marks,stations,delivery,performance,points,ladder,sanctions,workdays}.py
service/prefect/
├── flows/hub_plan_watch.py  hub_jira_create.py  hub_jira_sync.py  harvest_registry.py  hub_sanctions.py
├── tasks/jira_tasks.py (+ comment, get property, search, fields)  tasks/google_tasks.py (+ write cells, upload doc)
├── tasks/utility_tasks.py (converter takes explicit ids)  tasks/harvest_attempt_tasks.py  tasks/songbird_tasks.py (exclude marks)
├── flows/common/social_harvest.py (no sheet writes)
├── prefect.yaml (−22 harvest-monthly, −social-harvest-sync, +5 deployments)
└── tests/test_{hub_plan_watch,hub_jira_create,hub_jira_sync,harvest_registry,hub_sanctions}.py
service/web/app/
├── pages/automations/index.vue  automations/plans/[id].vue  automations/accounts/[id].vue
├── pages/reports/index.vue      reports/incentive.vue
├── layouts/default.vue (nav)  composables/useMe.ts (can)  types/hub.ts
service/web/server/fixtures/index.ts   service/web/e2e/ui-sweep.e2e.ts   service/web/README.md
docker-compose.yml (HUB_SP_LETTER_FOLDER_ID)   .claude/CLAUDE.md   service/api/README.md
```

**Structure Decision**: the existing services, extended; no new service or container. The
harvest sheet retirement and the removed deployments are part of the same change.

## Complexity Tracking

| Item | Why | Simpler alternative rejected |
|---|---|---|
| Entity property per issue + read-back (R3) | Bulk create doesn't map keys to rows; FR-016/SC-001 need exactness | One-by-one creation breaks constitution II; order-mapping is not guaranteed |
| hub-api → Prefect API calls (R2) | Buttons must start flows now | A polling queue adds latency and an all-day poller |
| Hourly sanctions tick deciding by working day (R10) | Working days need holidays; one idempotent tick is simpler than three calendars | Separate crons can't express "working day 2" |
