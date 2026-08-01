# Contract: Migration Files

**Feature**: `004-relational-spine` | Satisfies FR-025 … FR-029

The interface this feature exposes to an operator is a set of SQL files and the rules they obey.

## Location and naming

```
config/postgres/migrations/
├── 001_knowledge_records.sql        # exists
├── 001_knowledge_records.down.sql   # new — no-op, see "Baselines"
├── 002_harvested_items.sql          # new — baseline
├── 002_harvested_items.down.sql     # new — no-op
├── 003_harvested_signals.sql        # new — baseline
├── 003_harvested_signals.down.sql   # new — no-op
├── 004_relational_spine.sql         # new tables
├── 004_relational_spine.down.sql
├── 005_link_existing.sql            # additive columns + FKs + indexes
├── 005_link_existing.down.sql
├── 006_enforce_account_link.sql     # NOT VALID → VALIDATE; run only after backfill
└── 006_enforce_account_link.down.sql
```

`NNN_snake_name.sql` forward, `NNN_snake_name.down.sql` reverse. Numbers are applied in ascending
order and never reused.

## Application

```bash
docker exec -i postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  < config/postgres/migrations/004_relational_spine.sql
```

No runner, no framework. Each file is self-contained.

## Rules every file must satisfy

| Rule | Requirement | Why |
|---|---|---|
| **Idempotent** | `CREATE TABLE IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS`, `ADD COLUMN IF NOT EXISTS`, guarded `DO $$` blocks for constraints | FR-028. Re-running must be harmless — an operator cannot always know what has been applied |
| **Additive** | No `DROP COLUMN`, no `ALTER … TYPE`, no `SET NOT NULL` on a populated table, no tightening that could reject an existing row | FR-026, constitution XII |
| **Transactional** | Wrapped so a failure leaves nothing half-applied | A partly-applied schema change is worse than none |
| **Records itself** | Ends with `INSERT INTO schema_migrations (version) … ON CONFLICT DO NOTHING` | Makes applied state inspectable |
| **Reversible** | A matching `.down.sql` exists | FR-027 |

## Baselines are a deliberate exception

`002` and `003` reproduce tables that already exist in production, holding 691 and 656 rows. On an
existing database they introduce **nothing**.

Their `.down.sql` is therefore a **no-op with an explanatory comment, not `DROP TABLE`**. Dropping
would destroy data that predates the migration, which FR-027 explicitly forbids ("removes what it
introduced without affecting data that existed before it"). The same applies to `001`.

```sql
-- 002_harvested_items.down.sql
-- Intentionally empty.
-- 002 is a BASELINE: it reproduces a table that already existed in the running
-- database (691 rows at the time of writing). It introduces nothing on an
-- existing database, so its correct reversal is to do nothing. DROP TABLE here
-- would destroy data that predates the migration — forbidden by FR-027.
```

## `006` has an ordering precondition

`006_enforce_account_link.sql` adds `CHECK (account_id IS NOT NULL) NOT VALID` and then
`VALIDATE CONSTRAINT`. It **fails by design** if any row is still unlinked.

It must run **after** `spine-backfill` reports zero unlinked rows. This is intentional: it is the
gate that turns SC-001 from an aspiration into an enforced invariant, and a failure here means the
backfill is incomplete, which is exactly when it should stop.

## Parity check (FR-029)

`init.sql` and the migration chain must produce the same structure. Verified, not trusted:

```bash
script/verify_schema_parity.sh
```

Builds two throwaway databases — one from `config/postgres/init.sql`, one from the migration chain
in order — and diffs:

- `information_schema.tables`
- `information_schema.columns` (name, type, nullability, default)
- `pg_indexes` (definition)
- `pg_constraint` (type, definition)

Exit non-zero on any difference. Also exposed as a pytest that skips when no database is reachable,
following the knowledge-base service's precedent of testing schema against real Postgres rather
than mocks.

Any change to `init.sql` **or** to a migration must keep this green. That is the whole point: the
two files are hand-written and will drift the moment one is edited alone — which is precisely how
`harvested_items` and `harvested_signals` came to have no migration at all.
