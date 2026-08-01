# Implementation Plan: Relational Spine — Clients, Accounts, Runs, and Briefs

**Branch**: `004-relational-spine` | **Date**: 2026-07-31 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/004-relational-spine/spec.md`

## Summary

Give the application schema the relational spine it lacks. Today three tables hold 62, 656, and 691
rows and share exactly one foreign key, which points at itself; every fact that would connect them —
who the clients are, which handle belongs to whom, which handle is a competitor — lives in Google
Sheets and is resolved at run time by a matching rule in code that is measurably ambiguous.

Six new tables (`clients`, `client_aliases`, `accounts`, `account_handles`,
`client_account_roles`, `account_follower_observations`) plus `runs`, `briefs`, and
`schema_migrations` turn those facts into data. `harvested_signals` and `harvested_items` gain a
link to an account; `knowledge_records` gains a link to a client. Every existing column, index, and
uniqueness rule is preserved. Two idempotent Prefect flows fill the new tables: `roster-sync`
(daily, sheets → datastore) and `spine-backfill` (one-time, row linking). The collection write path
gains account resolution, and skips-with-a-reason when a handle is unregistered or inactive.

Five migrations, all additive and reversible, including the **baseline migrations that
`harvested_items` and `harvested_signals` have never had** — without which none of the above can be
applied to a running database at all.

Populating briefs, brief-to-post attribution, and cohort semantics are out of scope (S-06/07/08).

## Technical Context

**Language/Version**: Python 3.14 (the `service/prefect` image), PostgreSQL 15.

**Primary Dependencies**: **No new packages.** Prefect 3.6.9 (async flows/tasks, deployments, cron),
`asyncpg` for all datastore work, `google-api-python-client`/`google-auth` via the existing
`GoogleCredentials` block for the two worksheets, and `hashmap.py` for the `COMPONENTS` /
`CLIENT_SOCIAL` blocks and `profile_handle` normalisation.

**Storage**: PostgreSQL 15, `noktah_dashboard` database. 9 new tables, 5 added columns on 3 existing
tables. Extensions `pgcrypto` and `pg_trgm` already present. No new storage system.

**Testing**: pytest in `service/prefect/tests/`. Sheets mocked for the reconciliation logic
(union of two roster sources, alias conflict handling, idempotency, report shape). **Schema and
constraint behaviour tested against a real disposable Postgres**, not mocks — following
`.claude/rules/backend/knowledge-base.md`, because partial unique indexes and the `NOT VALID` →
`VALIDATE` sequence are exactly the class of bug that passes against a fake. Plus a schema-parity
check (FR-029) as both a script and a skip-if-no-database pytest.

**Target Platform**: Linux containers on Docker Desktop, `dashboard-networks`; the existing
`postgres`, `prefect`, and `prefect-worker` services.

**Project Type**: Prefect workflows + database schema. No new service, no new image.

**Performance Goals**: Not a driver — the whole dataset is ~1,400 rows and ~22 accounts.
`roster-sync` completes in seconds; account resolution adds one indexed lookup per collected item
against a table of tens of rows.

**Constraints**: Migrations additive and reversible (FR-026/FR-027); every existing column and
uniqueness rule preserved (FR-015); the write path must never create an account (FR-016b); flows
never raise and continue past single-item failures (constitution I/V); no secrets in source or logs
(constitution IV).

**Scale/Scope**: 21 clients, 22 accounts, 656 signal rows, 691 ledger rows, 62 knowledge records.
9 new tables, 5 new columns, 5 migrations + 5 down scripts, 2 new flows, 3 new task modules, 1
insertion into the existing harvest engine, 1 new deployment, 1 parity script.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design.*

Evaluated against `.specify/memory/constitution.md` v1.1.0. Principles VI–XII are correctness
requirements and are weighted equally with I–V, per Governance.

| Principle | Status | Notes |
|---|---|---|
| I. Prefect orchestration | ✅ Pass | `roster-sync`, `spine-backfill` — kebab flows, async, return the standard dict, never raise. Tasks `roster.*`, `spine.*`, `social.account.*`, `run.record.*` follow `api-group.resource.action`. Both runnable via `docker exec prefect python flows/<name>.py`. |
| II. External API standards | ✅ Pass | Google via the existing OAuth block; DB tasks `retries=2, retry_delay_seconds=30`. No new provider. No collection-pacing change — the retry carve-out is untouched. |
| III. Docker-first | ✅ Pass | No new service or image. Migrations are files under `config/postgres/migrations/`; one new deployment in `prefect.yaml`. |
| IV. Security | ✅ Pass | Credentials from env/blocks only. Logs carry client names, handles, and counts — no secrets. |
| V. Observability & error handling | ✅ Pass | Classified skip reasons (FR-016c) and a full add/change/unresolved report (FR-023). No silent drop anywhere: unresolvable handles become recorded unattributed accounts, unresolvable knowledge records are retained and reported. |
| VI. Data honesty & provenance | ✅ Pass | `account_follower_observations.follower_count` is `NOT NULL` and the table has no estimate or interpolation column, so an observation and an estimate structurally cannot share a field. Absence is the absence of a row. |
| VII. Append-only observations | ⚠️ **Partial** | Follower observations are append-only and never updated. **Missed captures are recorded on the run, not as observation rows.** See Complexity Tracking #2. |
| VIII. Evidence discipline | ✅ N/A | This feature computes no estimates, baselines, or effects. It supplies the client and role dimensions that make matched negatives *possible* later. |
| IX. Identity chain integrity | ✅ Pass | This is the feature that builds the chain. `briefs` and `runs` carry stable ids; attribution and its confidence are explicitly S-07. Account identity survives a handle rename (FR-011), which is a prerequisite for any later attribution. |
| X. Polite collection | ⚠️ **Partial** | Deactivating an account stops storage but not fetching, so a deactivated-but-still-targeted account is fetched and discarded. See Complexity Tracking #1. |
| XI. Model call governance | ✅ Pass | No model calls added or changed. Identity resolution is exact-match on a normalised key — deterministic, which XI explicitly requires over a model call. |
| Development Workflow — dry-run for costed batch operations | ✅ Pass | The clause names *backfill* explicitly. `spine-backfill` mutates 1,347 rows in one pass and `roster-sync` can deactivate accounts, which per FR-024a stops storage. Both take `--validate-only`. Neither spends model budget, so the cost-reporting half of the clause is vacuous here; the preview half is not, and is honoured. |
| XII. Versioned vocabularies & additive schema | ✅ Pass | Every migration additive; no destructive change anywhere, so no justification is owed under XII. Client-name reconciliation moves from hardcoded logic to queryable data, which is the principle's intent. Role is a structural enum in a `CHECK`, not a content-attribute vocabulary. |

**Gate result**: PASS with two declared deviations, both traceable to explicit user decisions during
`/speckit-clarify` rather than to design convenience. Neither is silent; both are recorded below and
surfaced at run time.

## Project Structure

### Documentation (this feature)

```text
specs/004-relational-spine/
├── plan.md                          # This file
├── spec.md                          # Feature specification
├── research.md                      # Phase 0 output
├── data-model.md                    # Phase 1 output
├── quickstart.md                    # Phase 1 output — apply + verify procedure
├── contracts/
│   ├── migrations.md                # Migration file rules, baselines, parity check
│   ├── account-resolution.md        # handle → account at write time; the three outcomes
│   └── roster-sync-report.md        # Reconciliation report shape
├── checklists/
│   └── requirements.md              # Spec quality checklist
└── tasks.md                         # Phase 2 (/speckit-tasks — NOT created here)
```

### Source Code (repository root)

```text
config/postgres/
├── init.sql                         # + the 9 new tables and 5 new columns (greenfield path)
└── migrations/
    ├── 000_schema_migrations.sql         # new — applied-version tracking; must lead the chain
    ├── 001_knowledge_records.down.sql    # new — no-op baseline reversal
    ├── 002_harvested_items.sql           # new — BASELINE (had no migration)
    ├── 003_harvested_signals.sql         # new — BASELINE (had no migration)
    ├── 004_relational_spine.sql          # clients, aliases, accounts, handles, roles,
    │                                     #   follower observations, runs, briefs, schema_migrations
    ├── 005_link_existing.sql             # account_id / run_id / client_id columns + FKs + indexes
    ├── 006_enforce_account_link.sql      # NOT VALID → VALIDATE; runs only after backfill
    │                                     #   also mirrored into init.sql, or parity fails
    └── *.down.sql                        # one per forward migration

