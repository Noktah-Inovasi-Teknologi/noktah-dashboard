---
description: "Task list for Social Profile Content Harvest & Analysis"
---

# Tasks: Social Profile Content Harvest & Analysis

**Input**: Design documents from `specs/002-social-content-harvest/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/ (roach-api.md, sheet-schema.md), quickstart.md

**Tests**: Included for the correctness-critical logic (pacing, hourly cap, back-off, dedupe,
continue-on-failure) and the roach API contract, per the plan's Testing strategy. Other tasks are
implementation-only.

**Organization**: Grouped by user story. US1 and US2 are both P1; US1 is the harvest→analyze→deliver
MVP, US2 hardens it for real (unblockable) runs. US3 (P2) adds observability + notification.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: US1 / US2 / US3 (Setup/Foundational/Polish have no story label)
- File paths are repo-relative.

## Path Conventions

- Orchestration: `service/prefect/` (flows, tasks, blocks, tests)
- Scraper API: `service/roach/` (api, collect, analyze, tests)
- Schema: `config/postgres/init.sql`; compose: `docker-compose.yml`

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Dependencies, container wiring, and env plumbing for both services.

- [X] T001 [P] Add `gallery-dl`, `fastapi`, `uvicorn[standard]` to `service/roach/pyproject.toml` and regenerate `service/roach/uv.lock` (`uv lock`)
- [X] T002 [P] Add `httpx` and `asyncpg` to `service/prefect/pyproject.toml` and regenerate `service/prefect/uv.lock` (`uv lock`)
- [X] T003 [P] Add `ROACH_API_KEY` to `service/roach/.env.example`; document `ROACH_API_KEY` and `HARVEST_DRIVE_PARENT_ID` (stable root Drive folder id for all output) as new `.env` entries in `.claude/CLAUDE.md` — `ROACH_API_URL` and `HARVEST_DB_URL` are auto-derived in `docker-compose.yml` (internal Docker network address / DSN built from existing `POSTGRES_*` vars) and documented there as such, not as manual entries
- [X] T004 Update `service/roach/Dockerfile` to `EXPOSE 8080` and replace the `sleep infinity` CMD with `uvicorn api:app --host 0.0.0.0 --port 8080` (ffmpeg install already present)
- [X] T005 Update `docker-compose.yml`: publish roach as `8081:8080` (host:container — 8080 reserved on the host for the local Google OAuth redirect flow) + add `/health` health check; declare a named volume `social_data` mounted at `/data` in **both** `roach` and `prefect`; add `ROACH_API_URL=http://roach:8080`, `ROACH_API_KEY`, `HARVEST_DB_URL` (auto-derived from `POSTGRES_*`), and `HARVEST_DRIVE_PARENT_ID` env to the `prefect` service

**Checkpoint**: `docker-compose up -d --build` brings up roach (serving) + prefect + postgres.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Schema, Google write access, the roach API + primitives, and the Prefect task/engine
scaffolding that every user story builds on.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [X] T006 Add `harvested_items` table (columns + `UNIQUE(platform, content_id, drive_target)` + `INDEX(drive_target, profile_key)` per data-model.md) to `config/postgres/init.sql` as an idempotent `CREATE TABLE IF NOT EXISTS` block
- [X] T007 Widen scopes to `drive.file` + `spreadsheets` and add write helpers `ensure_folder`, `upload_file`, `create_spreadsheet`, `append_rows` to `GoogleClient` in `service/prefect/blocks/google_credentials.py` (keep `load_or_env` pattern)
- [X] T008 [P] Add Google delivery tasks `drive.folder.ensure`, `drive.file.upload`, `sheets.create`, `sheets.rows.append` (each `retries=2, retry_delay_seconds=30`) to `service/prefect/tasks/google_tasks.py`
- [X] T009 [P] Create `service/roach/api.py`: FastAPI app, `GET /health` (no auth), and a raw `X-API-KEY` header check (401 otherwise) for all other routes
- [X] T010 Create `service/roach/collect.py`: video primitive (yt-dlp, reuse scaffold opts) + non-video primitive (gallery-dl) writing to `/data`, returning normalized item metadata + `local_paths`
- [X] T011 Extend `service/roach/analyze.py`: branch by media type — video keeps the audio+video call; image/carousel sent as `image_url`(s) with `subtitle` left empty; return `{subtitle, flow, summary, status, error}`
- [X] T012 Implement `POST /list`, `POST /download`, `POST /analyze` in `service/roach/api.py` per `contracts/roach-api.md` (status codes `404`/`429`/`500`, `{ok,code,reason}` bodies) — depends on T009, T010, T011
- [X] T013 [P] Create `service/prefect/tasks/social_tasks.py`: `social.profile.list`, `social.item.download`, `social.item.analyze` calling roach via `httpx` with the `X-API-KEY` header, surfacing `429`/`404` as typed outcomes
- [X] T014 [P] Add a randomized-delay helper (5–10 s, skip-after-last) and a rolling-hour `RateWindow` limiter (timestamp `deque`, ≤100) to `service/prefect/tasks/utility_tasks.py`
- [X] T015 Create the shared harvest engine scaffold `service/prefect/flows/common/social_harvest.py`: async `run_harvest(profiles, depth_selector, ...)` returning the RunResult dict (`start_time/end_time/data/summary/error`), `end_time` in `finally`, never raising — depends on T013

