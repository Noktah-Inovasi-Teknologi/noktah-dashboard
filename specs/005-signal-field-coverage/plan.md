# Implementation Plan: Signal Field Coverage

**Branch**: `005-signal-field-coverage` | **Date**: 2026-08-02 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/005-signal-field-coverage/spec.md`

## Summary

Make absence legible, then close the gaps that are cheap to close.

The corpus stores "the platform never publishes this" and "our enrichment call failed" as the same
`NULL`, so no consumer can tell a collection regression from a platform limit. This feature adds
two records that resolve that — a version-controlled **field availability determination** per
(platform, content type, field), and an append-only **capture outcome** per observation per
supplementary pass — and then uses them to land the specific gaps the brief named.

Research (Phase 0) changed the shape of the work substantially:

- **Share counts need no collection work.** roach already parses TikTok `shareCount`; the value is
  discarded at three storage boundaries. Marginal requests: **zero**.
- **Follower capture needs no new mechanism.** It and its append-only series shipped with feature
  004. The real gap is that a *missed* capture lives only in a run summary, not as queryable
  per-account state.
- **Instagram non-video comment counts are probably obtainable at near-zero cost.** gallery-dl
  fetches `/v1/feed/user/` and maps `like_count` while silently dropping `comment_count`, a field
  this codebase already reads off the same media shape elsewhere. One capped probe decides it — and
  may make the change *net request-negative* by subsuming the existing Reels pass.
- **Comment text is recommended against**, on a structural per-post cost of ≈4–10× baseline.

## Technical Context

**Language/Version**: Python 3.14

**Primary Dependencies**: Prefect 3.6.9, asyncpg, google-api-python-client (Prefect service);
FastAPI, gallery-dl 1.32.6, yt-dlp, curl_cffi (roach)

**Storage**: PostgreSQL 15 — `harvested_signals`, `accounts`, `runs`,
`account_follower_observations`; two new tables this feature. Google Sheets as the reviewer-facing
delivery surface.

**Testing**: pytest. Schema and constraint tests run against a **real disposable PostgreSQL
database** (`pytestmark = pytest.mark.schema`), per `.claude/rules/backend/schema.md`. Collection
parsing is unit-tested against captured payload fixtures — never against live platforms.

**Target Platform**: Linux containers via docker-compose

**Project Type**: Workflow-orchestration service (Prefect) + stateless collection microservice
(roach). No new service.

**Performance Goals**: Not latency-sensitive — batch collection. The governing budget is **request
volume, not time**: no increase over the measured per-account baseline (R5) except where stated in
advance and accepted (FR-024).

**Constraints**:
- No new authenticated surface; no increase in request rate (FR-025, Constitution X).
- Investigations capped at 10 live requests, offline evidence first, hard stop on throttle
  (FR-012a–c).
- Schema changes additive only; migration + `init.sql` parity enforced by
  `script/verify_schema_parity.sh`.
- roach source is **baked into its image, not volume-mounted** — any `collect.py` change needs
  `docker-compose up -d --build roach`.

**Scale/Scope**: 696 signal rows across 2 platforms × 5 content types; ~20 accounts on monthly
harvest cadence. The availability matrix is ~50 rows. Everything here is small-data; the risk is
correctness and collection politeness, not throughput.

## Constitution Check

*GATE: evaluated before Phase 0 and re-evaluated after Phase 1 design.*

| Principle | Gate | Status |
|---|---|---|
| **I. Prefect orchestration** | New automation expressed as flows/tasks; kebab-case flows, `api-group.resource.action` tasks; flows return the standard dict and never raise | **PASS** — `field-availability-sync`, `sheet-header-backfill`; tasks `availability.determination.sync`, `social.capture.record-outcome`, `google.sheets.ensure-header` |
| **II. Collection targets** | No platform API assumed; no `retries=2` on collection tasks; pacing centrally configured | **PASS** — no new scheduled collection task; the probe is a one-off manual investigation |
| **III. Docker-first** | Runs in existing containers; no new service | **PASS** — roach rebuild required and documented |
| **IV. Security** | No new credentials; nothing sensitive logged | **PASS** — reuses existing burner cookie session |
| **V. Observability** | Skipped/failed items recorded with a classified reason, never silently dropped | **PASS** — this is the feature's entire purpose; `capture_outcomes` is the mechanism |
| **VI. Data honesty** | No metric stored that cannot be observed; unavailable returned explicitly, never proxied | **PASS** — FR-005/FR-015/FR-020; see the interpolation note below |
| **VII. Append-only observations** | Observations immutable; misses recorded as classified missed attempts | **PASS** — `capture_outcomes` append-only (FR-003a); FR-006 marks pre-feature rows as lacking provenance |
| **VIII. Evidence discipline** | Claims carry their sample; no assertion from unobserved data | **PASS** — TikTok non-video availability recorded `undetermined` (0 rows observed) rather than inferred from the code path |
| **IX. Identity chain** | Traceability preserved | **PASS** — capture outcomes carry `run_id`; no attribution change |
| **X. Polite collection** | No rate increase, no new surface, no evasion; uncollectable is a reported fact | **PASS** — FR-012a–c cap the investigation; R6 declines comment text on exactly this ground |
| **XI. Model call governance** | — | **N/A** — no model calls |
| **XII. Versioned vocabularies, additive schema** | Vocabulary stored as data, not hardcoded; additive migrations | **PASS** — five-value status vocabulary as data (FR-002/FR-002a); migration 007 additive only |

**No violations. The Complexity Tracking table is intentionally omitted — there is nothing to
justify.**

### Two constitutional subtleties this design must not get wrong

1. **Interpolation.** The constitution's metric table says follower-count-*at-post-time* is derived
   and may be interpolated *if marked*; FR-020 forbids interpolating a follower count. These are
   different fields. `account_follower_observations.follower_count` is an observation and is never
   interpolated. Any at-post-time derivation belongs to a consumer and must never be written back
   into the observation table. See research R3.
2. **Comment text.** The metric table lists it as *Observable — store as observation*. That
   classifies what is knowable, not what is worth collecting. R6 declines it on collection-cost
   grounds under Principle X and records it as `not_collected_by_decision` — a decision, not a
   contradiction.

## Project Structure

### Documentation (this feature)

```text
specs/005-signal-field-coverage/
├── plan.md              # This file
├── research.md          # Phase 0 — R0-R6, all offline
├── data-model.md        # Phase 1
├── quickstart.md        # Phase 1
├── contracts/
│   ├── field-availability.md
│   ├── capture-outcome.md
│   └── delivery-surface.md
├── checklists/
│   └── requirements.md
└── tasks.md             # Phase 2 (/speckit-tasks — NOT created here)
```

### Source Code (repository root)

```text
config/postgres/
├── init.sql                                  # MIRROR every 007 change here (parity)
└── migrations/
    ├── 007_signal_field_coverage.sql         # + shares, field_availability, capture_outcomes
    └── 007_signal_field_coverage.down.sql

