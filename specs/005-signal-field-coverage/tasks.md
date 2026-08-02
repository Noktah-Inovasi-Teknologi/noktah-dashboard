---

description: "Task list for 005-signal-field-coverage"
---

# Tasks: Signal Field Coverage

**Input**: Design documents from `/specs/005-signal-field-coverage/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md), [data-model.md](./data-model.md), [contracts/](./contracts/)

**Tests**: **Included and mandatory** — not by the template's optional-test rule, but because
`.claude/rules/backend/schema.md` requires schema and constraint work to be tested against a real
disposable PostgreSQL database ("partial unique indexes, the `NOT VALID` → `VALIDATE` sequence, and
cross-source name reconciliation are exactly the class of bug that passes against a mock and fails
against Postgres"). All such tests carry `pytestmark = pytest.mark.schema`.

**Organization**: Tasks grouped by user story. US1 is the MVP and unblocks the recording of every
other story's outcome.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: US1–US5, mapping to spec.md user stories
- Exact file paths included

## Path Conventions

Repo root is `c:\Users\bagas\Documents\GitHub\noktah-dashboard`. Paths below are repo-relative.
Prefect service code lives in `service/prefect/`, collection in `service/roach/`, schema in
`config/postgres/`.

---

## ⚠️ Read before starting

Four rules from the plan and project constitution that this task list encodes. Violating any of
them is a review-blocking error, not a style preference.

1. **Phase 7 (US2 implementation) is CONDITIONAL.** It exists only if T041's probe returns a
   positive result. A negative result means T042–T046 are **deleted**, and that is a *successful*
   outcome (FR-011) — not a blocked one.
2. **T038 must never become a probe.** The request baseline is read off a harvest that was going to
   run anyway. A task that runs a harvest *in order to* measure costs requests to count requests.
3. **Never glob `config/postgres/migrations/`.** `007_*.sql` also matches `007_*.down.sql`;
   applying both back-to-back silently undoes the migration.
4. **roach source is baked into its image**, not volume-mounted. Any `collect.py` edit needs
   `docker-compose up -d --build roach`.

> **Numbering note**: T065–T067 were added by `/speckit-analyze` remediation and are placed in
> their correct phases rather than at the end. Existing IDs were deliberately **not** renumbered —
> renumbering would invalidate every cross-reference in this file, plan.md, and quickstart.md.
> Execute by phase order, not by ID order. Total: **67 tasks**.

---

## Phase 1: Setup

**Purpose**: Rehearsal environment and the tracked location for the declarative source

- [X] T001 Create the rehearsal database with `script/db_rehearsal.sh create` and confirm it clones `noktah_dashboard`, so no migration in this feature is ever first applied to production
- [X] T002 [P] Create `config/field_availability.yaml` with only the `version: 1` header and an empty `determinations:` list, and verify with `git check-ignore -v config/field_availability.yaml` that it is **tracked** (repo-root `data/` is gitignored at `.gitignore:111` and would silently break FR-001a)
- [X] T003 [P] Confirm `SPINE_TEST_DATABASE_URL` reaches a Postgres server so `pytest -m schema` runs rather than auto-skipping (see `service/prefect/tests/conftest.py`)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Schema for both new records plus the `shares` column. **Blocks every user story.**

**⚠️ CRITICAL**: No user story work can begin until this phase is complete and schema parity passes.

- [X] T004 Write `config/postgres/migrations/007_signal_field_coverage.sql` adding `harvested_signals.shares BIGINT NULL`, the `field_availability` table, and the `capture_outcomes` table per [data-model.md](./data-model.md) — idempotent (`IF NOT EXISTS`, `pg_constraint` guards for named constraints), additive only, wrapped in `BEGIN; … COMMIT;`, ending with the `schema_migrations` self-record insert
- [X] T005 Write `config/postgres/migrations/007_signal_field_coverage.down.sql` dropping the two new tables and the `shares` column — safe here (unlike the 001–003 baselines) because all three are created by this migration and hold no pre-existing data
- [X] T006 Mirror every T004 change into `config/postgres/init.sql` in the same order, matching **constraint names exactly** (named `ALTER … ADD CONSTRAINT`, never an inline `REFERENCES`) and matching nullability semantics, per `.claude/rules/backend/schema.md`
- [X] T007 Apply T004 to the rehearsal database only: `docker exec -i postgres psql -U "$POSTGRES_USER" -d spine_rehearsal < config/postgres/migrations/007_signal_field_coverage.sql`, then re-apply it to prove idempotence
- [X] T008 Run `script/verify_schema_parity.sh` and confirm exit 0 — this is the check that catches an `init.sql` mirror written with different constraint names from the migration
- [X] T009 [P] Write `service/prefect/tests/test_field_availability.py` with `pytestmark = pytest.mark.schema`, asserting the five-value `status` CHECK **rejects** a sixth value at the database boundary (FR-002a) and that `UNIQUE (platform, content_type, field_name)` holds
- [X] T010 [P] Write `service/prefect/tests/test_capture_outcome.py` with `pytestmark = pytest.mark.schema`, asserting the `outcome` and `capture_kind` CHECKs reject unknown values and that a row survives insert without a `harvested_signals` row existing (the keying decision in [data-model.md §3](./data-model.md))
- [X] T011 Extend `service/prefect/tests/test_schema_parity.py` coverage by running `cd service/prefect && ./.venv/Scripts/python.exe -m pytest tests/ -m schema -v` and confirming all schema-marked tests pass

**Checkpoint**: Schema exists in rehearsal, both paths agree, constraints proven enforced. User stories may begin.

---

## Phase 3: User Story 1 - Tell a platform limit apart from a collection failure (Priority: P1) 🎯 MVP

**Goal**: Every empty engagement value resolves to exactly one cause — a platform limit, a
deliberate decision, or a classified capture failure.

**Independent Test**: Run the `why_empty` query in [data-model.md §6](./data-model.md); zero rows
return `UNRESOLVED — this row is a bug` for post-feature content. Instagram carousel `views`
reports a platform limit while a Reel whose enrichment failed reports an attempted-but-failed
capture.

### Tests for User Story 1

- [X] T012 [P] [US1] Write parser/validation tests for the YAML source in `service/prefect/tests/test_field_availability.py` — reject unknown `status`, empty `reason`, duplicate `(platform, content_type, field)`, and a non-`undetermined` entry with no `evidence`, per [contracts/field-availability.md §2](./contracts/field-availability.md)
- [X] T013 [P] [US1] Write a test in `service/prefect/tests/test_field_availability.py` asserting a **parse failure leaves the existing table untouched** — a determination silently reverting to a stale value is worse than a visibly failed sync
- [X] T014 [P] [US1] Write a test in `service/prefect/tests/test_capture_outcome.py` asserting `no_match` and `failed` are stored distinctly and that `failed` without a `reason` is rejected rather than defaulted (FR-004)
- [X] T065 [P] [US1] Write a test in `service/prefect/tests/test_capture_outcome.py` asserting **append-only** behaviour (FR-003a): two capture attempts for the same `(platform, content_id, capture_kind)` produce **two rows** with distinct `observed_at`, not one updated row. data-model.md §3 states this is "enforced by convention + test" — without this test the convention is the only thing holding, and an `ON CONFLICT DO UPDATE` added later would pass every other test
- [X] T066 [P] [US1] Add a **sync idempotence** assertion to `service/prefect/tests/test_field_availability.py` (FR-001b, [contracts/field-availability.md §3](./contracts/field-availability.md)): running the sync twice against an unchanged YAML yields `inserted: 0, updated: 0` and every row reported `unchanged` on the second pass

### Implementation for User Story 1

- [X] T015 [US1] Seed `config/field_availability.yaml` from the **measured** R0 table in [research.md](./research.md) — including Instagram `story` as `unavailable_platform_limit` across all four fields, and TikTok `carousel`/`image`/`story` as `undetermined` with "0 rows ever observed" as the reason (Constitution VIII forbids asserting availability from an unexercised code path)
- [X] T016 [US1] Create `service/prefect/tasks/availability_tasks.py` with `@task(name="availability.determination.sync")` — parses and validates the YAML, upserts on `(platform, content_type, field_name)`, and **never deletes rows absent from the file** without an explicit flag
- [X] T017 [US1] Add `@task(name="availability.determination.get")` to `service/prefect/tasks/availability_tasks.py` returning the determination or `None`; document in its docstring that `None` means `undetermined` and MUST NOT be inferred from stored values
- [X] T018 [US1] Create `service/prefect/flows/field_availability_sync.py` — flow `field-availability-sync`, async, returns the standard `{start_time, end_time, data, summary, error}` dict, never raises (Constitution I), supports `--validate-only`
- [X] T019 [US1] Implement `summary.coverage_gaps` in `service/prefect/flows/field_availability_sync.py` — enumerate every `(platform, content_type, field)` present in `harvested_signals` with no determination. This is the value SC-001 is measured against
- [X] T020 [US1] Create `@task(name="social.capture.record-outcome")` in `service/prefect/tasks/social_tasks.py` — **insert-only**, no `ON CONFLICT DO UPDATE` (FR-003a), validating `outcome`/`capture_kind`/`reason` against the closed vocabularies in [contracts/capture-outcome.md](./contracts/capture-outcome.md) and raising on violation
- [X] T021 [US1] Wire capture-outcome writes into `service/prefect/flows/common/social_harvest.py` around the roach `/list` result — record `success`, `no_match`, `failed`, or `not_attempted` per item per capture kind, best-effort so a recording failure never aborts a harvest
- [X] T022 [US1] Implement `no_match` specifically for the Instagram Reels pass in `service/prefect/flows/common/social_harvest.py` — a carousel absent from the shortcode-keyed clips response is `no_match`, **not** `failed` (this is the live case that makes a normal post look like an error)
- [X] T023 [US1] Register a `field-availability-sync` deployment in `service/prefect/prefect.yaml` (unscheduled — determinations change rarely and by human decision) and redeploy with `docker exec prefect prefect deploy --all`
- [X] T024 [US1] Run `docker exec prefect python flows/field_availability_sync.py --validate-only` then without the flag; confirm `rejected: 0` and `coverage_gaps: []`
- [X] T025 [US1] Verify the closed vocabulary is enforced **in the database**, not just the task, using the direct `INSERT` in [quickstart.md §3](./quickstart.md) — it must fail on the CHECK constraint
- [X] T026 [US1] Run the SC-008 query in [quickstart.md §4](./quickstart.md) and confirm all 377 Instagram non-video rows resolve to `unavailable_platform_limit` for `views` rather than counting as collection failures

**Checkpoint**: US1 fully functional. Absence is legible; every later story's outcome is now recordable.

---

## Phase 4: User Story 3 - Share counts reach the analyst (Priority: P3, sequenced 2nd)

**Goal**: TikTok share counts — already parsed by roach and discarded at three boundaries — reach
storage and the reviewer surface. Marginal collection volume: **zero**.

**Sequenced ahead of US2** because it is fully deterministic with no investigation dependency,
while US2 is gated on a probe. Spec priority is unchanged.

**Independent Test**: Harvest a TikTok account; every video row carries a share count, the reviewer
sheet shows it as the trailing column, `advertisement` still round-trips through
`social-harvest-sync`, and the run's request count is unchanged.

### Tests for User Story 3

- [X] T027 [P] [US3] Write a test in `service/prefect/tests/test_social_tasks.py` asserting `social_signal_record` persists `shares`, and that a share count of `0` is stored as `0` and never conflated with `NULL` (Edge Case "A field is present but zero")
- [X] T028 [P] [US3] Write a test in `service/prefect/tests/test_social_harvest.py` asserting header resolution is **by name**, and that a tab whose header lacks `shares` does not receive misaligned row data (FR-016b)

### Implementation for User Story 3

- [X] T029 [US3] Add a `shares` parameter to `social_signal_record` in `service/prefect/tasks/social_tasks.py` and include it in the `INSERT … ON CONFLICT (platform, content_id) DO UPDATE` column list
- [X] T030 [US3] Pass `counts.get("shares")` at the `social_signal_record` call site in `service/prefect/flows/common/social_harvest.py` (around line 598, alongside the existing `views`/`likes`/`comments`)
- [X] T031 [US3] Append `"shares"` to `ACCOUNT_HEADER` in `service/prefect/flows/common/social_harvest.py` as the **trailing** column — never mid-layout, which would shift `advertisement`, the one column a human edits and the sync reads back. `DETAIL_HEADER` inherits it; verify `CONTENT_ID_COL` is unaffected
- [X] T032 [US3] Add the share value to the row-build list in `service/prefect/flows/common/social_harvest.py` (around line 170) in trailing position matching the header
- [X] T033 [US3] Extend `ensure_tab` in `service/prefect/blocks/google_credentials.py` to **extend an existing tab's header** to the current layout — it currently writes the header only when creating a tab (line 664), so existing tabs would never gain the column
- [X] T034 [US3] Add `@task(name="google.sheets.ensure-header")` to `service/prefect/tasks/google_tasks.py` wrapping T033, so the delivery path and the backfill flow share one definition
- [X] T035 [US3] Create `service/prefect/flows/sheet_header_backfill.py` — one-off flow `sheet-header-backfill`, idempotent, **touches the header row only**, and **skips and reports** any tab whose header does not match the expected previous layout rather than overwriting it
- [X] T036 [US3] Audit `service/prefect/flows/social_harvest_sync.py` (advertisement read, around line 104) and confirm every reviewer-surface read resolves columns by header name and **raises** on a missing expected column rather than defaulting to an index (FR-016b)
- [X] T037 [US3] Run `docker exec prefect python flows/sheet_header_backfill.py --validate-only`, then apply; confirm `tabs_skipped` is empty or each skip is explained
- [X] T038 [US3] Add per-pass request-count logging to `service/roach/collect.py` and surface it in the harvest summary; **read the baseline off the next already-scheduled harvest** — do not run a harvest to measure one (FR-025). Record the measured figure in [research.md](./research.md) R5, replacing the ~7–10 code-derived estimate
- [ ] T039 [US3] *(BLOCKED: awaiting the next scheduled TikTok harvest — no TikTok account has been collected since the rebuild, so `shares` has no row to appear on yet.)* After the next TikTok harvest, run the SC-003 query in [quickstart.md §6](./quickstart.md); confirm `with_shares = rows` for video and that the run's request count is unchanged from the T038 baseline

**Checkpoint**: US1 and US3 both work independently. Share counts visible end to end at zero collection cost.

---

## Phase 5: User Story 4 - Instagram follower count as a trustworthy series (Priority: P4)

**Goal**: Verify the existing capture and series, and make a *missed* capture queryable per account
rather than trapped in a run summary.

**May close with no production-code change** if verification finds capture healthy and misses
already recorded — that is a legitimate outcome (plan.md). If verification finds capture *failing*,
T067 is the repair path; do not assume the healthy branch.

**Independent Test**: Harvest Instagram accounts, then query the follower series: one timestamped
observation per account per run, or a classified miss. No account silently absent from both.

### Tests for User Story 4

- [X] T040 [P] [US4] Write a test in `service/prefect/tests/test_social_harvest.py` asserting that a `None` follower count produces a `capture_outcomes` row with `capture_kind='instagram_profile_info'` and a classified reason, and writes **nothing** to `account_follower_observations` (FR-020: never zero, never carried forward)

### Implementation for User Story 4

- [X] T041 [US4] Verify current behaviour: run the follower-series query in [contracts/capture-outcome.md §5](./contracts/capture-outcome.md) against production and record which Instagram accounts have observations, which do not, and whether the GraphQL fallback is carrying them (`web_profile_info` has been 400ing since 2026-07-31)
- [X] T042 [US4] Route the existing `follower_capture_missed` list in `service/prefect/flows/common/social_harvest.py` (lines 447–456) into `social.capture.record-outcome` with `capture_kind='instagram_profile_info'`, `account_id` set, and a classified reason — so a miss becomes queryable state rather than run-summary-only text (FR-018)
- [X] T043 [US4] Confirm no interpolation or carry-forward exists on the observation path in `service/prefect/tasks/social_tasks.py` — `account_follower_observations.follower_count` is an **observation**; the constitution's interpolatable "follower count at post time" is a *different, derived* field and must never be written back here (plan.md, research R3)
- [X] T044 [US4] Verify FR-019 by querying two observations of the same account and confirming change is derivable from timestamps alone, with no assumption of even spacing
- [X] T067 [US4] ~~**Conditional on T041.**~~ **NOT NEEDED — verified 2026-08-02.** T041 found follower capture healthy: 11 Instagram accounts carry observations, `lasikasyik` re-measured 1342 -> 1343 across two runs, and the GraphQL fallback is carrying them while `web_profile_info` still 400s. No repair required. Original task: **Conditional on T041.** If T041 finds follower capture *failing* rather than healthy, repair the resolution path in `service/roach/collect.py` — `_instagram_profile_info` (line 598) has been receiving a per-account `400` from `web_profile_info` since 2026-07-31, and `_instagram_profile_info_graphql` (line 671) is a 2026-08-01 fallback with one day of evidence behind it. Re-check the `IG_PROFILE_RELAY_FLAGS` set against instaloader's `Profile._obtain_metadata` **before** assuming `IG_PROFILE_DOC_ID` rotated (roach.md records that assuming rotation cost two wasted probes). Any probing here counts against the same 10-request budget as T046 (FR-012b). If T041 finds capture healthy, delete this task



**Checkpoint**: Follower series verified; misses queryable and distinguishable from never-attempted.

---

## Phase 6: User Story 2 - Investigation (Priority: P2) — the gate

**Goal**: Determine, on evidence, whether Instagram publishes a comment count for feed posts and
carousels through a surface already in use. **The determination is the deliverable**, positive or
negative.

**Independent Test**: A determination exists for `(instagram, carousel, comments)` and
`(instagram, image, comments)` with its evidence and probe count recorded.

- [X] T045 [US2] Re-confirm the offline evidence chain in [research.md](./research.md) R1 before any live request (FR-012a): gallery-dl 1.32.6 `extractor/instagram.py:1151` routes the posts listing to `/v1/feed/user/`, `:240` maps `like_count` only, and `service/roach/collect.py:767` already reads `comment_count` off the same media shape from the clips endpoint
- [X] T046 [US2] **LIVE PROBE — the only network-touching task in this feature.** Budget ≤10 requests, expected 1, against one designated **non-client** account. Determine whether one page of `/api/v1/feed/user/<user_id>/` returns `comment_count` for carousel/image items, and whether it also returns `play_count` for video. **Stop immediately on any 429 or challenge** and record `inconclusive` (FR-012c)
- [X] T047 [US2] Record the outcome in `config/field_availability.yaml` for `(instagram, carousel|image, comments)` with the **actual probe count** in `evidence` (FR-012b), then re-run `field-availability-sync`
- [X] T048 [US2] State the marginal collection volume implied by the outcome — Outcome A (subsumes the clips pass) ≤0; Outcome B +1–2 per account per run against the T038 measured baseline; Outcome C zero — and obtain acceptance **before** any Phase 7 work begins (FR-024)

**Checkpoint**: The determination exists. If Outcome C, US2 is **complete** — skip Phase 7 entirely and delete it (FR-011).

---

## Phase 7: User Story 2 - Implementation (CONDITIONAL on T046)

**⚠️ This phase exists only if T046 returned Outcome A or B.** On Outcome C, delete T049–T053; that
is a successful outcome, not a blocked one.

- [X] T049 [US2] Add `_instagram_feed_stats` to `service/roach/collect.py` following the established `_instagram_clip_stats` pattern (line 725) — reuse `_instagram_api_session`, the per-profile fingerprint, and curl_cffi impersonation. **Never plain `requests`/`httpx`** (roach rule #1). Best-effort: return `{}` on any failure so a hiccup never breaks the listing
- [X] T050 [US2] Patch the resulting comment counts onto items in `_list_instagram` in `service/roach/collect.py` (alongside the existing clip-stats patch at lines 1000–1014), keyed by shortcode, without overwriting a value already present
- [X] T051 [US2] **If Outcome A** (feed endpoint also returns `play_count`): replace the `_instagram_clip_stats` call in `service/roach/collect.py` with the single feed pass, making the change net request-negative. **If Outcome B**: run both passes and accept the stated +1–2
- [X] T052 [US2] Add fixture-based parsing tests to `service/roach/tests/test_collect.py` using a captured payload — **no live calls in tests**
- [X] T053 [US2] Rebuild roach (`docker-compose up -d --build roach`) — source is baked into the image, not volume-mounted — then verify comment counts appear on newly harvested carousel and image rows and that the per-account request count matches the T048 accepted figure

**Checkpoint**: Instagram non-video comment coverage closed, within the accepted request budget.

---

## Phase 8: User Story 5 - A recorded decision on comment text (Priority: P5)

**Goal**: A written, evidence-backed determination that stops the question being reopened. **No
collection code is written** (FR-022).

**Independent Test**: The determination states marginal requests per post and per account per run
as a multiple of the measured baseline, and issues an explicit recommendation.

- [X] T054 [US5] Update [research.md](./research.md) R6 to express comment-text cost against the **T038 measured** baseline rather than the ~7–10 estimate, keeping the per-post structural argument intact
- [X] T055 [US5] Record `comment_text` in `config/field_availability.yaml` as `not_collected_by_decision` for every Instagram and TikTok content type — **never `unavailable_platform_limit`** (FR-023): it is obtainable, and recording a policy choice as a platform law would make it look irreversible
- [X] T056 [US5] Re-run `field-availability-sync` and confirm the comment-text determination is queryable with its reason and a pointer to research R6

**Checkpoint**: All five stories complete.

---

## Phase 9: Polish & Cross-Cutting Concerns

- [X] T057 Apply migration 007 to production `noktah_dashboard` **only after** the full rehearsal in T007–T008 passed, using the explicit filename — never a glob (`007_*.sql` also matches `007_*.down.sql`)
- [X] T058 [P] Update `.claude/rules/backend/roach.md` with the `/v1/feed/user/` finding and the gallery-dl mapping gap, so the next person does not re-derive it — including the negative result if T046 returned Outcome C
- [X] T059 [P] Update `.claude/rules/backend/schema.md` naming section with the new tasks (`availability.determination.sync`, `social.capture.record-outcome`, `google.sheets.ensure-header`) and the new tables
- [X] T060 [P] Update `.claude/CLAUDE.md` with the `field-availability-sync` and `sheet-header-backfill` commands
- [X] T061 Correct the stale figures in `docs/AUDIT.md` §3 — 696 rows not 656, 377 Instagram non-video not 361, TikTok `shareCount` already parsed, Instagram follower capture already implemented
- [X] T062 Run the full validation sequence in [quickstart.md](./quickstart.md) steps 1–8
- [X] T063 Run `cd service/prefect && ./.venv/Scripts/python.exe -m pytest tests/ -v` and confirm no regression in the non-schema suite
- [X] T064 Verify SC-002 end to end: the `why_empty` query in [data-model.md §6](./data-model.md) returns **zero** `UNRESOLVED` rows for post-feature content, with pre-feature rows correctly reporting `provenance unknown` rather than success (FR-006)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: no dependencies
- **Phase 2 (Foundational)**: depends on Phase 1 — **BLOCKS all user stories**
- **Phase 3 (US1)**: depends on Phase 2. Blocks nothing technically, but every other story's
  *outcome recording* depends on it, so it is the MVP
- **Phase 4 (US3)**: depends on Phase 2; independent of US1 in code, though T038's baseline feeds
  T048 and T054
- **Phase 5 (US4)**: depends on Phase 2 and on T020 (`social.capture.record-outcome`)
- **Phase 6 (US2 investigation)**: depends on Phase 2; T048 depends on T038's measured baseline
- **Phase 7 (US2 implementation)**: **conditional on T046**
- **Phase 8 (US5)**: depends on T038 (baseline) and T016 (sync task)
- **Phase 9 (Polish)**: depends on all desired stories

### Critical path

```
T001–T003 → T004–T008 (schema + parity) → T015–T024 (US1) → T064
                                       ↘ T038 (baseline) → T048 → [T049–T053?]
