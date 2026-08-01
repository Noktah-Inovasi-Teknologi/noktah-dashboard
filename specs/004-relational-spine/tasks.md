---

description: "Task list for 004-relational-spine"
---

# Tasks: Relational Spine — Clients, Accounts, Runs, and Briefs

**Input**: Design documents from `/specs/004-relational-spine/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md), [data-model.md](./data-model.md), [contracts/](./contracts/)

**Tests**: Included. The plan names specific test files, and FR-029 (`init.sql` ≡ migration chain) is a requirement that cannot be satisfied without an automated check. Schema and constraint tests run against a **real disposable Postgres**, not mocks — partial unique indexes and the `NOT VALID` → `VALIDATE` sequence do not fail against a fake.

**Organization**: Grouped by user story, in the spec's priority order (US1 → US4). Only Phase 2 is a true prerequisite; see "Why US3 is not first" under Dependencies for why the migration *conventions* were split out of US3 and made foundational.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: `[US1]`…`[US4]`, mapping to the spec's user stories

## Path Conventions

Repository root. Migrations in `config/postgres/migrations/`, flows and tasks in `service/prefect/`, helper scripts in `script/`.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Make it possible to test schema work safely before any schema is written.

- [x] T001 Create `script/db_rehearsal.sh` that clones `noktah_dashboard` into a throwaway database (`pg_dump | psql`), so every migration is rehearsed against real data before touching production — per quickstart step 1
- [x] T002 [P] Add `service/prefect/tests/conftest.py` with a session-scoped fixture yielding a disposable Postgres database, skipping the whole module when no database is reachable (mirrors the knowledge-base service's real-database approach in `.claude/rules/backend/knowledge-base.md`)
- [x] T003 [P] Add a `schema` pytest marker to `service/prefect/pyproject.toml` so database-backed tests can be selected or excluded

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The migration conventions every later migration depends on.

**⚠️ CRITICAL**: No migration task in any user story may start until this phase is complete.

- [x] T004 Create `config/postgres/migrations/000_schema_migrations.sql` creating `schema_migrations (version TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())`, idempotent
- [x] T005 [P] Create `config/postgres/migrations/000_schema_migrations.down.sql` dropping only that table
- [x] T006 [P] Create `config/postgres/migrations/001_knowledge_records.down.sql` as an **intentionally empty no-op** with the comment explaining that 001 is a baseline of a pre-existing table and `DROP TABLE` would destroy data predating it — per [contracts/migrations.md](./contracts/migrations.md)
- [x] T007 Add the `schema_migrations` table to `config/postgres/init.sql` so the greenfield path and the migration path agree from the start

**Checkpoint**: Migration conventions established; user story work can begin.

---

## Phase 3: User Story 1 - Ownership and client attribution answerable from the datastore (Priority: P1) 🎯 MVP

**Goal**: Every one of the 656 performance records and 691 ledger records resolves to an account, and every account resolves to its clients and their roles — with no spreadsheet and no name matching at query time.

**Independent Test**: Run the two SQL queries in [quickstart.md](./quickstart.md) step 7. Every signal row appears under a client and a role; the two unattributable handles appear with `NULL` client; the three never-collected competitors are listed. Verify by hand against the spreadsheets.

### Tests for User Story 1 ⚠️

> Write these first; they must fail before implementation.

- [x] T008 [P] [US1] Constraint tests in `service/prefect/tests/test_spine_schema.py`: `UNIQUE (platform, handle_key)` rejects a duplicate handle (FR-011c); `UNIQUE (account_id) WHERE role='owned' AND is_active` rejects a second owner but permits a handover (FR-009); `UNIQUE (client_id, account_id)` rejects a second relationship for a pair (FR-010); `UNIQUE (account_id) WHERE is_current` rejects two current handles
- [x] T009 [P] [US1] Account-resolution tests in `service/prefect/tests/test_account_resolution.py` covering the three outcomes in [contracts/account-resolution.md](./contracts/account-resolution.md) — `resolved`, `unregistered`, `inactive` — plus case-insensitive lookup and a **former** handle still resolving after a rename
- [x] T010 [P] [US1] Roster-sync tests in `service/prefect/tests/test_roster_sync.py`: union of the two roster sources; `Eskala` surfaces under `source_disagreements`; re-running produces all-`unchanged` counters (SC-008); a failed sheet read raises rather than deactivating the roster
- [x] T011 [P] [US1] Backfill tests in `service/prefect/tests/test_spine_backfill.py`: all handles link; an unmatched handle becomes an account with no role rather than being dropped; an opaque identifier is recorded with `identifier_kind = 'opaque_id'`

### Schema for User Story 1

- [x] T012 [US1] Create `config/postgres/migrations/004_relational_spine.sql` with all nine tables and every constraint from [data-model.md](./data-model.md) — `clients`, `client_aliases`, `accounts`, `account_handles`, `client_account_roles`, `account_follower_observations`, `runs`, `briefs` — idempotent, transactional, recording itself in `schema_migrations`
- [x] T013 [US1] Create `config/postgres/migrations/004_relational_spine.down.sql` dropping only the tables 004 introduced, in FK-safe order
- [x] T014 [US1] Create `config/postgres/migrations/005_link_existing.sql` adding `harvested_signals.account_id`, `harvested_signals.run_id`, `harvested_items.account_id`, `harvested_items.run_id`, `knowledge_records.client_id` — all **nullable**, with FKs and supporting indexes. Touch no existing column, index, or constraint (FR-015)
- [x] T015 [US1] Create `config/postgres/migrations/005_link_existing.down.sql` dropping only those five columns and their indexes
- [x] T016 [US1] Mirror 004, 005 **and 006** into `config/postgres/init.sql` so the greenfield path matches (FR-029). 006's two `CHECK` constraints must be included — on a fresh database the harvest tables are empty, so the constraints apply immediately, and omitting them makes `pg_constraint` differ between the two paths and fails the parity check in T039/T044

### Reconciliation for User Story 1

- [x] T017 [P] [US1] Create `service/prefect/tasks/roster_tasks.py` with `roster.client.upsert`, `roster.account.upsert`, `roster.role.upsert` — each idempotent, each raising on failure for Prefect retry
- [x] T018 [US1] Create `service/prefect/flows/common/roster.py`: read the `Clients` worksheet and the `COMPONENTS` block, take their **union** as the roster (research R1), derive owned handles from the `Instagram`/`TikTok` columns via `hashmap.profile_handle`, derive competitor handles from `CLIENT_SOCIAL`, and build the report in [contracts/roster-sync-report.md](./contracts/roster-sync-report.md)
- [x] T019 [US1] Make `service/prefect/flows/common/roster.py` **raise on an unreadable sheet** rather than treating an empty read as an empty roster — departures are inferred from absence, so a failed read would otherwise deactivate every client (mirrors `hashmap.load_hashmaps()`)
- [x] T020 [US1] Implement deactivation in `service/prefect/flows/common/roster.py`: a client, account, or relationship absent from the sheets is marked `is_active = false`, never deleted (FR-024), and each newly-inactive account is listed under `newly_inactive_accounts` with the deployment an operator must remove (FR-024b)
- [x] T021 [US1] Create `service/prefect/flows/roster_sync.py` — the `roster-sync` flow: async, returns the standard `{start_time, end_time, data, summary, error}` dict, never raises, with an `if __name__ == "__main__"` block writing timestamped JSON to `service/prefect/data/`
- [x] T022 [US1] Add the `roster-sync` deployment to `service/prefect/prefect.yaml` on a daily cron, clear of the 03:00 `social-harvest-sync` slot (FR-022a)
- [x] T064 [US1] Add `roster.account.record-rename` to `service/prefect/tasks/roster_tasks.py` (FR-011b, SC-003a): given an account and a new handle, insert the new `account_handles` row and clear `is_current` on the old one **in that order**, since the `UNIQUE (account_id) WHERE is_current` index rejects two current handles. Reject — never infer — when the new handle already belongs to a different account, because that is an account merge and FR-011b forbids inferring one
- [x] T065 [US1] Make `roster.account.upsert` in `service/prefect/tasks/roster_tasks.py` **report rather than create** when a sheet handle matches no account but its client already owns an account on that platform. That is the shape of a rename, and silently creating a second account splits the history FR-011 exists to protect — which is exactly what happens if an operator updates the handle in the Clients sheet and nothing else

### Backfill for User Story 1

- [x] T023 [P] [US1] Create `service/prefect/tasks/spine_tasks.py` with `spine.signal.link` and `spine.item.link`, matching on `(platform, lower(profile_key))` against `account_handles`
- [x] T024 [US1] Create `service/prefect/flows/spine_backfill.py` — the `spine-backfill` flow: create accounts for ledger handles not covered by the roster (`rsmatadryap`, the opaque TikTok identifier) with no role, link both harvest tables, and report every row left unlinked
- [x] T062 [US1] Add `--validate-only` to `service/prefect/flows/spine_backfill.py`, reporting exactly what would be created and linked without writing. The constitution names backfill as a costed batch operation requiring dry-run, and this one mutates 1,347 rows in a single pass
- [x] T063 [US1] Add `--validate-only` to `service/prefect/flows/roster_sync.py`, reporting what would be created, changed, and **deactivated** without writing. Deactivation is the dangerous half: per FR-024a it silently stops storage for every affected account, so it must be previewable before it is applied
- [x] T025 [US1] Create `config/postgres/migrations/006_enforce_account_link.sql` adding `CHECK (account_id IS NOT NULL) NOT VALID` then `VALIDATE CONSTRAINT` on both harvest tables — documented as failing by design if the backfill is incomplete
- [x] T026 [P] [US1] Create `config/postgres/migrations/006_enforce_account_link.down.sql` dropping only those two constraints

### Write path for User Story 1

- [x] T027 [US1] Add `social.account.resolve` to `service/prefect/tasks/social_tasks.py` returning one of `resolved` / `unregistered` / `inactive` per [contracts/account-resolution.md](./contracts/account-resolution.md). It **must never create an account** (FR-016b)
- [x] T028 [US1] Wire resolution into `service/prefect/flows/common/social_harvest.py` at the delivery point (~line 508, beside the existing dedup and signal writes), passing `account_id` into `social_dedup_record` and `social_signal_record`
- [x] T029 [US1] Add `account_id` to the `INSERT`/`ON CONFLICT` statements of `social_dedup_record` and `social_signal_record` in `service/prefect/tasks/social_tasks.py`, leaving both existing unique indexes untouched
- [x] T030 [US1] Implement classified skipping in `service/prefect/flows/common/social_harvest.py`: `items_skipped_unregistered` and `items_skipped_inactive` as separate summary counters carrying the handle, continuing to the next item, and distinguishable from an ordinary zero-item run (FR-016c, FR-023a)

**Checkpoint**: US1 complete. Ownership is answerable in SQL; SC-001, SC-002, SC-003 verifiable.

---

## Phase 4: User Story 2 - Client name reconciliation as data (Priority: P2)

**Goal**: All 14 knowledge-base client names resolve to exactly one client each, by stored data rather than a matching rule in code.

**Independent Test**: [quickstart.md](./quickstart.md) step 8 — `Nirwana Coffee Space` resolves to Pamekasan only; `Lasik Asyik` and `LASIK Asyik by SMEC Tebet` resolve to the same client; inserting a conflicting alias raises a unique violation.

### Tests for User Story 2 ⚠️

- [x] T031 [P] [US2] Alias tests in `service/prefect/tests/test_roster_alias.py`: `UNIQUE (alias_key)` rejects a name already claimed by another client; the rejection is reported and does **not** abort the run; lookup is exact and case-insensitive with no similarity call
- [x] T032 [P] [US2] Seed-mapping test in `service/prefect/tests/test_roster_alias.py` asserting the full alias table from [data-model.md](./data-model.md) — in particular that `Nirwana Coffee Space` maps to Pamekasan and **not** Sumenep, the case the token-subset rule cannot separate

### Implementation for User Story 2

- [x] T033 [P] [US2] Add `roster.alias.upsert` to `service/prefect/tasks/roster_tasks.py`, normalising to `alias_key` and tagging `source` as `clients_sheet`, `components_block`, `knowledge_base`, `former_name`, or `manual`. It MUST be **create-only with respect to `client_id`**: an existing alias row's client binding is never rewritten by a sync, or the daily run silently reverts every manual correction
- [x] T034 [US2] Extend `service/prefect/flows/common/roster.py` to register aliases from all four sources, including both Sumenep spellings and the distinct `Eskala` case (research R1)
- [x] T035 [US2] Seed knowledge-base aliases in `service/prefect/flows/common/roster.py` using the existing token-subset rule as a proposal for names that have **no alias row yet**, then persist. "Once" means create-only, not once-per-run: `roster-sync` runs daily, so re-deriving would overwrite a maintainer's correction with the ambiguous token-subset answer this feature exists to eliminate. After seeding, the rule is never consulted for that name again (spec Assumptions)
- [x] T036 [US2] Implement conflict capture in `service/prefect/flows/common/roster.py`: catch the unique violation, record it under `aliases.conflicts` with both claimants, continue the run (FR-003)
- [x] T037 [US2] Add `spine.knowledge.link` to `service/prefect/tasks/spine_tasks.py` setting `knowledge_records.client_id` by `alias_key`, leaving unresolvable records **retained and reported** rather than dropped (FR-017)
- [x] T038 [US2] Call `spine.knowledge.link` from `service/prefect/flows/spine_backfill.py` and report the unresolved count
- [x] T066 [US2] Add `roster.alias.reassign` to `service/prefect/tasks/roster_tasks.py` (SC-009): repoint an existing alias at a different client, re-tag its `source` as `manual`, and re-link any `knowledge_records` already bound to the old client through that alias. This is the only path that may change an alias's client binding, and it is what makes "correct a mapping by editing data, with no code change" true rather than aspirational
- [x] T067 [P] [US2] Add a test to `service/prefect/tests/test_roster_alias.py` asserting a reassigned alias **survives a subsequent `roster-sync` run** — the regression that would otherwise silently undo every correction at 03:00 the next morning

**Checkpoint**: US1 and US2 both work. SC-004 verifiable; the Nirwana cross-contamination is fixed.

---

## Phase 5: User Story 3 - Migration-managed, reversible schema evolution (Priority: P3)

**Goal**: The two harvest tables that have never had a migration get one, and a database built from migrations alone is provably identical to one built from `init.sql`.

**Independent Test**: [quickstart.md](./quickstart.md) steps 1–2 — apply the chain to an empty database and to a copy of production, twice each; reverse the newest; `script/verify_schema_parity.sh` exits 0 and the 62 / 656 / 691 row counts are unchanged.

### Tests for User Story 3 ⚠️

- [x] T039 [P] [US3] Create `service/prefect/tests/test_schema_parity.py` building two throwaway databases — one from `config/postgres/init.sql`, one from the migration chain — and diffing `information_schema.tables`, `information_schema.columns`, `pg_indexes`, and `pg_constraint`. Skips without a database
- [x] T040 [P] [US3] Add an idempotency test to `service/prefect/tests/test_schema_parity.py` applying every migration twice and asserting the second pass is harmless (FR-028)

### Implementation for User Story 3

- [x] T041 [P] [US3] Create `config/postgres/migrations/002_harvested_items.sql` reproducing the live `harvested_items` structure exactly — all 9 columns, `uq_harvested_item`, `ix_harvested_target_profile`
- [x] T042 [P] [US3] Create `config/postgres/migrations/003_harvested_signals.sql` reproducing the live `harvested_signals` structure exactly — all 16 columns, `uq_harvested_signal`, `ix_signal_profile_engagement`, `ix_signal_profile_trgm`
- [x] T043 [P] [US3] Create `config/postgres/migrations/002_harvested_items.down.sql` and `003_harvested_signals.down.sql` as **intentionally empty no-ops**, each carrying the comment explaining that dropping a baselined pre-existing table would destroy the 691 / 656 rows that predate it (FR-027)
- [x] T044 [US3] Create `script/verify_schema_parity.sh` performing the same structural diff as T039 from the command line, exiting non-zero on any difference
- [x] T045 [US3] Rehearse the full chain per [quickstart.md](./quickstart.md) steps 1–2 using `script/db_rehearsal.sh`, and record the outcome — this is the evidence for SC-006 and SC-007

**Checkpoint**: All three pre-existing tables are migration-covered; `init.sql` and the chain cannot drift silently.

---

## Phase 6: User Story 4 - Durable identity for executions and generated briefs (Priority: P4)

**Goal**: Every collection and generation execution has a stable identifier that outlives it, and a `briefs` structure exists with stable ids.

**Independent Test**: [quickstart.md](./quickstart.md) step 9 — `briefs` exists, holds zero rows, and resolves to a client and a run; every existing flow behaves identically.

### Tests for User Story 4 ⚠️

- [x] T046 [P] [US4] Run-record tests in `service/prefect/tests/test_run_records.py`: a run row is created at start and closed at finish; a crashed flow leaves `ended_at` null rather than a false completion; with the tables empty every existing flow path still succeeds (FR-021)

### Implementation for User Story 4

- [x] T047 [P] [US4] Create `service/prefect/tasks/run_tasks.py` with `run.record.start` and `run.record.finish`, capturing `kind`, `flow_name`, `flow_run_name`, `flow_run_id`, `client_id`, timestamps, status, and the flow's `summary`
- [x] T048 [US4] In `service/prefect/tasks/run_tasks.py`, populate `runs.flow_run_name` from `prefect.runtime.flow_run.name` so it matches the `harvest_name` column that `_resolve_harvest_name` in `service/prefect/flows/common/social_harvest.py` now writes into every delivered sheet row — the join key that already exists on both sides (research R8)
- [x] T049 [US4] Wire `run.record.start` / `run.record.finish` into `service/prefect/flows/common/social_harvest.py`, best-effort so a run-record failure can never abort a harvest
- [x] T050 [US4] Set `harvested_signals.run_id` and `harvested_items.run_id` from the active run record in `service/prefect/flows/common/social_harvest.py`
- [x] T051 [US4] Wire `run.record.start` / `run.record.finish` into `service/prefect/flows/common/songbird.py`, recording `kind = 'generation'` and the client. Write **no** brief rows — populating `briefs` is S-06

**Checkpoint**: All four user stories independently functional. SC-005 verifiable.

---

## Phase 7: Follower Observation Capture (FR-013)

**Purpose**: Satisfies FR-013a/FR-013b. It belongs to no user story's independent test and is sequenced last because it is the only work with an external dependency **known not to work** (research R3). It must not gate anything.

- [x] T052 [P] Add `social.account.record-followers` to `service/prefect/tasks/social_tasks.py`, inserting one `account_follower_observations` row **only** when a count was returned, and never a placeholder row (FR-013a)
- [x] T053 Call it from `service/prefect/flows/common/social_harvest.py` inside a try/except that swallows every failure — follower capture must never fail a collection run (FR-013b)
- [x] T061 In `service/prefect/flows/common/social_harvest.py`, count every account whose follower count was **not** returned into a `follower_capture_missed` summary entry carrying the handle and a classified reason (`not_returned` / `error`). This is the mitigation plan.md's Complexity Tracking promises for the Principle VII deviation — without it, misses are recorded nowhere and the deviation is wider than what was justified
- [x] T054 Verified via mocked tests reproducing the exact documented data shapes (`test_follower_count_recorded_when_platform_returns_one`, `test_follower_count_missing_is_reported_not_treated_as_error`, `test_social_harvest.py`) rather than a live platform run — roach's rules caution against probing Instagram/TikTok beyond what's needed, and research R3 already spent its probe budget confirming Instagram returns no count. A TikTok-shaped `public_metadata.follower_count` writes one observation row; an Instagram-shaped listing with none writes zero and is classified `not_returned`, not an error. The live path itself (`social_account_record_followers` call site in `social_harvest.py`) is unit-tested; end-to-end confirmation happens naturally on the next real `harvest-monthly-*` run
- [x] T055 [P] Record in `service/prefect/tasks/social_tasks.py` the two ruled-out hypotheses from research R3 (rotated `doc_id`; missing session-derived parameters) so the next attempt does not repeat the two probes already spent

---

## Phase 8: Polish & Cross-Cutting Concerns

- [x] T056 [P] Update `.claude/CLAUDE.md` with the `roster-sync` and `spine-backfill` flows, the migration workflow, and the ownership queries
- [x] T057 [P] Create `.claude/rules/backend/schema.md` covering the migration contract, the baseline no-op down scripts, the `NOT VALID` → `VALIDATE` pattern, and the rule that `init.sql` and the chain must stay in parity
- [x] T058 [P] Update `.claude/rules/backend/songbird.md` noting that `CLIENT_SOCIAL` is now mirrored into `client_account_roles`, and that competitor identity is queryable rather than run-scoped
- [x] T068 [P] Create `docs/roster-maintenance.md` — the operator runbook for the two maintainer actions this feature introduces: recording a handle rename (T064) and correcting a client name mapping (T066). Include the failure each one prevents, since neither is discoverable from the schema: updating a handle in the Clients sheet without recording the rename splits an account's history, and hand-editing `client_aliases.client_id` instead of using the reassign path is liable to be reverted by the next daily sync
- [x] T059 Run the full existing suite — `service/prefect/tests/` (182 tests) and `service/knowledge-base/tests/test_repository.py` — confirming in particular that `test_single_current_record_invariant_holds_under_concurrent_upserts` still passes after `knowledge_records` gains `client_id`
- [x] T060 Execute [quickstart.md](./quickstart.md) end to end against the live database and record each measured number against its success criterion

---

## Dependencies & Execution Order

### Why US3 is not first

The spec ranks the stories by value: US1 (P1) is the outcome, US3 (P3) is plumbing. The plan sequences US1 first anyway, because US1's migrations (`004`, `005`, `006`) only **add** tables and columns — they do not need the two legacy tables to be baselined first. US3's baselines matter for rebuilding a database from migrations alone, which nothing in US1 requires.

So the stories are genuinely independent and the order here follows value, not mechanics. The only true prerequisite is Phase 2, which is why the migration conventions were split out of US3 and made foundational.

### Phase Dependencies

- **Phase 1 (Setup)**: no dependencies
- **Phase 2 (Foundational)**: after Setup — **blocks every migration task**
- **Phase 3 (US1)**: after Phase 2
- **Phase 4 (US2)**: after Phase 2. Shares `roster.py` and `spine_tasks.py` with US1, so if both are staffed, US1's T018 and T023 land first
- **Phase 5 (US3)**: after Phase 2. Fully independent of US1/US2 — different files entirely
- **Phase 6 (US4)**: after T012 (the `runs` and `briefs` tables)
- **Phase 7**: after T012 (`account_follower_observations`) and T028 (the harvest write point)
- **Phase 8 (Polish)**: after all desired stories

### Ordering constraints inside US1

`T012 → T014 → T024 → T025`. The enforce migration (T025) **must** run after the backfill (T024) reports zero unlinked rows; running it early fails by design.

### Parallel Opportunities

- T002, T003 together
- T005, T006 together
- All of T008–T011 together (four separate test files)
- T017 and T023 together (different task modules); T064, T065, T066 all land in `roster_tasks.py`, so they are sequential with each other and with T017/T033
- All of T041, T042, T043 together — and the whole of Phase 5 in parallel with Phases 3 and 4, since US3 touches no file US1 or US2 touches
- T056, T057, T058 together

---

## Parallel Example: User Story 1

```bash
# Tests first — four independent files:
Task: "Constraint tests in service/prefect/tests/test_spine_schema.py"
Task: "Account-resolution tests in service/prefect/tests/test_account_resolution.py"
Task: "Roster-sync tests in service/prefect/tests/test_roster_sync.py"
Task: "Backfill tests in service/prefect/tests/test_spine_backfill.py"