config/
└── field_availability.yaml                   # NEW — version-controlled source of truth (FR-001a)
                                              # NOT data/ — `data` is gitignored (.gitignore:111),
                                              # which would leave the source untracked and break FR-001a

service/roach/
├── collect.py                                # IG feed-stats pass (ONLY if the R1 probe confirms)
└── tests/test_collect.py                     # fixture-based, no live calls

service/prefect/
├── flows/
│   ├── field_availability_sync.py            # NEW — `field-availability-sync`
│   ├── sheet_header_backfill.py              # NEW — one-off `sheet-header-backfill`
│   └── common/social_harvest.py              # shares through-wiring; capture-outcome writes
├── tasks/
│   ├── availability_tasks.py                 # NEW — availability.determination.{sync,get}
│   ├── social_tasks.py                       # social.signal.record + shares; capture.record-outcome
│   └── google_tasks.py                       # google.sheets.ensure-header (name-based reads)
└── tests/
    ├── test_field_availability.py            # NEW (schema-marked)
    ├── test_capture_outcome.py               # NEW (schema-marked)
    └── test_social_harvest.py                # extended

script/
└── verify_schema_parity.sh                   # MUST pass after 007
```

**Structure Decision**: No new service. The work lands in three existing places — the Postgres
schema (two tables, one column), the Prefect service (two small flows, task extensions), and
optionally roach (one enrichment pass, gated on the R1 probe). A version-controlled
`config/field_availability.yaml` is the declarative source clarified in Q1; the Prefect flow
reconciles it into Postgres so determinations are joinable against `harvested_signals` in SQL.

**Why `config/` and not `data/`**: `.gitignore:111` ignores any directory named `data`, so a
`data/field_availability.yaml` would be untracked — silently defeating FR-001a's entire purpose
(a determination reviewable as a diff). `config/` is tracked and already holds `config/postgres/`.

## Implementation Sequence

Ordered so that each step is independently verifiable and no step depends on an unresolved
investigation.

| # | Step | Story | Marginal requests | Gate |
|---|---|---|---|---|
| 1 | Migration 007 + `init.sql` mirror: `harvested_signals.shares`, `field_availability`, `capture_outcomes` | US1/US3 | 0 | `verify_schema_parity.sh` passes |
| 2 | `data/field_availability.yaml` seeded from R0 measured coverage + `field-availability-sync` flow | US1 | 0 | Every (platform, content type, field) resolves; no `undetermined` except TikTok non-video |
| 3 | Capture-outcome writes from the harvest engine (incl. `no_match` for the Reels pass) | US1 | 0 | Empty values on new rows resolve to one cause |
| 4 | Share counts through `social.signal.record` → `harvested_signals` | US3 | 0 | TikTok video rows carry shares |
| 5 | `ACCOUNT_HEADER` + `shares`, name-based reads, `sheet-header-backfill` one-off | US3 | 0 | Sync still reads `advertisement` correctly on backfilled and fresh tabs |
| 6 | Request-count instrumentation; **read the number off the next scheduled harvest** | FR-024 | 0 | Baseline recorded in research.md |
| 7 | Route `follower_capture_missed` into `capture_outcomes` with classified reasons | US4 | 0 | Miss distinguishable from never-attempted by query |
| 8 | **R1 probe** (≤10 requests, expected 1) on a designated non-client account | US2 | ≤10 one-off | Outcome A/B/C recorded as a determination |
| 9 | *Conditional on 8:* IG feed-stats pass in roach + rebuild | US2 | ≤0 to +2/account/run | Stated and accepted per FR-024 before merge |
| 10 | Record the R6 comment-text determination as `not_collected_by_decision` | US5 | 0 | Determination queryable |

Steps 1–7 and 10 are **fully deterministic and carry zero collection risk**. Only step 8 touches a
platform, and only step 9 changes ongoing collection volume — and step 9 does not exist unless the
probe says it should.

## Phase 2 note

`/speckit-tasks` will decompose the above. Two things it must preserve:

- **Step 9 is conditional.** Tasks for it must be written as blocked on the step-8 determination,
  not as assumed work. A negative probe result means step 9 is *deleted*, and that is a successful
  outcome (FR-011), not a failure.
- **Step 6 must not become a probe.** The baseline is read off a run that was going to happen
  anyway. A task that says "run a harvest to measure the baseline" spends requests to count
  requests and should be rejected in review.

## Post-Design Constitution Re-Check

Re-evaluated after data-model.md and contracts/ were written. **Still no violations.**

Two design decisions were specifically checked against the constitution and adjusted:

- `capture_outcomes` was almost keyed to `harvested_signals.id`. That would have made a capture
  outcome undeletable-but-orphanable and, worse, would have prevented recording an outcome for an
  item that *failed before a signal row existed* — silently losing exactly the failures Principle V
  says must never be dropped. It is keyed to `(platform, content_id)` instead, which exists
  independently of whether a signal row was written.
- The availability seed was almost written to assert TikTok carousel/image/story availability from
  the code path. Principle VIII forbids claims from unobserved data; those entries are seeded
  `undetermined` with the row count (0) recorded as the reason.