service/prefect/
├── flows/
│   ├── roster_sync.py               # "roster-sync" — daily; sheets → clients/aliases/accounts/roles
│   ├── spine_backfill.py            # "spine-backfill" — one-time; link existing rows
│   └── common/
│       ├── roster.py                # shared reconciliation engine (union, alias, conflict, report)
│       └── social_harvest.py        # + account resolution at the write point, + follower capture,
│                                    #   + run record start/finish
├── tasks/
│   ├── roster_tasks.py              # roster.client.upsert, roster.alias.upsert,
│   │                                #   roster.account.upsert, roster.role.upsert, roster.report.build
│   ├── spine_tasks.py               # spine.signal.link, spine.item.link, spine.knowledge.link
│   ├── social_tasks.py              # + social.account.resolve, social.account.record-followers
│   └── run_tasks.py                 # run.record.start, run.record.finish
├── prefect.yaml                     # + roster-sync deployment (daily cron)
└── tests/
    ├── test_roster_sync.py          # two-source union, alias conflicts, idempotency, report shape
    ├── test_spine_backfill.py       # linking, unattributed handles, opaque identifiers
    ├── test_account_resolution.py   # resolved / unregistered / inactive; rename resolves both handles
    └── test_schema_parity.py        # FR-029; skips without a database

