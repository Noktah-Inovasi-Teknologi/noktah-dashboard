---
description: "Task list for Songbird — Targeted Content Generation"
---

# Tasks: Songbird — Targeted Content Generation

**Input**: Design documents from `specs/003-songbird-content-generation/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Included (the plan specifies pytest unit coverage for date distribution, column alignment,
graceful degradation, and per-idea failure isolation — the exact behaviours that only reproduce under
the real orchestration logic). Test tasks are marked and may be skipped if you defer testing.

**Organization**: Grouped by user story (US1–US4 from spec.md) for independent implementation/testing.

**Status note**: Some tasks were already implemented during pre-spec prototyping and are checked `[x]`.
Two of them (T009, T010) need a follow-up edit to match clarifications made after they were written — see
their inline notes. Treat checked items as "verify against the design", not "ignore".

## Path Conventions

Prefect service at `service/prefect/`; DB init at `config/postgres/init.sql`; compose at repo root
`docker-compose.yml`. Paths below are repository-relative.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Config and env plumbing every story relies on.

- [x] T001 Add the `harvested_signals` table (+ `uq_harvested_signal`, `ix_signal_profile_engagement`, `ix_signal_profile_trgm`) to `config/postgres/init.sql` per data-model.md.
- [X] T002 Add `OPENROUTER_API_KEY`, `OPENROUTER_MODEL` (optional), `SONGBIRD_DRIVE_PARENT_ID`, and `SONGBIRD_CLIENTS_SPREADSHEET_ID`/`SONGBIRD_CLIENTS_TAB`/`SONGBIRD_QUANTITY_COLUMN` (optional overrides) to BOTH the `prefect` and `prefect-worker` `environment:` blocks in `docker-compose.yml`; document all new keys in the `.env` section of `.claude/CLAUDE.md`.

**Checkpoint**: `docker-compose config` resolves; `docker exec prefect env | grep OPENROUTER_API_KEY` is set.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Shared building blocks used by US1/US2/US3. MUST complete before those stories.

- [x] T003 [P] Add `tasks/openrouter_tasks.py`: async `openrouter.chat.complete` task (provider routing, reasoning disabled, 429 back-off) + `object_schema`/`array_schema` helpers, per contracts/generation-io.md.
- [x] T004 [P] Add `CLIENT_SOCIAL` mapping (`client → {own[], competitors[]}`) to `service/prefect/hashmap.py` per data-model.md (R6).
- [x] T005 [P] Add `songbird.client.context` task in `service/prefect/tasks/songbird_tasks.py` — fuzzy-matched current `knowledge_records` (R4).
- [X] T006 [P] Add `songbird.config.quantity` task in `service/prefect/tasks/songbird_tasks.py` — read the per-client monthly quantity from the Clients worksheet via `google_read_sheet_data` (spreadsheet/tab/column from env with defaults); fail fast when the client row or quantity is absent (FR-003b, R5).
- [X] T007 [US1] (see US1 — engine) — placeholder removed; engine lives in Phase 3.

**Checkpoint**: `docker exec prefect python -c "import tasks.openrouter_tasks, tasks.songbird_tasks, hashmap"` imports clean.

---

## Phase 3: User Story 1 — Monthly content plan, draft (Priority: P1) 🎯 MVP

**Goal**: Produce a reviewable, rationale-annotated draft plan of the client-configured size, dated across
the target month, grounded in knowledge + ranked signal.

**Independent Test**: quickstart.md Scenario 2 — run the monthly flow with `--target draft`; a draft Sheet
with the configured row count appears, every row has `Topik`/in-month `Tanggal`/`Bentuk`, rationale
columns present, content Indonesian with code-mixing, summary carries the disclaimer.

- [x] T008 [US1] Add `songbird.signal.top-performers` task in `service/prefect/tasks/songbird_tasks.py` — rank `harvested_signals` by engagement for a handle set. **NOTE (clarification follow-up): add a `window_days: int = 180` parameter and a `published_at >= now() - make_interval(days => window_days)` (or `NULL`) predicate to satisfy FR-009a; the prototype currently ranks all-time.**
- [X] T009 [US1] Create `service/prefect/flows/common/songbird.py` engine: gather signals (client context, own+competitor top performers via `CLIENT_SOCIAL` + params), assemble the Indonesian/code-mixed prompt (contracts/generation-io.md), call `openrouter_chat` with `array_schema`, parse `items`, and continue-on-failure per idea (FR-020). Returns the standard `{start_time,end_time,data,summary,error}` dict, never raising; `end_time` in `finally`; `summary` includes `ideas_requested/produced/failed`, `signal_available`, `target`, and `hit_disclaimer` (R10).
- [X] T010 [US1] In the engine, implement engine-side publish-date distribution across the target month (`YYYY-MM-DD`, even spread, multi-share when quantity>days) — never delegated to the model (FR-004, R7).
- [X] T011 [US1] In the engine, implement draft delivery: `sheets_create` under `SONGBIRD_DRIVE_PARENT_ID` named `"{client} — Songbird Draft {month}"` with header = content-plan columns + rationale columns, then `sheets_rows_append` the rows, per contracts/content-plan-row.md (FR-016a). Reuse `drive_folder_ensure`/`sheets_create`/`sheets_rows_append` from `google_tasks.py`.
- [X] T012 [US1] Create `service/prefect/flows/songbird_monthly_plan.py` → async `songbird_monthly_plan_flow(client, month, target="draft", quantity=None, platform=…, audience=…, goal=…, tone=…, content_pillars=[], signal_window_days=180, credentials_block_name="google-creds")`; resolve quantity via `songbird.config.quantity` when `quantity is None`; call the engine; `if __name__=="__main__"` argparse + timestamped JSON to `service/prefect/data/` (constitution). Kebab flow name `songbird-monthly-plan`.
- [X] T013 [P] [US1] Unit tests in `service/prefect/tests/test_songbird.py`: date distribution (spread + multi-share), draft header/columns, `signal_available` true/false, per-idea failure isolation (N-1 delivered, `ideas_failed==1`), disclaimer present — model/Google/DB mocked.

**Checkpoint**: Scenario 2 passes end-to-end; MVP is demoable.

---

## Phase 4: User Story 2 — On-demand incidental content (Priority: P1)

**Goal**: Manually generate N standalone draft ideas, no dates, no live handoff.

**Independent Test**: quickstart.md Scenario 4 — run `songbird_generate.py --quantity 3`; exactly 3 draft
ideas, no `Tanggal`, no live write, standard outcome.

- [X] T014 [US2] Create `service/prefect/flows/songbird_generate.py` → async `songbird_generate_flow(client, quantity, platform=…, audience=…, goal=…, tone=…, content_pillars=[], signal_window_days=180, credentials_block_name="google-creds")` reusing the engine in a mode that skips date distribution and forces `target="draft"` (FR-015). Kebab flow name `songbird-generate`; argparse + timestamped JSON `__main__` block.
- [X] T015 [US2] Ensure the engine supports a `distribute_dates: bool`/`target` gate so on-demand emits blank `Tanggal` and never writes live (share the engine with US1; add the branch, don't fork it).
- [X] T016 [P] [US2] Unit test in `service/prefect/tests/test_songbird.py`: on-demand run produces exactly `quantity` ideas, all `Tanggal` empty, `target=="draft"`, no live-append call invoked.

**Checkpoint**: Scenario 4 passes; both P1 stories independently runnable.

---

## Phase 5: User Story 3 — Live handoff to the content-plan worksheet (Priority: P2)

**Goal**: With `--target live`, append monthly ideas into the existing content-plan worksheet aligned to
its header, rationale columns dropped, consumable by the content-plan → Jira flow.

**Independent Test**: quickstart.md Scenario 3 — `--target live` against a throwaway tab, then
`content_plan_spreadsheet_to_jira_issue.py --validate-only` validates the rows with no issues created.

- [X] T017 [US3] In the engine, implement live delivery: read the target worksheet header via `google_read_sheet_data`, build each row by column-name lookup (unmapped fields dropped, rationale columns excluded), then `sheets_rows_append` (FR-016, contracts/content-plan-row.md). Log missing content-plan columns; never fabricate columns.
- [X] T018 [US3] Wire `target`, `live_spreadsheet_id` (default the content-plan `SPREADSHEET_ID`), and `live_tab` params through `songbird_monthly_plan_flow` to the engine; keep `draft` the default (FR-015).
- [X] T019 [P] [US3] Unit test in `service/prefect/tests/test_songbird.py`: given a shuffled/extra-column live header, rows align by name, rationale columns absent, no new columns; unmapped generated field omitted.

**Checkpoint**: Scenario 3 passes; generated plan flows to Jira validation unchanged.

---

## Phase 6: User Story 4 — Capture performance signal during harvesting (Priority: P2)

**Goal**: The existing social-harvest run mirrors each delivered item's engagement + analysis into
`harvested_signals`, idempotently and best-effort.

**Independent Test**: quickstart.md Scenario 1 — harvest own + competitor handles; both appear in
`harvested_signals` with engagement + analysis, rankable; a signal-write failure never aborts the harvest.

- [x] T020 [US4] Add `social.signal.record` task (idempotent upsert on `(platform, content_id)`, count coercion) in `service/prefect/tasks/social_tasks.py` (FR-013).
- [x] T021 [US4] Call `social_signal_record` best-effort at the delivery point in `service/prefect/flows/common/social_harvest.py` (try/except → warn, never abort — FR-014); import it in both the relative and standalone import blocks.
- [X] T022 [P] [US4] Unit test in `service/prefect/tests/test_social_tasks.py` (or extend existing): `_coerce_count` handles `''`/`'1.2K'`/`'3M'`/int; upsert updates on conflict rather than duplicating (mocked asyncpg).

**Checkpoint**: Scenario 1 passes; signal store fills and ranks.

---

## Phase 7: Polish & Cross-Cutting Concerns

- [X] T023 [P] Add `songbird-monthly-plan` (monthly cron, params `client`/`month`/`target=draft`) and `songbird-generate` (no schedule, manual, empty defaults) deployments to `service/prefect/prefect.yaml`, mirroring the `social-harvest-*` block; `pull` set_working_directory `/app`.
- [X] T024 [P] Document songbird in `.claude/CLAUDE.md` (Development Commands: run flows, deploy, params table) and add `.claude/rules/backend/songbird.md` mirroring the social-harvest rules doc.
- [X] T025 [P] Update `.env.example` (if present) / the env docs with the Phase-1 keys and a note that `OPENROUTER_API_KEY` is now needed by the Prefect services (previously roach-only).
- [X] T026 Run `quickstart.md` Scenarios 2→4→3→1→5 against the live stack; confirm SC-001..SC-007 outcomes; fix any gaps.

---

## Dependencies & Execution Order

- **Setup (P1: T001–T002)** → blocks everything (DB table + env).
- **Foundational (P2: T003–T006)** → blocks US1/US2/US3.
- **US1 (T008–T013)** → the MVP; T008/T009 are prerequisites for US2 and US3 (shared engine + signal task).
- **US2 (T014–T016)** → depends on the engine (T009); otherwise independent of US3/US4.
- **US3 (T017–T019)** → depends on the engine (T009) + monthly flow (T012).
- **US4 (T020–T022)** → fully independent (touches only social-harvest); can proceed any time. Enables
  richer US1 signal but US1 degrades gracefully without it.
- **Polish (T023–T026)** → after the stories they document/deploy are in place.

## Parallel Opportunities

- Foundational: T003, T004, T005, T006 are all `[P]` (distinct files/functions).
- US4 (T020–T022) can run in parallel with all of US1–US3 (separate module).
- Test tasks T013/T016/T019/T022 are `[P]` within/after their story.
- Polish T023/T024/T025 are `[P]`.

## Implementation Strategy

- **MVP = US1 (monthly draft)**: Setup + Foundational + Phase 3 delivers a demoable, valuable slice
  (grounded, dated, rationale-annotated draft plan). Ship it, then layer US2 (on-demand), US3 (live
  handoff), and US4 (signal enrichment) incrementally.
- **Reconciliation**: T008/T010 already exist from prototyping — apply the noted edits (180-day window;
  ensure quantity comes from the Clients sheet, not a flow default) so the code matches the clarified spec.
