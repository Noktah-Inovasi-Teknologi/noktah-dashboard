# Implementation Plan: Longitudinal Metric Capture

**Branch**: `006-longitudinal-metric-capture` | **Date**: 2026-08-03 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/006-longitudinal-metric-capture/spec.md`

## Summary

Stop discarding numbers the system already has in hand, then derive rate of change from them.

The de-duplication gate is correct and stays. What changes is that a listing response's current
counts are recorded for **every** item it contains, before the gate decides whether to download
anything. Re-observation therefore costs **zero additional platform requests** — the counts arrive
in a response the monthly harvests already fetch, and are discarded one line after they arrive
([social_harvest.py:510-511](../../service/prefect/flows/common/social_harvest.py#L510-L511)).

Phase 0 measured the live store rather than assuming, and three findings shaped the design:

- **The corpus is 819 rows, not 656.** The spec's figure came from the audit's July snapshot. It is
  still growing, so the pre-existing set must be defined as "everything present when the migration
  runs", not as a literal count (R1).
- **The entire corpus is re-observable at zero cost.** ⚠️ *Corrected 2026-08-03 — Phase 0
  originally reported 69.2% reachable and 252 rows permanently frozen, having assumed
  `list_depth = 30`. The monthly deployments actually list at **150** (`social_harvest_window.py`,
  never overridden), and no account holds more than 69 items. Measured live: one `lasikasyik`
  listing returned **182** items. All 819 rows are reachable; **zero** are frozen (R2).* The reach
  ceiling is still real and still recorded as `aged_out_of_listing` when an item does fall out —
  it is simply far higher than first stated.
- **Velocity coverage differs sharply by metric.** Instagram carries `likes` on 791/793 rows but
  `views` and `comments` on only 338 — the Reels-enriched video subset. Share counts are absent
  everywhere. Per-metric velocity will therefore be dense for likes and sparse for everything else,
  so consumers must read the per-metric observation count rather than assume uniform coverage (R3).

The build is small: two tables, one widened vocabulary, one classification view, one new flow, and
an insertion point of a few lines in the harvest loop. No model calls, no new service, no new
platform requests.

## Technical Context

**Language/Version**: Python 3.14

**Primary Dependencies**: Prefect 3.6.9, asyncpg (Prefect service). No new dependency, and no roach
change — therefore **no roach image rebuild**.

**Storage**: PostgreSQL 15 — existing `harvested_signals`, `accounts`, `runs`, `capture_outcomes`;
two new tables (`metric_observations`, `velocity_intervals`) and one view (`velocity_status`) added
by migration `008_observation_history`.

**Testing**: pytest. Schema, constraint, and derivation-correctness tests run against a **real
disposable PostgreSQL database** (`pytestmark = pytest.mark.schema`), per
`.claude/rules/backend/schema.md`. Pure arithmetic (interval rates, plateau/acceleration detection)
is unit-tested with no database.

**Target Platform**: Linux containers via docker-compose

**Project Type**: Workflow-orchestration service (Prefect). No new service.

**Performance Goals**: Not latency-sensitive — batch. The governing budget is **request volume and
model spend, both of which must not move at all** (FR-025, FR-026).

**Constraints**:
- Zero additional platform requests; zero model calls (FR-025, FR-026; Constitution X/XI).
- Schema changes additive; migration + `init.sql` parity enforced by
  `script/verify_schema_parity.sh`.
- Velocity is datastore-only — no delivered sheet gains a column, no sheet layout changes (FR-016a).
- Derivation must be re-runnable without duplicating derived rows (FR-016c).
- A metric refresh must not erase analysis or reviewer fields (FR-010a) — see Complexity Tracking.

**Scale/Scope**: 819 signal rows across 22 profiles, ~13 actively posting; monthly cadence. At one
observation per item per run, the observation table grows by roughly 570 rows/month. This is
small-data — the risk is correctness and honest absence, not throughput.

## Constitution Check

*GATE: evaluated before Phase 0 and re-evaluated after Phase 1 design.*

| Principle | Gate | Status |
|---|---|---|
| **I. Prefect orchestration** | Flows kebab-case, tasks `api-group.resource.action`, flows return the standard dict and never raise | **PASS** — flow `velocity-derive`; tasks `social.observation.record`, `velocity.interval.derive`, `velocity.acceleration.detect`, `velocity.observation.backfill` |
| **II. External API standards** | No platform API assumed; no `retries=2` on collection tasks | **PASS** — no new collection call at all; the observation pass reads an in-memory response |
| **III. Docker-first** | Runs in existing containers | **PASS** — Prefect service only; no roach rebuild, no new service |
| **IV. Security** | No new credentials, nothing sensitive logged | **PASS** — no new external surface |
| **V. Observability** | Skipped/failed items recorded with a classified reason, never silently dropped | **PASS** — FR-018/FR-019 via `capture_outcomes`; run summary gains observation counters |
| **VI. Data honesty** | No metric stored that cannot be observed; no proxy substituted | **PASS** — decreases stored as observed (FR-012); FR-010a forbids a refresh erasing unobserved fields; nothing is interpolated between observations |
| **VII. Append-only observations** | Observations immutable; misses recorded; no even-spacing assumption; pre-feature content marked history-less, never zero-velocity | **PASS** — this feature *is* Principle VII: `metric_observations` append-only, `velocity_intervals` divides by measured elapsed time, `velocity_status` classifies every absence |
| **VIII. Evidence discipline** | Sample size visible; nothing asserted from unobserved data | **PASS** — FR-017 puts the observation count beside every velocity; the 252 aged-out rows are reported as unreachable rather than inferred |
| **IX. Identity chain** | Traceability preserved | **PASS** — each observation carries `run_id` and `account_id` |
| **X. Polite collection** | No increase in request rate; uncollectable targets reported, not routed around | **PASS** — zero marginal requests (FR-026); "aged out of listing" is a reported fact, and extending depth to chase it is explicitly out of scope |
| **XI. Model call governance** | Deterministic first; costed batch ops support dry-run | **PASS** — zero model calls (FR-025); `velocity-derive --validate-only` (FR-027) |
| **XII. Additive schema change** | Additive by default; vocabularies versioned as data; destructive migration justified | **PASS with one documented item** — see Complexity Tracking for the `capture_kind` CHECK widening |

**Post-Phase-1 re-evaluation**: unchanged. The design added no abstraction layer, no service, and no
new external dependency. The single Complexity Tracking entry is a constraint *widening* that cannot
reject an existing row.

## Project Structure

### Documentation (this feature)

```text
specs/006-longitudinal-metric-capture/
├── plan.md              # This file
├── research.md          # Phase 0 — measured findings, resolved unknowns
├── data-model.md        # Phase 1 — tables, view, vocabularies, invariants
├── quickstart.md        # Phase 1 — runnable validation
├── contracts/
│   ├── observation-record.md      # capture → stored observation
│   ├── velocity-derivation.md     # observation pair → derived interval
│   └── absence-classification.md  # why an item has no velocity
├── checklists/
│   └── requirements.md
├── spec.md
└── tasks.md             # Phase 2 — NOT created by /speckit-plan
```

### Source Code (repository root)

```text
config/postgres/
├── init.sql                                     # MODIFIED — mirror 008 (greenfield parity)
└── migrations/
    ├── 008_observation_history.sql              # NEW — 2 tables, 1 view, 1 widened CHECK
    └── 008_observation_history.down.sql         # NEW — real reversal

