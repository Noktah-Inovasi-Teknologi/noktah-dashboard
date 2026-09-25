# Database Schema Development Rules

## Overview

The application schema lives in two places that must always agree:

- `config/postgres/init.sql` — the **greenfield path**, applied once at first container boot.
- `config/postgres/migrations/*.sql` — the **upgrade path**, applied by hand to a running database.

Before feature 004-relational-spine, `harvested_items` and `harvested_signals` existed only in
`init.sql`, with no migration file — there was no supported way to change them on a running
database. That defect is what this file exists to prevent from recurring.

## The migration contract

Every migration under `config/postgres/migrations/` MUST be:

- **Idempotent** — `CREATE TABLE IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS`,
  `ADD COLUMN IF NOT EXISTS`, and a `DO $$ ... IF NOT EXISTS (SELECT 1 FROM pg_constraint ...) $$`
  guard for named constraints (Postgres has no `ADD CONSTRAINT IF NOT EXISTS`). Re-running a
  migration must be harmless — an operator cannot always know what has already been applied.
- **Additive** — no `DROP COLUMN`, no `ALTER … TYPE`, no `SET NOT NULL` on a populated table, no
  tightening of an existing constraint that could reject an existing row.
- **Transactional** — wrapped in `BEGIN; … COMMIT;` so a failure leaves nothing half-applied.
- **Self-recording** — ends with
  `INSERT INTO schema_migrations (version) VALUES ('NNN_name') ON CONFLICT (version) DO NOTHING;`.
- **Reversible** — a matching `NNN_name.down.sql` exists.

Applied with:
```bash
docker exec -i postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" < config/postgres/migrations/NNN_name.sql
```

**Never glob the migrations directory.** `NNN_*.sql` also matches `NNN_name.down.sql` — applying a
migration and its own reversal back-to-back silently undoes what you just did. Always apply an
explicit, ordered list of filenames.

## Baseline migrations

`001_knowledge_records`, `002_harvested_items`, and `003_harvested_signals` reproduce tables that
already existed in production before this feature. On an existing database they introduce
**nothing**. Their `.down.sql` is therefore an intentionally empty no-op with an explanatory
comment — `DROP TABLE` would destroy data that predates the migration, which the reversibility
contract explicitly forbids.

If a table gets an audit finding like "exists only in `init.sql`, no migration" again, the fix is
the same shape: write a baseline migration that `CREATE TABLE IF NOT EXISTS`-reproduces it exactly,
give it a no-op down script, and mirror it into `init.sql`.

## Making a column mandatory without a destructive migration

`SET NOT NULL` on a populated table takes an `ACCESS EXCLUSIVE` lock and a full table rewrite, and
is awkward to reverse. Instead:

1. Add the column **nullable** in one migration.
2. Backfill it (a flow, not a migration — SQL cannot read Google Sheets).
3. In a later migration, add a named `CHECK (col IS NOT NULL) NOT VALID`, then immediately
   `VALIDATE CONSTRAINT`. `NOT VALID` skips checking existing rows at add-time; `VALIDATE`
   (a `SHARE UPDATE EXCLUSIVE` lock, not `ACCESS EXCLUSIVE`) checks them without blocking reads
   or writes, and **fails loudly if any row is still unlinked** — which is the point. Reverses
   with a single `DROP CONSTRAINT`.

See `config/postgres/migrations/006_enforce_account_link.sql` for the reference implementation,
and `flows/spine_backfill.py` for the backfill it depends on running first.

**A `NOT VALID` → `VALIDATE` constraint and a plain `CHECK` constraint on an empty table produce
byte-identical `pg_constraint` entries once validated** — `pg_get_constraintdef()` only shows a
`NOT VALID` suffix while `convalidated = false`. This is why `init.sql` can declare the equivalent
constraint directly (the table starts empty, so it validates immediately) and still stay in parity
with the migrated path.

## Mirroring into `init.sql`

Every migration must also be reflected in `init.sql`, in the same order, so a database built from
scratch matches one built by applying the full migration chain. Two traps:

- **Match constraint names exactly.** A migration written as `ALTER TABLE t ADD CONSTRAINT
  fk_thing FOREIGN KEY (...) REFERENCES ...` and an `init.sql` copy written as an inline
  `col UUID REFERENCES other(id)` produce **different** auto-generated vs. explicit constraint
  names — a parity mismatch. When a migration adds a constraint by explicit ALTER with a name,
  mirror it the same way in `init.sql`, not as an inline column-level `REFERENCES`.