# Then the two task modules, which do not share a file:
Task: "Create service/prefect/tasks/roster_tasks.py"
Task: "Create service/prefect/tasks/spine_tasks.py"
```

---

## Implementation Strategy

### MVP (User Story 1 only)

1. Phase 1 → Phase 2 → Phase 3
2. **Stop and validate**: quickstart step 7. Every one of the 656 signal rows resolves to a client and a role, with no spreadsheet open
3. At this point owned-versus-competitor is answerable in SQL — the feature's headline outcome — while the knowledge base still resolves by the old code path and the legacy tables still have no baseline. Both are acceptable interim states

### Incremental Delivery

1. Setup + Foundational
2. **US1** → the ownership query works → MVP
3. **US2** → knowledge grounding stops leaking between the two Nirwana outlets
4. **US3** → the database becomes rebuildable and drift becomes detectable
5. **US4** → runs and briefs gain durable identity for S-06/S-07 to build on
6. **Phase 7** → follower capture, TikTok only, expected to add one account's worth of data

### Risk Notes

- T019 is small and easy to skip. Do not — without it, one failed Sheets read deactivates the entire roster, and per FR-024a that silently stops storage for every account.
- T025 will fail if run before T024 completes. That is the gate working, not a defect.
- T054's Instagram result is expected to be zero observations. Do not treat that as a bug to fix inside this feature; research R3 has already spent the probe budget.

---

## Notes

- **T061–T068 were added by `/speckit-analyze` remediation** and are placed in their execution
  phase rather than in ID order. IDs were not renumbered because T012, T018, T019, T023, T024,
  T025, T028, and T054 are cross-referenced from the Dependencies and Risk Notes sections, and a
  renumber would silently break those references. Phase placement carries execution order.
- **Two invariants are easy to lose and expensive to notice.** `roster-sync` must never rewrite an
  existing alias's `client_id` (T033, T035, T067), and must never create a second account for a
  client that already has one on that platform (T065). Break either and the failure is silent: a
  reverted correction, or an account whose history is split in half while every query still
  returns plausible-looking results.
- `[P]` = different files, no dependency on an incomplete task
- Every schema and constraint test needs a real Postgres; they skip cleanly without one
- `profile_key` is never removed from either harvest table — it is the raw observed value and the only audit trail for the backfill
- Commit after each task or logical group
