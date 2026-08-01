# Quickstart: Relational Spine

**Feature**: `004-relational-spine` | **Spec**: [spec.md](./spec.md) | **Model**: [data-model.md](./data-model.md)

How to apply this feature and prove it works. Every expected number below was measured on the live
database on 2026-07-31; if yours differ, the data has moved on, not the procedure.

## Prerequisites

- `postgres` and `prefect` containers up (`docker-compose ps`)
- A **copy** of the live database for step 1. Do not rehearse migrations on production.
- Google credentials working: `docker exec prefect python hashmap.py` prints block counts

## Baseline — record what you have before touching anything

```bash
docker exec postgres psql -U noktah -d noktah_dashboard -At \
  -c "SELECT count(*) FROM knowledge_records;" \
  -c "SELECT count(*) FROM harvested_signals;" \
  -c "SELECT count(*) FROM harvested_items;"
```

Expect `62` (current: 62 of these), `656`, `691`. Keep them — SC-007 is checked against them.

## 1. Rehearse the migrations on a copy

```bash
docker exec postgres createdb -U noktah spine_rehearsal
docker exec postgres pg_dump -U noktah noktah_dashboard | docker exec -i postgres psql -U noktah -d spine_rehearsal

# Explicit ordered list, NOT a glob. `00N_*.sql` also matches `00N_name.down.sql`,
# which would apply each reversal immediately after its migration — silently
# undoing 004 and 005. Never glob this directory.
FORWARD="000_schema_migrations 001_knowledge_records 002_harvested_items \
003_harvested_signals 004_relational_spine 005_link_existing"

for m in $FORWARD; do
  f="config/postgres/migrations/$m.sql"
  echo "== $f"; docker exec -i postgres psql -U noktah -d spine_rehearsal -v ON_ERROR_STOP=1 < "$f"
done
```

`000` must lead: `004` and `005` end by recording themselves in `schema_migrations`, so they fail
if that table does not exist. `001` is included because a rebuild from migrations alone needs it;
on a clone it is a harmless no-op.

`006` is deliberately absent — it enforces the account link and cannot pass until the backfill has
run. It is applied at step 6.

Then apply them **again**. Both passes must succeed — that is FR-028.

Reverse the newest and confirm the pre-existing rows are untouched (FR-027 / SC-007):

```bash
docker exec -i postgres psql -U noktah -d spine_rehearsal < config/postgres/migrations/005_link_existing.down.sql
docker exec postgres psql -U noktah -d spine_rehearsal -At \
  -c "SELECT count(*) FROM harvested_signals;" -c "SELECT count(*) FROM harvested_items;"
```

Still `656` and `691`.

## 2. Prove `init.sql` and the migrations agree (FR-029 / SC-006)

```bash
script/verify_schema_parity.sh
```

Exit `0`. Any difference in tables, columns, types, nullability, defaults, indexes, or constraints
fails the check. Run this after **any** edit to `init.sql` or to a migration — they are hand-written
in parallel and drift silently otherwise.

The parity build applies the full chain **including `006`**, against an empty database where the
harvest tables have no rows for its constraints to reject. `init.sql` must therefore carry those
constraints too (T016), or this check fails on `pg_constraint`.

## 3. Apply to the real database

```bash
# Same explicit list as step 1 — never glob, `.down.sql` matches `*.sql`.
for m in 000_schema_migrations 001_knowledge_records 002_harvested_items \
         003_harvested_signals 004_relational_spine 005_link_existing; do
  docker exec -i postgres psql -U noktah -d noktah_dashboard -v ON_ERROR_STOP=1 \
    < "config/postgres/migrations/$m.sql"
done
docker exec postgres psql -U noktah -d noktah_dashboard -c "SELECT version, applied_at FROM schema_migrations ORDER BY version;"
```

Do **not** run `006` yet — it enforces the account link and will fail until the backfill has run.
That failure is the design working.

## 4. Populate the roster

```bash
docker exec prefect python flows/roster_sync.py
```

Expect roughly: 21 clients created, ~30 aliases, 22 accounts, 22 roles. The summary must show
`Eskala` under `source_disagreements` and both Sumenep spellings resolved to one client — see
[contracts/roster-sync-report.md](./contracts/roster-sync-report.md).

Run it a second time. Everything falls to `unchanged`, nothing is created (SC-008).

## 5. Link the existing rows

```bash
docker exec prefect python flows/spine_backfill.py
```