**Checkpoint**: roach API answers `/list|/download|/analyze`; Prefect can call it and reach Drive/DB.

---

## Phase 3: User Story 1 - Harvest & analyze a batch of public profiles (Priority: P1) 🎯 MVP

**Goal**: End-to-end happy path — up to five public profiles → per-profile Drive folders of
downloaded media + one run-level analysis Google Sheet with metadata + analysis per item.

**Independent Test**: Run one public profile; confirm an account-named Drive folder holds the
media and the Sheet has one row per item with metadata + subtitle/content-flow/summary.

### Tests for User Story 1

- [X] T016 [P] [US1] roach API contract tests (`/list|/download|/analyze` shapes, auth, empty items) in `service/roach/tests/test_api.py` (mock yt-dlp/gallery-dl/OpenRouter); **assert `public_counts` excludes reach/impressions/saves** (FR-003)
- [X] T017 [P] [US1] Flow happy-path test (one profile → folder + Sheet rows) with roach + Drive mocked in `service/prefect/tests/test_social_harvest.py`

### Implementation for User Story 1

- [X] T018 [US1] Engine collection loop: for each profile call `social.profile.list`, then sequentially `social.item.download` → `social.item.analyze`, building ContentItem+Analysis records in `service/prefect/flows/common/social_harvest.py` (depends on T015)
- [X] T019 [US1] Engine delivery: `ensure_folder` per profile **within `HARVEST_DRIVE_PARENT_ID`** (named after handle; capture the returned stable folder id), `upload_file` media, `create_spreadsheet` once per run under the same parent, `append_rows` per item per `contracts/sheet-schema.md`, and **delete each item's local `social_data` file(s) immediately after a successful upload** (R10), in `service/prefect/flows/common/social_harvest.py` (depends on T008, T018)
- [X] T020 [US1] Depth selectors in the engine: `recent` (newest N by `published_at`) and `window` (published within last X days) in `service/prefect/flows/common/social_harvest.py`
- [X] T021 [P] [US1] Create flow `service/prefect/flows/social_harvest_recent.py` (name `social-harvest-recent`, params `profiles`, `n=10`; argparse `__main__`; timestamped JSON to `data/`)
- [X] T022 [P] [US1] Create flow `service/prefect/flows/social_harvest_window.py` (name `social-harvest-window`, params `profiles`, `days=7`; argparse `__main__`; timestamped JSON to `data/`)
- [X] T023 [US1] Route video vs non-video items into rows and handle zero-content profile (empty folder + logged note, no rows) in the engine

**Checkpoint**: US1 is independently demoable — quickstart Scenarios 1 & 2 pass.

---

## Phase 4: User Story 2 - Safe, resilient collection without tripping bot detection (Priority: P1)