service/prefect/
├── flows/
│   ├── velocity_derive.py                       # NEW — `velocity-derive` flow (+ --validate-only)
│   └── common/
│       └── social_harvest.py                    # MODIFIED — observation pass over all listed items
├── tasks/
│   ├── social_tasks.py                          # MODIFIED — social.observation.record; FR-010a fix
│   └── velocity_tasks.py                        # NEW — derivation + detection tasks
├── prefect.yaml                                 # MODIFIED — velocity-derive deployment (monthly)
└── tests/
    ├── test_metric_observation.py               # NEW — append-only, provenance, legacy seeding
    ├── test_velocity_derivation.py              # NEW — irregular spacing, decreases, idempotence
    ├── test_absence_classification.py           # NEW — exactly-one-reason invariant
    ├── test_social_harvest.py                   # MODIFIED — refresh path, no download/analyze
    └── test_schema_parity.py                    # unchanged; must still pass
```

**Structure Decision**: Extends the existing Prefect service in place — the same shape as features
004 and 005 (additive migration, tasks beside their peers, one thin flow entrypoint), introducing no
new module boundary. Velocity derivation gets its own task module (`velocity_tasks.py`) rather than
joining `social_tasks.py`, because it performs no collection and no platform I/O; mixing it in would
blur the collection/derivation split that Constitution II's retry carve-out depends on.

## Design Decisions

### Where the observation is taken

The observation pass runs over `all_items` — the full listing response — **not** over the
depth-selected `items`. This is the difference between the feature working and barely working: the
day-window filter is applied client-side after the response arrives, so restricting observation to
selected items would discard exactly the counts FR-004 exists to capture.

Placement is after per-profile account resolution (so `account_id` is known) and before the per-item
download loop. The de-duplication gate itself is **not modified** — it keeps skipping download and
analysis exactly as today, which is what keeps FR-002 and FR-026 true.

One observation per item per run, taken in this single pass. New items therefore get their first
observation here rather than at signal-write time, so no item is double-observed within a run.

### Why derivation is a separate flow

Per the Q2 clarification, `velocity-derive` runs on its own schedule, reading stored observations.
It performs no collection, so a derivation failure cannot affect a harvest and a blocked profile
cannot prevent derivation over what was already recorded. It also makes `--validate-only` natural
(FR-027) and permits re-derivation after a threshold change without re-listing anything.

Idempotence (FR-016c) is by construction: an interval is keyed to its ordered observation pair, so
re-running recomputes the same rows rather than appending. Derived values are recomputed on re-run —
the one place this system deliberately overwrites, and it is safe because a derived row is a
function of two immutable observations plus configuration, never itself an observation.

### Why absence classification is a view

FR-024's "exactly one reason" is an invariant over current state, so it is expressed as a view with
a single `CASE` — the same shape as feature 005's why-empty query. A stored classification column
would need maintaining on every observation write and could silently disagree with the observations
it describes, which is precisely what the Q3 clarification rejected for history status.

## Complexity Tracking

> Filled because Constitution Check flagged one item under Principle XII.

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| Migration 008 widens the closed `chk_capture_outcomes_kind` CHECK via `DROP CONSTRAINT` + `ADD CONSTRAINT`, rather than being purely additive | A missed metric refresh must be recorded in `capture_outcomes` (FR-018), whose `capture_kind` vocabulary is closed at the database boundary and does not include this feature's capture kind. The vocabulary is deliberately closed, so it cannot be extended without replacing the constraint. | A parallel `missed_refreshes` table was rejected: it would duplicate the exact shape of `capture_outcomes` — same `(platform, content_id)` key, same append-only semantics, same reason vocabulary — and would leave "why is this value missing" answerable only by a UNION across two tables, reintroducing the ambiguity feature 005 removed. The replacement is a strict **superset** (every currently-valid value stays valid), so it cannot reject an existing row; the `.down.sql` restores the narrower vocabulary exactly. |

**Not a violation, but recorded because it looks like one:** `velocity_intervals` rows are
recomputed in place on re-derivation. This does not breach Principle VII, which governs
*observations*. Derived values are explicitly not observations, and the spec requires them to be
re-derivable after a threshold change (FR-016c). No observation is ever updated or deleted.
