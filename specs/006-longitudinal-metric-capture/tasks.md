---

description: "Task list for 006-longitudinal-metric-capture"
---

# Tasks: Longitudinal Metric Capture

**Input**: Design documents from `/specs/006-longitudinal-metric-capture/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md),
[data-model.md](./data-model.md), [contracts/](./contracts/), [quickstart.md](./quickstart.md)

**Tests**: Included. `.claude/rules/backend/schema.md` requires partial indexes, CHECK-constraint
behaviour, and cross-source reconciliation to be tested against a **real disposable PostgreSQL
database** — this feature is entirely that class of change. Schema tests carry
`pytestmark = pytest.mark.schema`; pure arithmetic is unit-tested without a database.

**Organization**: Grouped by user story so each is independently implementable and testable.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: US1 / US2 / US3, mapping to spec.md user stories
- Exact file paths are given in every task

## Path Conventions

Existing repository layout — no new project scaffold:

- Migrations: `config/postgres/migrations/`, mirrored into `config/postgres/init.sql`
- Prefect service: `service/prefect/{flows,tasks,tests}/`
- Deployments: `service/prefect/prefect.yaml`

---

## Phase 1: Setup

**Purpose**: Correct a known-stale figure and stand up the config surface and rehearsal clone before
anything is written.

- [X] T001 Correct the stale corpus figures in [specs/006-longitudinal-metric-capture/spec.md](./spec.md): replace the literal "656" in **FR-021** and **SC-008** with "all pre-feature rows", per [research.md](./research.md) R1 (measured 819 on 2026-08-03 and still growing). Leave the Problem section's historical 656 intact — it correctly describes the audit snapshot.
- [X] T002 [P] Create `service/prefect/tasks/velocity_tasks.py` with env-backed config constants only: `VELOCITY_MIN_INTERVAL_SECONDS` (86400), `VELOCITY_PLATEAU_FRACTION` (0.10), `VELOCITY_ACCELERATION_MULTIPLE` (3.0), `VELOCITY_DETECTION_METRIC` ("likes"), `VELOCITY_CONFIG_VERSION` ("v1") — per [contracts/velocity-derivation.md](./contracts/velocity-derivation.md) § Configuration
- [X] T003 [P] Create the rehearsal database clone: `script/db_rehearsal.sh create`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Schema, the observation write path, and legacy seeding. Every user story reads or
writes `metric_observations`, so none can begin until this is done.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

**Why legacy seeding is foundational rather than part of US3**: deriving velocity before seeding
would produce intervals for pre-existing items that are missing their earliest endpoint, and those
intervals would then need re-deriving. Seeding is an ordering constraint on correctness, not merely
a US3 deliverable.

- [X] T004 Write `config/postgres/migrations/008_observation_history.sql` — `metric_observations` and `velocity_intervals` tables, the `velocity_status` view, and the widened `chk_capture_outcomes_kind` CHECK, per [data-model.md](./data-model.md). Idempotent (`IF NOT EXISTS`, `pg_constraint` guards), transactional, self-recording into `schema_migrations`
- [X] T005 Write `config/postgres/migrations/008_observation_history.down.sql` — drop both tables and the view, and restore the **original four-value** `chk_capture_outcomes_kind` exactly
- [X] T006 Mirror migration 008 into `config/postgres/init.sql`, declaring every constraint as a **named ALTER matching the migration's constraint names exactly** — not inline `REFERENCES` — per the parity trap in `.claude/rules/backend/schema.md`
- [X] T007 Apply 008 to the rehearsal clone, then verify idempotence (re-apply) and reversibility (down, then up again), per [quickstart.md](./quickstart.md) step 1. Confirm the widened CHECK lists `metric_refresh` **plus all four original kinds**
- [X] T008 Run `script/verify_schema_parity.sh` and confirm exit 0
- [X] T009 Write `service/prefect/tests/test_metric_observation.py` with `pytestmark = pytest.mark.schema`: `chk_metric_observations_has_a_value` rejects an all-NULL observation; `provenance` vocabulary is closed; `uq_metric_observation_capture` makes a same-instant re-insert a no-op; a zero count stores as 0 and is distinguishable from NULL
- [X] T010 Implement `social.observation.record` in `service/prefect/tasks/social_tasks.py` per [contracts/observation-record.md](./contracts/observation-record.md) — reuse the existing `_coerce_count`, return `None` when all four counts are NULL, `ON CONFLICT DO NOTHING`, and issue no `UPDATE`/`DELETE` ever
- [X] T011 Implement `velocity.observation.backfill` in `service/prefect/tasks/velocity_tasks.py` — seed one `provenance='legacy'` observation per pre-existing signal row stamped with its existing `harvested_at`, guarded by `NOT EXISTS`, skipping rows with no metric at all; report the count seeded
- [X] T011a Create `service/prefect/flows/observation_backfill.py` — the `observation-backfill` flow wrapping `velocity.observation.backfill`, with a `validate_only` param and an `if __name__ == "__main__":` CLI block. Standalone and unscheduled, mirroring `service/prefect/flows/spine_backfill.py`; reports the count seeded. Do **not** add a backfill parameter to `velocity-derive` — that flow is scheduled monthly and must not carry a one-shot operation
- [X] T012 Extend `service/prefect/tests/test_metric_observation.py` with seeding tests: seeded `observed_at` equals `harvested_at` and **never** `now()`; a second run inserts nothing; metric-less rows are skipped
- [X] T013 Seed legacy observations into the rehearsal clone and verify per [quickstart.md](./quickstart.md) step 3 — the `observed_at` range must match the harvest dates, not today

**Checkpoint**: Observation store exists, is append-only, and holds the full pre-feature corpus.

---

## Phase 3: User Story 1 - Engagement stops being frozen at first sighting (Priority: P1) 🎯 MVP

**Goal**: A re-listed item's current counts are recorded without any download or model call, for
every item in the listing response — including those the day-window filter discards.

**Independent Test**: Run an existing monthly harvest deployment against a fully-harvested profile.
Confirm new observations appear, `items_collected` stays at or near zero, no model spend is
incurred, and at least one observation lands on an item published more than 31 days ago.

### Tests for User Story 1

> Write these first and confirm they fail before implementing.

- [X] T014 [US1] Extend `service/prefect/tests/test_social_harvest.py`: the refresh pass records an observation for an already-successful item while `social_item_download` and `social_item_analyze` are **never called**; the dedup gate still increments `items_skipped_dedup`
- [X] T014a [US1] Add a schema-marked regression test to `service/prefect/tests/test_social_harvest.py`: an item with accumulated `metric_observations` rows that goes through the failure-retry purge path ([social_harvest.py:605-619](../../service/prefect/flows/common/social_harvest.py#L605-L619)) retains every observation after the purge (FR-005). The purge deletes Drive files, sheet rows, and the ledger record — it must not reach the observation store. This holds by construction today; the test is what keeps it holding
- [X] T015 [P] [US1] Add a test in `service/prefect/tests/test_social_tasks.py`: the metrics-only latest-value update leaves `subtitle`, `content_flow`, `summary`, and `advertisement` unchanged (FR-010a — the [research.md](./research.md) R8 destruction case)
- [X] T016 [US1] Add a test asserting the observation pass iterates **`all_items`**, not the depth-selected `items`: an item excluded by the window selector still receives an observation (FR-004)

### Implementation for User Story 1

- [X] T017 [US1] Add the observation pass to `service/prefect/flows/common/social_harvest.py` — iterate `all_items`, positioned **after** per-profile account resolution and **before** the per-item download loop. Do not modify the dedup gate at lines 600-604
- [X] T018 [US1] Record a `capture_outcomes` row per observed item in `service/prefect/flows/common/social_harvest.py` with `capture_kind='metric_refresh'` and `outcome` of `success` or `no_match`, per [data-model.md](./data-model.md) § capture_outcomes
- [X] T019 [US1] Add a metrics-only latest-value update to `service/prefect/tasks/social_tasks.py` that writes only metric columns and `harvested_at`. It MUST NOT write `subtitle`, `content_flow`, `summary`, or `advertisement` — do not reuse `social.signal.record`, whose upsert overwrites those unconditionally ([social_tasks.py:292-294](../../service/prefect/tasks/social_tasks.py#L292-L294))
- [X] T020 [US1] Wrap the whole pass best-effort in `service/prefect/flows/common/social_harvest.py` — a failed observation write must never fail a harvest that delivered content, matching how `social.signal.record` is already treated
- [X] T021 [US1] Add run-summary counters `observations_recorded`, `observations_skipped_no_counts`, `observations_failed` to the summary dict in `service/prefect/flows/common/social_harvest.py`
- [X] T022 [US1] Validate against the rehearsal clone per [quickstart.md](./quickstart.md) step 4, including the "observed outside window" query (must be > 0) and the request-volume comparison against a pre-feature run (`request_stats` must be unchanged)

**Checkpoint**: Engagement is no longer frozen. Shippable on its own — this is the MVP.

---

## Phase 4: User Story 2 - Velocity derived from unevenly spaced observations (Priority: P2)

**Goal**: Change, per-day rate, and late acceleration are derived from stored observations and
persisted, correct under irregular spacing, and re-runnable without duplication.

**Independent Test**: Seed one item with three observations spaced 2 days then 26 days. Confirm the
per-day rates reflect each interval's own elapsed time, differ from what uniform spacing would give,
and are read back as stored values rather than recomputed.

### Tests for User Story 2

- [X] T023 [P] [US2] Write pure-arithmetic unit tests (no database) in `service/prefect/tests/test_velocity_derivation.py`: irregular spacing (2d then 26d) yields per-interval rates; a decrease produces a negative delta unclamped; a sub-24h pair stores deltas with all rates NULL and `rate_withheld_reason='interval_too_short'`; identical trajectories on accounts differing by an order of magnitude flag identically
- [X] T024 [P] [US2] Write plateau/acceleration unit tests in `service/prefect/tests/test_velocity_derivation.py`: detection needs ≥3 observations; a withheld interval is excluded and is **not** treated as a plateau (FR-015a); an item with too few observations is not recorded as non-accelerating
- [X] T025 [US2] Write schema-marked tests in `service/prefect/tests/test_velocity_derivation.py`: two consecutive derivation runs leave row count and values identical (FR-016c); a re-run under a new `config_version` updates in place without duplicating; an interval anchored on a `legacy` observation is derivable and identifiable as such (FR-023a)

### Implementation for User Story 2

- [X] T026 [US2] Implement `velocity.interval.derive` in `service/prefect/tasks/velocity_tasks.py` — walk each item's observations in `observed_at` order, compute `elapsed_seconds` from measured timestamps (never from position), deltas per metric, and per-day rates only when the interval meets the floor; upsert on `(from_observation_id, to_observation_id)`
- [X] T027 [US2] Implement `velocity.acceleration.detect` in `service/prefect/tasks/velocity_tasks.py` — scale-free against the item's own rated intervals, on the configured detection metric, setting `is_plateau`, `is_acceleration`, and `plateau_interval_id` per [contracts/velocity-derivation.md](./contracts/velocity-derivation.md)
- [X] T028 [US2] Create `service/prefect/flows/velocity_derive.py` — the `velocity-derive` flow with `platform`, `content_ids`, `since`, and `validate_only` params; returns the standard `{start_time, end_time, data, summary, error}` dict and never raises; includes an `if __name__ == "__main__":` CLI block
- [X] T029 [US2] Populate the flow summary in `service/prefect/flows/velocity_derive.py`: `items_considered`, `intervals_derived`, `intervals_updated`, `intervals_withheld_too_short`, `accelerations_detected`, `items_insufficient_observations`, `config_version`
- [X] T030 [US2] Add the `velocity-derive` deployment to `service/prefect/prefect.yaml` — monthly cron at **06:00 WIB on the 2nd**, clear of the client harvests (17:30 on the 1st → 02:00 on the 2nd) and the competitor harvests (02:30–05:00 on the 2nd)
- [X] T031 [US2] Validate against the rehearsal clone per [quickstart.md](./quickstart.md) step 5, including the `per_day × days ≈ delta_likes` spot-check that catches elapsed time being ignored

**Checkpoint**: Velocity is derived, persisted, and re-runnable. US1 still works unchanged.

---

## Phase 5: User Story 3 - Absence is legible, never inferred (Priority: P3)

**Goal**: Every item without velocity resolves to exactly one classified reason, and an item that
has aged past listing depth says so rather than looking like a capture failure.

**Independent Test**: Construct one item per cause, query `velocity_status`, and confirm each
resolves to exactly one named reason with no overlap and zero `UNRESOLVED` rows.

**Note**: The `velocity_status` view ships in migration 008 (T004) because it is schema. This phase
implements the aged-out detection that makes `no_longer_observable` reachable, and asserts the
invariants.

### Tests for User Story 3

- [X] T032 [P] [US3] Write `service/prefect/tests/test_absence_classification.py` with `pytestmark = pytest.mark.schema`: `UNRESOLVED` returns **zero rows**; every item appears exactly once; `has_velocity` is true iff `reason='has_velocity'`; a legacy-only item never reports `observed_once`
- [X] T032a [P] [US3] Add tests to `service/prefect/tests/test_absence_classification.py` for the reach discriminator: an item published before the oldest returned item classifies `aged_out_of_listing`; one published after it classifies `absent_within_reach`; neither ever classifies `deleted` from a listing absence (FR-019)
- [X] T033 [P] [US3] Add tests to `service/prefect/tests/test_absence_classification.py` for branch ordering: an item with one `legacy` observation classifies as `legacy_only`, and one with one `captured` observation classifies as `observed_once` — the `captured_count = 0` term is what separates them

### Implementation for User Story 3

- [X] T034 [US3] Implement absence classification in `service/prefect/flows/common/social_harvest.py` — after listing a profile, compute `T` = the `published_at` of the **oldest item the listing actually returned**, then for each known item of that account absent from the response record a `capture_outcomes` row with `outcome='not_attempted'` and reason `aged_out_of_listing` when published before `T`, or `absent_within_reach` when published after `T`. Use the oldest *returned* item, not a rank against `list_depth` — the listing may return a short page. **Never record `deleted` from a listing absence**; that reason is reserved for an explicit platform not-found on the download path. Needs no reason-vocabulary change, because `chk_capture_outcomes_reason` constrains reasons only when `outcome='failed'`
- [X] T035 [US3] Record a classified reason in `service/prefect/flows/common/social_harvest.py` when a listing fails outright (block, throttle, parse failure) so the miss is distinguishable from an attempt never made (FR-018, FR-020)
- [X] T036 [US3] Add an `aged_out` counter to the harvest run summary in `service/prefect/flows/common/social_harvest.py`, so the ~252 unreachable rows measured in [research.md](./research.md) R2 are visible as a reported fact rather than silence
- [X] T037 [US3] Validate against the rehearsal clone per [quickstart.md](./quickstart.md) step 6 — confirm zero `UNRESOLVED` rows and zero `legacy_only` items carrying velocity

**Checkpoint**: All three stories functional. Every absence has exactly one reason.

---

## Phase 6: Polish & Cross-Cutting Concerns

- [X] T038 [P] Update `.claude/rules/backend/schema.md` — document `metric_observations` and `velocity_intervals`, the `velocity_status` view, the `metric_refresh` capture kind, and add the new flow/task names to the Naming section
- [X] T039 [P] Update `.claude/CLAUDE.md` with a "Longitudinal Metrics" section: `velocity-derive` commands, `--validate-only`, the backfill invocation, and the operator query from [contracts/absence-classification.md](./contracts/absence-classification.md)
- [X] T040 [P] Document in `.claude/CLAUDE.md` that re-observability is bounded by listing depth (~30 items/account), that roughly 30% of the existing corpus is permanently frozen, and that extending depth is deliberately out of scope
- [X] T041 Verify analysis output survived across the full rehearsal cycle per [quickstart.md](./quickstart.md) step 7 — compare `has_summary`/`has_flow` counts before and after; any decrease is an FR-010a regression and a hard stop
- [X] T042 Run the full test suite: `cd service/prefect && ./.venv/Scripts/python.exe -m pytest tests/ -m schema -v` plus the three new modules. Confirm the schema tests actually ran rather than auto-skipping on an unreachable database
- [X] T043 Run `script/verify_schema_parity.sh` once more after all edits and confirm exit 0
- [X] T044 Apply to production: `docker exec -i postgres psql -U noktah -d noktah_dashboard < config/postgres/migrations/008_observation_history.sql`, then run the legacy backfill, then `docker exec prefect prefect deploy --all`
- [X] T045 Tear down the rehearsal clone: `script/db_rehearsal.sh drop`
- [X] T046 Record the post-deployment baseline — `velocity_status` distribution (expect near-total `legacy_only` on day one, which is correct) and the seeded observation count, so next month's cycle has something to compare against

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: no dependencies
- **Foundational (Phase 2)**: needs Setup — **blocks all user stories**
- **US1 (Phase 3)**: needs Foundational
- **US2 (Phase 4)**: needs Foundational. Independent of US1 in code, but has no real data to derive
  from until US1 has run at least twice — see the timing note below
- **US3 (Phase 5)**: needs Foundational. T034 edits the same file as US1's T017, so schedule after
  US1 to avoid a conflict
- **Polish (Phase 6)**: needs all desired stories

### Critical path

```
T001-T003 → T004-T008 (schema) → T009-T013 (observation store)
          → T014-T022 (US1, MVP)
          → T023-T031 (US2)   → T032-T037 (US3) → T038-T046