- **Match nullability semantics, not just the end state.** A column enforced `NOT NULL` via a
  named `CHECK` constraint (because the enforcement had to be added after the column, per the
  pattern above) is **not** the same as a column declared `NOT NULL` at creation —
  `information_schema.columns.is_nullable` reflects only the attribute-level `NOT NULL`, not an
  arbitrary `CHECK`. If the migration path produces `is_nullable = 'YES'` (enforced via `CHECK`
  instead), `init.sql` must declare the column the same way, or the parity check fails.

## Proving the two paths agree

```bash
script/verify_schema_parity.sh
```

Builds two throwaway databases — one from `init.sql`, one from the full migration chain — and
diffs `information_schema.tables`, `information_schema.columns`, `pg_indexes`, and `pg_constraint`.
Exits non-zero on any difference. Also available as
`service/prefect/tests/test_schema_parity.py::test_init_sql_and_migration_chain_are_structurally_identical`
(marked `@pytest.mark.schema`, real Postgres required — see below).

**Run this after any edit to `init.sql` or to a migration.** The two files are hand-written in
parallel and will drift the moment one is edited alone — which is exactly how `harvested_items`
and `harvested_signals` came to have no migration at all.

## Testing against a real database, not mocks

Schema and constraint tests (`test_spine_schema.py`, `test_account_resolution.py`,
`test_roster_sync.py`, `test_roster_alias.py`, `test_spine_backfill.py`, `test_run_records.py`,
`test_schema_parity.py`) run against a real disposable PostgreSQL database, following the
knowledge-base service's precedent (`.claude/rules/backend/knowledge-base.md`): partial unique
indexes, the `NOT VALID` → `VALIDATE` sequence, and cross-source name reconciliation are exactly
the class of bug that passes against a mock and fails against Postgres.

All such tests carry `pytestmark = pytest.mark.schema`. `service/prefect/tests/conftest.py`'s
`pytest_collection_modifyitems` auto-skips only `schema`-marked tests when no database is
reachable at `SPINE_TEST_DATABASE_URL` — every other (mocked) test in the suite is unaffected.

```bash
# From the host, against the postgres container's published port:
cd service/prefect
./.venv/Scripts/python.exe -m pytest tests/ -m schema -v
```

The `spine_db` fixture creates and migrates a fresh throwaway database (`spine_test` by default)
per test — no manual setup required beyond a reachable Postgres server.

## Rehearsing against real data before touching production

```bash
script/db_rehearsal.sh create   # clones noktah_dashboard into spine_rehearsal
script/db_rehearsal.sh drop     # tears it down
```

Apply migrations, run `roster-sync`/`spine-backfill` (via `SPINE_DB_URL` pointing at the
rehearsal clone), and verify row counts and headline queries **before** running any of it against
`noktah_dashboard` directly. Applying schema changes to the live database is a deliberate,
separate step — never the default path while iterating.

## Field availability and capture outcomes (feature 005)

Two tables make absence legible. Before them, "the platform never publishes
this" and "our enrichment call failed" were both stored as `NULL`, so a
collection regression was indistinguishable from a platform limit.

- **`field_availability`** — "can this ever be known?", keyed by
  `(platform, content_type, field_name)`. Reference data, mirrored from the
  version-controlled `config/field_availability.yaml` by `field-availability-sync`.
  Holds **no foreign keys**, deliberately: a determination must be updatable
  without touching a stored observation.
- **`capture_outcomes`** — "was it known this time?". **Append-only**, one row
  per supplementary capture attempt.

Three details that are easy to get wrong:

- **`capture_outcomes` is keyed by `(platform, content_id)`, not
  `harvested_signals.id`.** A capture can fail *before* a signal row exists;
  keying to the signal row would make exactly those failures unrecordable.
- **`no_match` is not `failed`.** A carousel absent from the Reels-keyed clips
  response is `no_match` — the pass worked, this item just wasn't in it.
  Collapsing the two restores the ambiguity the feature removes.
- **`CHECK (outcome <> 'failed' OR (reason IS NOT NULL AND reason IN (...)))`** —
  the `IS NOT NULL` term is load-bearing. Without it, `outcome='failed'` with a
  NULL reason evaluates to `false OR NULL` = `NULL`, and **a CHECK constraint
  passes on NULL**, letting through precisely the case being forbidden. Caught by
  `test_capture_outcome.py::test_failed_without_reason_rejected`.

The status vocabulary is closed and enforced at the database boundary, not only
in the sync task — it is reference data other systems read, so a sixth value must
be impossible to store even if the task is bypassed.

## Observation history and velocity (feature 006)

Two tables and a view make engagement *accumulation* measurable. Before them a
post's counts were frozen at first sighting — `harvested_signals` is UNIQUE on
`(platform, content_id)`, so the schema had no slot for a second observation,
and a post that earned 5,000 likes in six hours was indistinguishable from one
that took three weeks.