**Goal**: Pace/randomize/sequential collection, enforce the 100/hour cap and ≤5 profiles, detect
rate-limit/challenge and back off + defer, de-duplicate re-runs, and isolate single-item/profile
failures without losing collected content.

**Independent Test**: Run ≥2 profiles with one returning a block/challenge; the run continues,
others complete, already-collected content is retained; a re-run skips duplicates.

### Tests for User Story 2

- [X] T024 [P] [US2] Tests for the 100/rolling-hour cap, randomized-delay omission after last item, `429`/challenge back-off + profile deferral, dedupe skip, and continue-on-item/profile-failure in `service/prefect/tests/test_social_harvest.py`

### Implementation for User Story 2

- [X] T025 [US2] Enforce sequential collection with a randomized 5–10 s delay between items (omitted after the last) using the T014 helper in `service/prefect/flows/common/social_harvest.py`
- [X] T026 [US2] Enforce the 100-item rolling-hour cap via the `RateWindow` limiter (pause/back-off when full) in `service/prefect/flows/common/social_harvest.py`
- [X] T027 [US2] On roach `429`/challenge, apply exponential back-off then defer/skip the profile while retaining its already-collected content, in `service/prefect/flows/common/social_harvest.py`
- [X] T028 [US2] Dedupe: add `social.dedup.check` / `social.dedup.record` (asyncpg against `HARVEST_DB_URL`) in `service/prefect/tasks/social_tasks.py`; resolve each profile's **stable folder id (from T019) as `drive_target`**, skip already-collected `(content_id, drive_target)` before download, and insert after delivery in the engine (depends on T006, T019)
- [X] T029 [US2] Reject/truncate runs with >5 profiles with a clear message in both flows + engine entry (`service/prefect/flows/common/social_harvest.py`)
- [X] T030 [US2] Anonymous-first vs burner-cookie fallback selection per platform (TikTok anonymous; Instagram cookies from `secrets/cookies.txt`) in `service/roach/collect.py`
- [X] T031 [US2] Per-item and per-profile failure isolation (skip + log, retain download on analysis failure, continue run) plus a best-effort **`social_data` retention sweep in the flow `finally`** to remove residue from skipped/failed items (R10), in `service/prefect/flows/common/social_harvest.py`

**Checkpoint**: quickstart Scenarios 3, 4 & 5 pass; US1 still works.

---

## Phase 5: User Story 3 - Observability and completion notification (Priority: P2)

**Goal**: Structured progress logging via the orchestrator and a completion notification for both
manual and scheduled runs.

**Independent Test**: Run the flow; Prefect run logs show per-profile/per-item progress (counts,
delays, back-offs, failures) and a completion notification is delivered; the scheduled deployment
produces identical output.

### Implementation for User Story 3

- [X] T032 [US3] Emit structured `get_run_logger` INFO/WARNING/ERROR entries (current profile, current item, cumulative counts, applied delays, back-off events, failures) throughout `service/prefect/flows/common/social_harvest.py`
- [X] T033 [US3] Populate the `summary` dict (profiles_processed, items_collected, items_skipped_dedup, items_failed, profiles_blocked, drive_folder_ids, sheet_id) in the engine RunResult
- [X] T034 [US3] Send a completion notification via a configurable Prefect notification block (loaded by name) at flow end in `service/prefect/flows/common/social_harvest.py` (depends on T033)
- [X] T035 [P] [US3] Add Prefect deployments for both flows via `service/prefect/prefect.yaml` (deployed with `prefect deploy --all` onto the existing `noktah-pool` process work pool, matching the `content-plan-to-jira` deployment pattern); execution is handled by the `prefect-worker` compose service (restored/added to `docker-compose.yml`). Deployments have **no schedule** — triggered manually. Notification block is configured via `HARVEST_NOTIFICATION_BLOCK` (spec Assumptions). Verified live: worker claims a deployment run and executes it via the `set_working_directory` pull step.

**Checkpoint**: quickstart Scenario 6 passes for manual and scheduled runs.

---

## Phase 6: Polish & Cross-Cutting Concerns