```

### Timing reality — read before planning a demo

US2 and US3 are code-complete on day one but **cannot be fully validated until a second observation
exists**, which arrives with the next monthly harvest. On day one a correct system shows nearly
every item as `legacy_only` with no velocity. That is the expected state, not a failure. Re-run
quickstart steps 5 and 6 after the next cycle.

### File-conflict constraints (why some tasks are not [P])

- `service/prefect/flows/common/social_harvest.py` — T017, T018, T020, T021, T034, T035, T036 all
  edit it. Strictly sequential.
- `service/prefect/tasks/social_tasks.py` — T010 and T019. Sequential.
- `service/prefect/tasks/velocity_tasks.py` — T002, T011, T026, T027. Sequential.
- `service/prefect/tests/test_metric_observation.py` — T009 and T012. Sequential.
- `service/prefect/tests/test_velocity_derivation.py` — T023, T024, T025. T023 and T024 are marked
  [P] as independent test-writing units; if one author owns the file, run them in order.
- `service/prefect/tests/test_social_harvest.py` — T014, T014a, T016. Sequential; only T015 (a
  different module) is parallel with them.
- `service/prefect/tests/test_absence_classification.py` — T032, T032a, T033. Marked [P] as
  independent test-writing units; serialize if one author owns the file.

### Parallel Opportunities

- T002 and T003 (Setup)
- T015 and T016 alongside T014 (different test modules)
- T023 and T024 (independent test units)
- T032 and T033 (independent test units)
- T038, T039, T040 (three different documentation files)

---

## Parallel Example: Phase 1 + early Phase 2

```bash
# Setup, in parallel:
Task: "Create service/prefect/tasks/velocity_tasks.py with config constants"
Task: "Create the rehearsal database clone via script/db_rehearsal.sh create"