script/
└── verify_schema_parity.sh          # init.sql vs migration chain structural diff
```

**Structure Decision**: Everything is additive inside `service/prefect/` and
`config/postgres/`, mirroring the layout songbird and social-harvest already use — thin flow
entrypoints, a `flows/common/` engine, single-responsibility task modules. No new service, image,
or dependency is introduced, so constitution III is satisfied without a Complexity Tracking entry
for structure. The schema work is deliberately *not* expressed as ORM models: this repository has
no ORM, every existing table is hand-written SQL, and introducing one to add nine tables would be a
new abstraction layer that no principle mandates.

## Implementation Sequence

Ordered so each stage is independently verifiable and the risky, externally-dependent work lands
last. Stage numbers map to the user stories in the spec.

| Stage | Delivers | Story | Verifiable by |
|---|---|---|---|
| 1 | Migration conventions: `000_schema_migrations`, the `001` no-op down script, rehearsal + test harness | — (foundational) | A migration can be applied, re-applied, and recorded |
| 2 | `004` — the nine new tables and every constraint from research R7 | US1, US2 | Constraint tests against a real Postgres |
| 3 | `roster-sync` + its deployment | US1, US2 | Report shows 21 clients, `Eskala` disagreement, both Sumenep spellings; re-run is all-`unchanged` |
| 4 | `005` — link columns; `spine-backfill` (with dry-run) | US1 | 0 unlinked signals, 0 unlinked items |
| 5 | `006` — enforce the account link, mirrored into `init.sql` | US1 | SC-001 becomes a database invariant |
| 6 | Account resolution in the write path; run records | US1, US4 | Skip reasons distinguishable in a real harvest summary |
| 7 | Baseline migrations `002`/`003`, their no-op down scripts, parity script | US3 (P3) | `verify_schema_parity.sh` green; chain rebuilds an empty database |
| 8 | Follower capture (TikTok works; Instagram does not) | — | One observation row from a TikTok run; none from Instagram, as expected |

**Only Stage 1 is a true prerequisite.** An earlier draft of this plan claimed the baselines had to
come first — that nothing could be applied to the running database without them. That is wrong, and
writing the task list is what exposed it: `004`, `005`, and `006` only *add* tables and columns to a
database that already has the two harvest tables. The baselines matter for rebuilding a database
from migrations alone, which no other stage does. So US3 now sits where its priority puts it, and
what was genuinely blocking — the migration *conventions* — was split out into Stage 1.

Stage 8 is last because it is the only stage with an external dependency that **is currently known
not to work** (research R3). Sequenced here, it cannot gate anything.

## Complexity Tracking

> Two constitution deviations. Both follow from decisions the user made explicitly during
> `/speckit-clarify`; neither is a convenience.

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|--------------------------------------|
| **X. Polite collection** — a deactivated account is still fetched, then discarded at the storage boundary, spending rate-limited requests on data that is thrown away | Clarification Q5: "inactive" blocks storage rather than being descriptive only. Collection targets are hardcoded in `prefect.yaml`, so the datastore has no way to stop a fetch. FR-024b requires every newly-inactive account to be reported so the target is removed promptly, bounding the waste to the gap between deactivation and an operator acting | Driving collection targets from the datastore removes the waste entirely and is the correct end state — rejected **for this feature only** as it converts a schema feature into a collection-control feature, and is recorded in Out of Scope rather than dropped. Making "inactive" descriptive-only (the alternative offered) was declined by the user |
| **VII. Append-only observations** — a missed follower capture is recorded in the run summary (`follower_capture_missed`, with the handle and a classified reason — T061), not as an observation row | FR-013a explicitly forbids a placeholder row ("a count that was not returned MUST result in no observation"), and a nullable count would let an observation and a non-observation share a field, which constitution VI forbids. The two principles pull opposite ways here and VI wins on the narrower point | A separate `follower_capture_attempts` table satisfies both literally — rejected as disproportionate: it would carry misses for ~22 accounts against **one** account that currently produces any observation at all, and VII's missed-observation clause exists to protect velocity derivation, which requires longitudinal re-observation that is explicitly out of scope here. Revisit when velocity work begins |

## Risks

| Risk | Mitigation |
|---|---|
| `006` runs before the backfill and fails | Intended. It is the gate that turns SC-001 into an invariant; the failure means the backfill is incomplete, which is when it should stop. Documented in the migrations contract and the quickstart |
| `roster-sync` reads an empty sheet and deactivates the entire roster | Departures are inferred from absence, so a failed read is indistinguishable from mass departure. The flow must fail on a bad read rather than act on it — mirroring `hashmap.load_hashmaps()`, which already raises rather than returning empty mappings. Called out in the roster-sync contract |
| The two roster sources drift further apart | `source_disagreements` reports every one. The alias table keeps the system correct regardless of which spelling wins |
| Adding `client_id` to `knowledge_records` disturbs the supersession race | The column is additive and nullable; the deferrable FK and `uq_current_client_subject` are untouched. `test_repository.py::test_single_current_record_invariant_holds_under_concurrent_upserts` must still pass — it is a regression test for exactly this table |
| Instagram follower capture never works | Nothing depends on it. Absence is a recorded state, the table exists so counts can be captured the moment a route is found, and the work is sequenced last |
