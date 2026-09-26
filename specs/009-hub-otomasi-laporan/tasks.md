# Tasks: Noktah Hub: Otomasi & Laporan

**Input**: design documents in `specs/009-hub-otomasi-laporan/`: [plan.md](plan.md),
[spec.md](spec.md), [research.md](research.md), [data-model.md](data-model.md),
[contracts/](contracts/), [quickstart.md](quickstart.md).

**Tests**: included. The repo's convention is that every behaviour gets a test, and the spec's
success criteria (SC-001, SC-007, SC-009) are only provable by tests.

**Format**: `[ID] [P?] [Story] Description`. `[P]` means it can run in parallel (different
files, no dependency on an unfinished task).

## Phase 1: Setup

- [X] T001 Amend the constitution to v1.2.0: Data Management names the Hub Registry as the source for the Jira automation and the Harvest; `hashmap.py` stays only for songbird until DEFERRED A-1, in `.specify/memory/constitution.md`
- [X] T002 [P] Add `HUB_SP_LETTER_FOLDER_ID` to the prefect and prefect-worker env in `docker-compose.yml`, and document it in `.claude/CLAUDE.md` Configuration
- [X] T003 [P] Create `config/hub/holidays.yaml` (Indonesian national holidays, maintained by hand) and `config/hub/sp_letter.html` (the letter from `sp-letter-template.md`)

## Phase 2: Foundational (blocks every story)

- [X] T004 Write migration `config/postgres/migrations/015_hub_automation_reports.sql` and `.down.sql` per [data-model.md](data-model.md): permissions widening, role defaults and grants, `people.started_on`, content plan tables, batches, the Jira copy, event point comments, `harvest_attempts`, `post_review_marks` with the one-time Iklan import, sanctions and letter counters
- [X] T005 Mirror migration 015 into `config/postgres/init.sql`, and add it to the chains in `service/api/tests/conftest.py`, `service/prefect/tests/conftest.py` and `script/verify_schema_parity.sh`
- [X] T006 Add permission keys and actions (`manage_automation`, `view_reports`, `view_incentive`, checked on brand `eskala`) in `service/api/app/permissions.py`; expose `can.manage_automation|view_reports|view_incentive` and accept `started_on` in `service/api/app/people/routes.py` and `service/api/app/people/service.py`
- [X] T007 [P] Prefect API client (start a deployment by name with parameters; recent and next flow runs for deployments) in `service/api/app/prefect_api.py`
- [X] T008 [P] Working-day calendar (Mon–Fri minus `holidays.yaml`; `working_day_n(month, n)`, `end_of_working_day`, `next_working_day_end`) in `service/api/app/workdays.py`
- [X] T009 [P] Jira copy store: upsert issues, append changes, sync state, in `service/api/app/reports/jira_store.py`
- [X] T010 Internal routes wiring: include the automation, reports and incentive internal routers under `/internal` in `service/api/app/internal.py` and `service/api/app/main.py` (public routers too)
- [X] T011 [P] Prefect tasks: `jira.issue.comment`, `jira.issue.get-property`, `jira.search.page`, `jira.fields.map`, `jira.issue.changelog` in `service/prefect/tasks/jira_tasks.py`; `google.sheets.write-cells` and `google.drive.upload-doc` in `service/prefect/tasks/google_tasks.py`
- [X] T012 [P] Tests: permissions and `/v1/me` flags in `service/api/tests/test_automation_permissions.py`; working days in `service/api/tests/test_workdays.py`
- [X] T013 Jira sync flow `hub-jira-sync` (field-name map, paged search with changelog, ingest, cursor) in `service/prefect/flows/hub_jira_sync.py`, with `/internal/jira/sync-state|ingest|comments` in `service/api/app/reports/internal.py`
- [X] T014 [P] Tests: ingest idempotency (changes never duplicated, fields by name) in `service/api/tests/test_jira_store.py`; flow paging and cursor in `service/prefect/tests/test_hub_jira_sync.py`