# Documentation polish, in parallel:
Task: "Update .claude/rules/backend/schema.md with the new tables and naming"
Task: "Update .claude/CLAUDE.md with velocity-derive commands"
Task: "Document the listing-depth ceiling in .claude/CLAUDE.md"
```

---

## Implementation Strategy

### MVP scope — US1 only (T001–T022, including T011a and T014a)

Delivers the whole point of the feature: engagement stops being frozen, and the store starts
accumulating a history that cannot be reconstructed later. Every month this is not shipped is a
month of observations permanently lost, and roughly 30% of the corpus has already been lost this way
(research.md R2). US2 and US3 derive value from that history and can follow without re-collecting
anything.

1. Phase 1 → Phase 2 → Phase 3
2. **STOP and VALIDATE**: quickstart steps 4 and 7 — confirm zero extra requests, zero model spend,
   and no analysis loss
3. Ship. The history starts accruing immediately

### Incremental delivery

1. Setup + Foundational → observation store live, corpus seeded
2. US1 → observations accumulate (MVP) → ship
3. US2 → velocity derivable → ship
4. US3 → absence fully classified → ship
5. Polish → docs, production apply, baseline recorded

### Highest-risk tasks

- **T019 / T041** — the FR-010a analysis-preservation path. This is the one place the feature can
  destroy data it cannot regenerate, and it fails silently ([research.md](./research.md) R8).
- **T017** — iterating `items` instead of `all_items` would leave the feature technically working
  and practically useless. T016 exists specifically to catch that.
- **T006** — `init.sql` parity drifts the moment it is edited alone; T008 and T043 both check it.
- **T014a** — the failure-retry purge is the one place observation history could be destroyed by a
  change made for entirely unrelated reasons. Nothing currently reaches the observation store, and
  nothing should start; this test is the only thing that will say so.

---

## Notes

- `[P]` = different files, no dependencies on incomplete tasks
- Commit after each task or logical group
- Schema-marked tests auto-skip without a reachable database — a green run that skipped everything
  proves nothing (T042 checks this explicitly)
- Never glob the migrations directory: `008_*.sql` also matches `008_*.down.sql`