```

### Parallel Opportunities

- T002, T003 in parallel
- T009, T010 in parallel (different test files)
- T012, T013, T014, T065, T066 in parallel
- T027, T028 in parallel
- T058, T059, T060 in parallel (different docs)
- After Phase 2: US1, US3, and the US2 *investigation* can proceed in parallel by different people

---

## Parallel Example: Phase 2 tests

```bash
# Different files, no shared state:
Task: "Write service/prefect/tests/test_field_availability.py (T009)"
Task: "Write service/prefect/tests/test_capture_outcome.py (T010)"
```

---

## Implementation Strategy

### MVP (User Story 1 only)

1. Phase 1 Setup → Phase 2 Foundational → Phase 3 US1
2. **STOP and VALIDATE**: run the `why_empty` query; the existing 696 rows become interpretable and
   the 377 Instagram non-video rows stop reading as collection failures
3. This is shippable on its own and is the highest-value slice — it costs zero requests and turns
   an ambiguous corpus into a legible one

### Incremental delivery

1. Foundation → US1 (MVP, zero collection risk)
2. + US3 — share counts, zero collection risk
3. + US4 — follower misses queryable, zero collection risk
4. + US2 investigation — the only network-touching step, ≤10 requests
5. + US2 implementation *if warranted*, or its recorded absence
6. + US5 — a recorded decision

Steps 1–3 and 6 carry **no collection risk whatsoever**. Only step 4 touches a platform, and only
step 5 changes ongoing collection volume — and step 5 does not exist unless the probe warrants it.

---

## Notes

- `[P]` = different files, no incomplete dependencies
- Every schema-touching test carries `pytestmark = pytest.mark.schema` and needs a real Postgres
- Commit after each task or logical group
- **A negative T046 result is a completed deliverable.** Do not treat Phase 7's deletion as failure