- [X] T036 [P] Update `service/roach/README.md` (interface is now an internal HTTP API) and the service/ports table in `.claude/CLAUDE.md`
- [X] T037 [P] Secret-hygiene pass: confirm no credentials in source/logs and that `service/roach/secrets/cookies.txt` is gitignored (FR-019/SC-008)
- [X] T038 Run all automated checks: `service/prefect` pytest + `service/roach` pytest
- [X] T039 Execute `quickstart.md` Scenarios 1–6 end-to-end against the running stack — partially validated: both images build cleanly (`docker compose build roach prefect`) and an isolated roach container smoke-tests correctly (`/health` 200, unauthenticated `/list` 401). Full scenario runs against real Instagram/TikTok/Google/OpenRouter require live credentials (`HARVEST_DRIVE_PARENT_ID`, a Drive-write-scoped refresh token, `OPENROUTER_API_KEY`, `ROACH_API_KEY`) not present in this environment — left for the user to run per quickstart.md once configured

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: no dependencies.
- **Foundational (Phase 2)**: depends on Setup; **blocks all user stories**.
- **US1 (Phase 3)**: depends on Foundational.
- **US2 (Phase 4)**: depends on Foundational; layers onto the US1 engine (US1 recommended first).
- **US3 (Phase 5)**: depends on Foundational; observes the engine (best after US1/US2 exist).
- **Polish (Phase 6)**: depends on all targeted stories.

### Key task dependencies

- T012 → T009, T010, T011 (roach endpoints need app + primitives + analyze)
- T015 → T013 (engine calls social tasks)
- T018 → T015; T019 → T008, T018; T020/T023 → T018
- T021/T022 → T020 (flows invoke depth-aware engine)
- T028 → T006 (dedupe needs the table)
- T034 → T033 (notification consumes the summary)

### Within each user story

- Tests (T016/T017, T024) written to fail first, then implementation.
- Engine collection (T018) before delivery (T019) before depth/routing (T020/T023).

### Parallel Opportunities

- Setup: T001, T002, T003 in parallel.
- Foundational: T008, T009, T013, T014 in parallel (distinct files); T007 before T008 conceptually (scope) but different files, so sequence T007→T008.
- US1 tests: T016, T017 in parallel. Flows T021, T022 in parallel.
- US2 relies on shared-engine edits (mostly sequential in `social_harvest.py`); T030 (roach) is parallel to them.

---

## Parallel Example: Foundational

```bash
# After T007 (scope widening), these touch different files and can run together:
Task: "T008 Google delivery tasks in service/prefect/tasks/google_tasks.py"
Task: "T009 roach FastAPI app + auth in service/roach/api.py"
Task: "T013 social.* roach-calling tasks in service/prefect/tasks/social_tasks.py"
Task: "T014 delay helper + RateWindow limiter in service/prefect/tasks/utility_tasks.py"
```

## Parallel Example: User Story 1

```bash
Task: "T016 roach contract tests in service/roach/tests/test_api.py"
Task: "T017 flow happy-path test in service/prefect/tests/test_social_harvest.py"
# then, after engine depth selection:
Task: "T021 flow service/prefect/flows/social_harvest_recent.py"
Task: "T022 flow service/prefect/flows/social_harvest_window.py"
```

---

## Implementation Strategy

### MVP First (US1)

1. Phase 1 Setup → 2. Phase 2 Foundational → 3. Phase 3 US1 → **validate** (Scenarios 1 & 2) → demo.
   This delivers a working harvest→analyze→deliver for cooperative (unblocked) profiles.

### Incremental Delivery

1. Setup + Foundational → foundation ready.
2. US1 → single-run harvest to Drive + Sheet (MVP).
3. US2 → makes real multi-profile runs survivable (pacing, cap, back-off, dedupe, isolation).
4. US3 → logging + completion notification + scheduling.

### Notes

- Both P1 stories are needed for a production-usable capability; US1 alone is demoable but will trip
  bot-detection on sustained runs (that is US2's job).
- Widening Google scopes (T007) requires **re-minting the refresh token** with consent for
  `drive.file` + `spreadsheets` (research R2) before delivery works.
- [P] = different files, no incomplete-task dependency. Commit after each task or logical group.