Then verify the headline criteria:

```bash
docker exec postgres psql -U noktah -d noktah_dashboard \
  -c "SELECT count(*) FILTER (WHERE account_id IS NULL) AS unlinked_signals FROM harvested_signals;" \
  -c "SELECT count(*) FILTER (WHERE account_id IS NULL) AS unlinked_items FROM harvested_items;" \
  -c "SELECT count(*) FILTER (WHERE client_id IS NULL) AS unlinked_knowledge FROM knowledge_records WHERE superseded_by IS NULL;"
```

`unlinked_signals` and `unlinked_items` must both be `0` (SC-001, SC-003). `unlinked_knowledge`
should be `0` too, though a non-zero value here is *reported*, not fatal (FR-017).

## 6. Enforce the invariant

Only now:

```bash
docker exec -i postgres psql -U noktah -d noktah_dashboard < config/postgres/migrations/006_enforce_account_link.sql
```

## 7. The question this whole feature exists to answer (SC-002)

Owned versus competitor, in SQL, with no spreadsheet:

```sql
SELECT c.display_name, r.role, ah.handle_text, s.platform, count(*) AS posts
FROM harvested_signals s
JOIN accounts a              ON a.id = s.account_id
JOIN account_handles ah      ON ah.account_id = a.id AND ah.is_current
LEFT JOIN client_account_roles r ON r.account_id = a.id AND r.is_active
LEFT JOIN clients c          ON c.id = r.client_id
GROUP BY 1,2,3,4
ORDER BY 1,2;
```

Every one of the 656 rows appears under a client and a role. Two accounts appear with `NULL`
client and role — `rsmatadryap` and the opaque TikTok identifier — which is correct, not a gap.

Configured but never collected (FR-006):

```sql
SELECT c.display_name, ah.handle_text, r.role
FROM client_account_roles r
JOIN clients c ON c.id = r.client_id
JOIN account_handles ah ON ah.account_id = r.account_id AND ah.is_current
WHERE NOT EXISTS (SELECT 1 FROM harvested_signals s WHERE s.account_id = r.account_id);
```

Expect the three competitor handles with no signal rows.

## 8. Client name reconciliation (SC-004)

```sql
SELECT ca.alias_text, ca.source, c.display_name
FROM client_aliases ca JOIN clients c ON c.id = ca.client_id
WHERE ca.alias_key LIKE 'nirwana%' OR ca.alias_key LIKE '%lasik%'
ORDER BY c.display_name, ca.alias_text;
```

`Nirwana Coffee Space` must resolve to **Pamekasan only**. `Lasik Asyik` and
`LASIK Asyik by SMEC Tebet` must resolve to the same client. No alias appears twice.

Then confirm the ambiguity cannot come back:

```sql
INSERT INTO client_aliases (client_id, alias_key, alias_text, source)
SELECT id, 'nirwana coffee space', 'Nirwana Coffee Space', 'manual'
FROM clients WHERE display_name = 'Nirwana Coffee Space Sumenep';
-- expect: ERROR duplicate key value violates unique constraint
```

## 9. Briefs exist and are empty (SC-005)

```sql
SELECT count(*) FROM briefs;   -- 0
\d briefs
```

Zero rows, stable ids, resolves to a client and a run. Populating it is feature S-06.

## 10. Collection still works

```bash
docker exec prefect prefect deployment run 'social-harvest-window/harvest-monthly-ecky-dental-center'
```

Check the summary: items written carry `account_id`; any `items_skipped_unregistered` or
`items_skipped_inactive` is reported with its handle. A TikTok run should produce one
`account_follower_observations` row; an Instagram run produces none, which is expected — see
research R3.

## Rollback

```bash
# Reverse order. Explicit list for symmetry with the forward path.
for m in 006_enforce_account_link 005_link_existing 004_relational_spine; do
  docker exec -i postgres psql -U noktah -d noktah_dashboard \
    < "config/postgres/migrations/$m.down.sql"
done
```

Stop there. `000` through `003` are **not** reversed: `000` still records what was applied, and
`001`/`002`/`003` are baselines of tables that predate them, whose down scripts are deliberately
empty because dropping them would destroy the 62 / 656 / 691 rows. See
[contracts/migrations.md](./contracts/migrations.md).

After rollback the three original tables hold their original `62 / 656 / 691` rows and every
existing flow behaves as it did before this feature.