- **`metric_observations`** — append-only, many rows per item. The record of
  what was seen and when. Never updated, never deleted.
- **`velocity_intervals`** — derived, one row per consecutive observation pair.
  **Recomputed in place on re-derivation.** This does NOT breach Principle VII,
  which governs *observations*: a derived row is a pure function of two
  immutable endpoints plus configuration, and FR-016c requires it be
  re-derivable after a threshold change. `config_version` records which
  thresholds produced each verdict.
- **`velocity_status`** — a view, not a column, answering "why does this item
  have no velocity" with exactly one reason.

Four details that are easy to get wrong:

- **Both tables are keyed by `(platform, content_id)`, not `harvested_signals.id`.**
  Same reasoning as `capture_outcomes`: the failure-retry purge can delete a
  signal row, and observation history must outlive that (FR-005). Regression
  tests: `test_metric_observation.py::test_purging_the_dedup_ledger_does_not_touch_observations`.
- **`velocity_status`'s item universe is the UNION of `harvested_signals` and
  `metric_observations`** — not the observation table alone. An item carrying no
  metric anywhere has nothing to seed and therefore zero observations; driven by
  observations alone it would vanish from the view, lacking both a velocity and
  a reason, which is exactly what FR-024 forbids. Measured: 2 of 819 rows are in
  that position. Caught only by validating against real data, not by any test
  written beforehand.
- **`never_observed` must be tested before the single-observation branches**, or
  zero falls through to one. Likewise `legacy_only` before `observed_once` — both
  have one observation and only `captured_count = 0` separates them.
- **`chk_metric_observations_has_a_value`** forbids an all-NULL observation. The
  correct record for "the capture produced nothing" is a `capture_outcomes` row
  with `outcome = 'no_match'`; an empty observation would make the series look
  longer than the evidence supports.

Migration 008 also **widens** `chk_capture_outcomes_kind` by DROP + ADD with a
strict superset (adding `metric_refresh`). Widening is the one shape of
constraint replacement this file permits, because it cannot reject an existing
row — verify the re-added list still contains all four original kinds.

Absence vocabulary, and the distinction that took a clarification round to get
right: `aged_out_of_listing` means published *before* the oldest item the
listing returned, so it was provably out of reach. `absent_within_reach` means it
should have been returned and was not — consistent with removal, but a short
page produces the same observation, so it is **never** recorded as `deleted`.
`deleted` requires an explicit platform not-found, which only the download path
can observe.

## Structured extraction (feature 007)

Migration 009 adds seven tables and one view: `content_extractions`,
`extraction_beats`, `extraction_attributes`, `extraction_quarantine`,
`extraction_vocabulary_terms`, `calibration_sample_members`,
`extraction_batch_runs`, plus `extraction_status`. Nothing is added to
`harvested_signals` — this feature is additive alongside it, never through it.

Its **one `ALTER` widens `runs.kind`** to accept `'extraction'`, by DROP + ADD
with a strict superset. Same shape as 008's `chk_capture_outcomes_kind` change,
and the only constraint replacement this file permits. A costed extraction batch
IS a run — it has a flow name, timing, a status and a summary, all of which `runs`
already holds — so `extraction_batch_runs` is a detail table on it rather than a
parallel run table.

Five details that are easy to get wrong:

- **The beats FK targets the vocabulary's FULL primary key** `(version, dimension,
  term)`, not `(version, term)`. Targeting the shorter key needs
  `UNIQUE (version, term)`, which forbids two dimensions sharing a term string in
  one version — and a future attribute dimension publishing `hook` is entirely
  plausible. That would make FR-043a false the moment S-05 lands. The constant
  `dimension` column on the beat row costs one byte and keeps the door open.
- **A beat is held to its parent's vocabulary version by an FK, not a CHECK** —
  a row-level CHECK cannot reference another table. That needs the otherwise
  redundant `uq_extraction_id_vocab UNIQUE (id, vocabulary_version)` on the
  parent, because Postgres requires a unique constraint covering exactly the
  referenced columns.
- **`extraction_quarantine` records outcomes where NO model call happened** —
  `media_unavailable`, `spend_ceiling_reached`. `raw_output` and `model_served`
  are nullable for exactly that. The table is the record of every attempt's
  outcome, not only of bad model output. There is **no `other`** in
  `failure_kind`: an escape hatch would absorb precisely the novel failures worth
  noticing, and it is the axis FR-020's counts group by.
- **Three things in `extraction_batch_runs` deliberately have no constraint.**
  `items_unclassified = 0` is not a CHECK — SC-010 wants unclassified outcomes
  *counted*, and a constraint would make the run fail to write its own summary
  rather than report the defect. The outcome counts summing to `items_in_scope`
  is only true once `runs.ended_at` is set, so it is a zero-row test query, like
  beat contiguity. `actual_usd` within ±25% of the projection is a *finding*, not
  a rejection.
- **`extraction_status` branch order is load-bearing**: `never_attempted` must be
  tested before the version-comparison branches, or an item with no rows falls
  through into `superseded_version_only`. Its item universe is the UNION of
  `harvested_signals` and `content_extractions` — driven by extractions alone, an
  item never extracted would vanish, having neither a result nor a reason.

Contiguity, "≥1 beat", and batch accounting are cross-row invariants with no row
constraint available; each has a zero-row query asserted by test.

## Noktah Hub: Registry, Client Card, Intake (feature 008)

Migration 010 adds the Hub's tables (`noktah_brands`, `people`, `person_emails`,
`person_roles`, `client_team_assignments`, `registry_changes`, `card_definitions`,
`card_values`, `intakes`, `intake_proposals`, `client_requests`,
`client_request_events`, `client_summaries`, `ai_ledger`, `hub_alerts_sent`,
`hub_sync_state`) and **additive** columns on `clients`. `hub-api` is the only
writer. Details that are easy to get wrong:

- **Card values are append-only history.** One field has at most ONE `current` and
  ONE `pending` row, enforced by two partial unique indexes. Those are checked
  immediately, so a write moves the old current row to `superseded`/`corrected`
  BEFORE inserting the new one, and fills the old row's `replaced_by` AFTER (a
  non-deferrable FK). Same ordering lesson as `knowledge_records`
  (knowledge-base.md). Don't reorder `card/values.write`.
- **The card definition freezes on first use.** `card_definitions.frozen_at` is set
  the first time a value references a version; the startup sync then refuses to
  change that version's YAML. A changed field list is `card_v2.yaml`, never an edit.
- **The Intake cache is a unique index**: `(client_id, content_hash) WHERE status <>
  'failed'`. A failed Intake doesn't block a retry of the same content; a
  successful one makes the retry a cache hit (no second model call). The one-time
  old-notes run (done 2026-09-25, code removed since) was idempotent by
  `uq_intakes_old_note_source` on `source_ref`; its `old_note` Intakes stay.
- **`chk_intakes_failure_reason` has the load-bearing `IS NOT NULL`**, for the same
  reason as `capture_outcomes` (a NULL reason would pass the CHECK).
- **`registry_changes` is the sheet copy's clock.** `hub_sync_state.last_change_id`
  is advanced only after a successful write, so a failed copy retries the same
  changes. `last_success_at IS NULL` means "not imported yet", and the copy refuses
  to write: it would otherwise overwrite the sheet from an empty Registry.
- **`clients.noktah_brand_id` is enforced later, by migration 011** (the schema.md
  pattern: `CHECK … NOT VALID` then `VALIDATE`), because the Clients roster-sync
  created have no brand until the one-time import gives them one. Apply 011 only
  after the real import; before it, 011 fails by design. `service/prefect`'s
  `spine_db` fixture skips 011 as it skips 006: roster-sync predates brands and
  never sets one (it is paused once the Hub owns the roster).

## Naming

Flows: `roster-sync`, `spine-backfill`, `field-availability-sync`,
`sheet-header-backfill` — kebab-case (constitution I).
Tasks: `roster.client.upsert`, `roster.alias.upsert`, `roster.account.upsert`, `roster.role.upsert`,
`roster.account.record-rename`, `roster.alias.reassign`, `spine.signal.link`, `spine.item.link`,
`spine.knowledge.link`, `social.account.resolve`, `social.account.record-followers`,
`social.capture.record-outcome`, `availability.determination.sync`,
`availability.determination.get`, `availability.determination.coverage-gaps`,
`google.sheets.ensure-header`, `run.record.start`, `run.record.finish`,
`social.observation.record`, `social.signal.record-metrics`, `social.known-items`,
`velocity.interval.derive`, `velocity.observation.backfill` —
`api-group.resource.action`.

Feature 006 adds the flows `velocity-derive` (monthly) and `observation-backfill`
(one-time, unscheduled).

Feature 008 adds the flows `hub-summary-refresh`, `hub-sheet-sync` (also deployed as
`hub-sheet-check`), `hub-intake-purge` and `hub-registry-import`, all calling `hub-api` through the one task `hub.internal.call`.

---

**Last Updated:** 2026-09-25
**Feature:** 004-relational-spine