**Checkpoint**: schema, permissions, calendar, Prefect trigger and Jira copy are in place.

## Phase 3: User Story 1: Greenlight plans and create Jira issues in batches (P1) 🎯 MVP

**Goal**: FR-010…FR-019, FR-022. **Independent test**: greenlight a plan, create its issues,
press again, and see no duplicates, with every key in its row.

- [X] T015 [P] [US1] Plan fingerprints, quota count, blockers and warnings (pure) in `service/api/app/automation/plans.py`
- [X] T016 [US1] Plan scan store and `/internal/automation/plan-targets` + `/internal/content-plans/scan` (the watcher is called here, Phase 4) in `service/api/app/automation/internal.py`
- [X] T017 [US1] Batches: create (checks Greenlight and fingerprint), claim (rows without an issue, Registry ids), result (records issues, rows, progress) in `service/api/app/automation/batches.py`
- [X] T018 [US1] Public routes: `GET /v1/content-plans`, `GET /v1/content-plans/{id}`, `POST …/rescan`, `POST …/greenlight`, `POST /v1/jira-batches`, `GET /v1/jira-batches[/{id}]` in `service/api/app/automation/routes.py`
- [X] T019 [US1] Converter takes explicit component, FA, CE and reporter ids (the hashmap stays the fallback only for the old CLI) and drops `metadata` from the payload, in `service/prefect/tasks/utility_tasks.py`
- [X] T020 [US1] Flow `hub-jira-create` (claim → re-read → refuse if changed → convert with `noktah.plan-row` property → bulk ≤45 → read back property → write Key HYPERLINK → result) in `service/prefect/flows/hub_jira_create.py`
- [X] T021 [US1] Flow `hub-plan-watch` scan part (targets → find file → read tab → scan) in `service/prefect/flows/hub_plan_watch.py`
- [X] T022 [P] [US1] Tests: fingerprint ignores Key/Approval, blockers, warnings, quota in `service/api/tests/test_plan_rules.py`; batch refuses changed plans and never re-creates a keyed row in `service/api/tests/test_batches.py`
- [X] T023 [P] [US1] Tests: create flow maps keys by property not order, writes Key only, refuses a changed plan, reports failures in `service/prefect/tests/test_hub_jira_create.py`
- [X] T024 [US1] Web: Otomasi page (cards + Jira tab with the month's plans, multi-select "Buat issue Jira", batch progress) in `service/web/app/pages/automations/index.vue`; plan page (quota, blockers, warnings, rows, Greenlight, flags) in `service/web/app/pages/automations/plans/[id].vue`; types in `service/web/app/types/hub.ts`
- [X] T025 [US1] Web: nav item "Otomasi" gated by `can.manage_automation` in `service/web/app/layouts/default.vue`; `Me.can` in `service/web/app/composables/useMe.ts`; fixtures in `service/web/server/fixtures/index.ts`; sweep routes in `service/web/e2e/ui-sweep.e2e.ts`

## Phase 4: User Story 2: Keep plans and issues in step (P1)

**Goal**: FR-017, FR-020, FR-021. **Independent test**: change, delete and add keyed rows;
flags and comments appear, and fields don't change.

- [X] T026 [US2] Watcher (R4: changed, deleted, key erased, key duplicated, key unknown; comment text; open-flag dedupe) in `service/api/app/automation/watcher.py`, called from the scan endpoint; `/internal/content-plans/comments` and `POST /v1/content-plans/flags/{id}/resolve`
- [X] T027 [US2] Flow `hub-plan-watch` comment part (post returned comments, report results) in `service/prefect/flows/hub_plan_watch.py`
- [X] T028 [P] [US2] Tests: every watcher rule, one comment per change, no comment for a Key write in `service/api/tests/test_watcher.py`; flow in `service/prefect/tests/test_hub_plan_watch.py`
- [X] T029 [US2] Web: flags with old → new, comment state and "Tandai selesai" on the plan page `service/web/app/pages/automations/plans/[id].vue`

## Phase 5: User Story 3: The Harvest follows the Registry (P1)

**Goal**: FR-030…FR-033. **Independent test**: the targets equal the Registry's active
accounts; a new one gets 90 days.

- [X] T030 [US3] `/internal/harvest/targets` and `GET /v1/harvest/accounts`, `POST /v1/harvest/accounts/{id}/run` in `service/api/app/automation/harvest.py`
- [X] T031 [US3] Task `harvest.attempt.record` in `service/prefect/tasks/harvest_attempt_tasks.py`; flow `harvest-registry` (targets → per account run_harvest 31/90 days → attempt → 30-minute spacing) in `service/prefect/flows/harvest_registry.py`
- [X] T032 [US3] `prefect.yaml`: remove the 22 `harvest-monthly-*` and `social-harvest-sync`; add `harvest-registry`, `hub-plan-watch`, `hub-jira-create`, `hub-jira-sync`, `hub-sanctions` in `service/prefect/prefect.yaml`
- [X] T033 [P] [US3] Tests: targets (active Eskala only, own + competitor, dedupe, first_harvest) in `service/api/tests/test_harvest_targets.py`; flow spacing and window in `service/prefect/tests/test_harvest_registry.py`
- [X] T034 [US3] Web: Harvest tab (accounts table, status, "Jalankan sekarang") in `service/web/app/pages/automations/index.vue`

## Phase 6: User Story 4: Review harvested posts (P2)

**Goal**: FR-034…FR-036. **Independent test**: marks exclude posts from reports and songbird.

- [X] T035 [US4] Marks: `GET /v1/harvest/accounts/{id}/posts`, `PUT /v1/harvest/posts/{platform}/{content_id}/marks` (history, `advertisement` kept in step) in `service/api/app/automation/marks.py`
- [X] T036 [US4] Stop writing the account and detail sheets in `service/prefect/flows/common/social_harvest.py`; exclude marked posts in `service/prefect/tasks/songbird_tasks.py`
- [X] T037 [P] [US4] Tests: marks history, advertisement sync, import in `service/api/tests/test_marks.py`; harvest no longer calls sheet appends in `service/prefect/tests/test_social_harvest.py`
- [X] T038 [US4] Web: account posts page with mark toggles in `service/web/app/pages/automations/accounts/[id].vue`

## Phase 7: User Story 5: Delivery per Client per month (P2)

**Goal**: FR-050, FR-051. **Independent test**: counts equal a hand count from Jira.

- [X] T039 [P] [US5] Status/station table and helpers (R7) in `service/api/app/reports/stations.py`
- [X] T040 [US5] Delivery report in `service/api/app/reports/delivery.py`; `GET /v1/reports/delivery` in `service/api/app/reports/routes.py`
- [X] T041 [P] [US5] Tests: Published, Late, published late, cancelled, month by publication date in `service/api/tests/test_delivery.py`
- [X] T042 [US5] Web: Laporan page with the Delivery tab, nav "Laporan" gated by `can.view_reports`, in `service/web/app/pages/reports/index.vue`

## Phase 8: User Story 6: Stations, returns, people, teams (P2)

**Goal**: FR-052…FR-057. **Independent test**: station numbers equal a hand trace of issue
histories.

- [X] T043 [US6] Station report (intervals, returns by origin, rounds, first pass, QA rounds, people via FA/CE fields and Team) in `service/api/app/reports/station_report.py`; `GET /v1/reports/stations`
- [X] T044 [P] [US6] Tests: every scenario of User Story 6 in `service/api/tests/test_stations.py`
- [X] T045 [US6] Web: Stations tab in `service/web/app/pages/reports/index.vue`

## Phase 9: User Story 7: Account performance (P2)

**Goal**: FR-060…FR-062. **Independent test**: figures equal a hand computation excluding
marked posts.

- [X] T046 [US7] Performance report in `service/api/app/reports/performance.py`; `GET /v1/reports/performance`
- [X] T047 [P] [US7] Tests in `service/api/tests/test_performance.py`
- [X] T048 [US7] Web: Performa tab in `service/web/app/pages/reports/index.vue`

## Phase 10: User Story 8: Points from judged Events (P3)

**Goal**: FR-070…FR-078. **Independent test**: every v2.1 combination gives the right points
and one comment.

- [X] T049 [US8] Points (pure, R9) in `service/api/app/incentive/points.py`; Event comments queued at ingest (`event_point_comments`) in `service/api/app/reports/internal.py`
- [X] T050 [US8] Incentive report `GET /v1/reports/incentive` (points, team reward, incomplete, manual direct sanctions) in `service/api/app/incentive/routes.py`
- [X] T051 [P] [US8] Tests: all five v2.1 point values, Judgment exceptions, Excellence, belum lengkap cases, adaptation, reference date, appeal in `service/api/tests/test_points.py`
- [X] T052 [US8] Web: Insentif page and start date on the person page in `service/web/app/pages/reports/incentive.vue` and `service/web/app/pages/people/[id].vue`

## Phase 11: User Story 9: Sanctions and SP letters (P3)

**Goal**: FR-079…FR-086. **Independent test**: each ladder row, hold, appeal and letter
behaves as specified.

- [X] T053 [US9] Ladder (pure, R10) in `service/api/app/incentive/ladder.py`
- [X] T054 [US9] Sanctions: tick (working day 2 compute + Slack, issue after working day 3, direct sanctions), record, hold, release, letter numbering and rendering in `service/api/app/incentive/sanctions.py` and `service/api/app/incentive/letters.py`; routes in `service/api/app/incentive/routes.py`; internal `sanctions/tick|letters`
- [X] T055 [US9] Flow `hub-sanctions` (tick → upload docs → report) in `service/prefect/flows/hub_sanctions.py`
- [X] T056 [P] [US9] Tests: every §3.2 row, heaviest wins, 90-day validity, same-code teguran, hold window, appeal, PHK only flagged, letter number and signers in `service/api/tests/test_ladder.py` and `service/api/tests/test_sanctions.py`; flow in `service/prefect/tests/test_hub_sanctions.py`
- [X] T057 [US9] Web: sanctions on the Insentif page (hold with reason, record a sanction) in `service/web/app/pages/reports/incentive.vue`

## Phase 12: Polish

- [X] T058 [P] Docs: `.claude/CLAUDE.md` (new flows, retired deployments, Otomasi/Laporan), `service/api/README.md`, `service/web/README.md` (pages table), `.claude/rules/backend/schema.md` (migration 015)
- [X] T059 Run the full test suites and `script/verify_schema_parity.sh`; run `bun run test:ui && bun run ui:gate`
- [X] T060 Copy review of new Hub UI text with the `copy-editor` agent; apply its fixes

## Dependencies

- Phase 1 → Phase 2 → all stories.
- **US1 → US2**: the watcher runs inside the scan endpoint US1 builds.
- **US3 → US4**: marks live on harvested posts.
- **US5 → US6** share `stations.py` (T039).
- **US8 → US9**: the ladder needs points.
- **US5–US9** need T013 (the Jira copy).
- Web tasks follow their story's API tasks.

## Parallel examples

- Phase 2: T007, T008, T009 and T011 are in different files.
- US1: T015 alongside T019.
- After T013: US5 (T039–T041) alongside US3 (T030–T033).

## Implementation strategy

MVP = Phases 1–4 (the Jira automation, the user's top priority). Then US3/US4 (Harvest),
then the reports, then incentives. Each phase ends with its tests green.
