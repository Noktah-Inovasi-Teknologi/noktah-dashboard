# Phase 1 Data Model: Relational Spine

**Feature**: `004-relational-spine` | **Date**: 2026-07-31 | **Spec**: [spec.md](./spec.md)

Target: the existing `noktah_dashboard` database on the `postgres` service. Extensions `pgcrypto`
and `pg_trgm` are already installed.

---

## Shape

```
                    client_aliases ──┐
                                     ▼
   knowledge_records ─────────────► clients ◄──────── briefs ────┐
                                     ▲                            │
                                     │                            ▼
                          client_account_roles                  runs
                                     │                            ▲
                                     ▼                            │
   account_handles ───────────────► accounts ◄── account_follower_observations
                                     ▲
                     ┌───────────────┴───────────────┐
              harvested_items                harvested_signals ──► runs
```

`accounts` is the hub the harvest tables attach to; `clients` is the hub everything client-shaped
attaches to; the two meet only through `client_account_roles`, which is what allows one account to
serve several clients (FR-008).

---

## New entities

### `clients`

One row per business. Identity is the surrogate `id`; `display_name` is free to change.

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | UUID | no | PK, `gen_random_uuid()` |
| `client_key` | TEXT | no | UNIQUE. Immutable slug assigned at creation; **never** rewritten on rename (FR-001) |
| `display_name` | TEXT | no | Current canonical name; non-empty |
| `is_active` | BOOLEAN | no | `true`. `false` = off the roster (FR-024) |
| `created_at` / `updated_at` | TIMESTAMPTZ | no | `now()` |

- `CHECK (btrim(display_name) <> '')`, `CHECK (btrim(client_key) <> '')`
- Renaming updates `display_name` and inserts the previous name into `client_aliases` with
  `source = 'former_name'`. `client_key` and `id` are untouched — that is what makes the identifier
  stable.

### `client_aliases`

Every name a client is known by, across all four sources found in research (R1). **This table is
the feature's answer to "reconciliation must be data, not code."**

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | UUID | no | PK |
| `client_id` | UUID | no | → `clients(id)` ON DELETE CASCADE |
| `alias_key` | TEXT | no | **UNIQUE** — normalised (lowercased, whitespace-collapsed). Enforces FR-003 |
| `alias_text` | TEXT | no | The name as observed |
| `source` | TEXT | no | CHECK ∈ `clients_sheet`, `components_block`, `knowledge_base`, `former_name`, `manual` |
| `created_at` | TIMESTAMPTZ | no | `now()` |

- The `UNIQUE (alias_key)` index **is** the conflict rejection FR-003 requires. Reconciliation
  catches the violation and reports it; it never resolves it by choosing.
- `clients_sheet` and `components_block` are separate sources because research R1 found the two
  spreadsheets disagree with each other, not merely with the knowledge base.
- Lookup is `WHERE alias_key = normalise($1)` — exact, case-insensitive, no similarity (FR-004).

### `accounts`

One presence on one platform. **Not** keyed by handle (FR-011).

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | UUID | no | PK |
| `platform` | TEXT | no | CHECK ∈ `instagram`, `tiktok` |
| `is_active` | BOOLEAN | no | `true`. `false` blocks storage of new content (FR-024a) |
| `created_at` / `updated_at` | TIMESTAMPTZ | no | `now()` |

- `UNIQUE (id, platform)` — redundant on its own, present so `account_handles` can carry a
  composite FK that guarantees a handle never drifts to a different platform than its account.

### `account_handles`

The handles an account has been observed under. Demoting the handle from identity to observation is
what stops a platform-side rename splitting an account in two.

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | UUID | no | PK |
| `account_id` | UUID | no | → `accounts(id)` ON DELETE CASCADE |
| `platform` | TEXT | no | Denormalised so uniqueness can be per platform |
| `handle_key` | TEXT | no | Lowercased lookup key |
| `handle_text` | TEXT | no | As observed — preserves display casing |
| `identifier_kind` | TEXT | no | CHECK ∈ `handle`, `opaque_id` (FR-012) |
| `is_current` | BOOLEAN | no | `true` |
| `first_seen_at` / `last_seen_at` | TIMESTAMPTZ | no | `now()` |

- `UNIQUE (platform, handle_key)` — a handle resolves to one account per platform (FR-011c). The
  same text on Instagram and TikTok is two accounts, per the spec's edge case.
- `UNIQUE (account_id) WHERE is_current` — exactly one current handle.
- `FOREIGN KEY (account_id, platform) → accounts(id, platform)`.
- A rename inserts a new row with `is_current = true` and clears the old one. Both keep resolving,
  so historical `profile_key` values in the harvest tables never dangle.
- **Merging two accounts is a maintainer action, never inferred** (FR-011b). No automatic
  same-handle-similarity logic exists anywhere.

### `client_account_roles`

The relationship, and the home of the role.

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | UUID | no | PK |
| `client_id` | UUID | no | → `clients(id)` |
| `account_id` | UUID | no | → `accounts(id)` |
| `role` | TEXT | no | CHECK ∈ `owned`, `competitor`, `reference` |
| `is_active` | BOOLEAN | no | `true` |
| `created_at` / `updated_at` | TIMESTAMPTZ | no | `now()` |

- `UNIQUE (client_id, account_id)` — one relationship per pair, so a role is never ambiguous
  (FR-010).
- `UNIQUE (account_id) WHERE role = 'owned' AND is_active` — one owner per account (FR-009).
  Scoped to `is_active` so a handover (old owner deactivated, new owner added) is representable.
- An account with **no** row here is the unattributed case: kept, queryable, excluded from every
  owned-versus-competitor answer (FR-008).

### `account_follower_observations`

Append-only. Rows are written **only** when the platform actually returned a count.

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | BIGSERIAL | no | PK |
| `account_id` | UUID | no | → `accounts(id)` |
| `observed_at` | TIMESTAMPTZ | no | `now()` |
| `follower_count` | BIGINT | no | **NOT NULL by design** — no row means no observation (FR-013a) |
| `run_id` | UUID | yes | → `runs(id)` |

- Index `(account_id, observed_at DESC)`.
- There is **no** `is_estimated`, no `interpolated_from`, and no nullable count. Constitution VI
  forbids an observation and an estimate sharing a field; the cheapest way to guarantee that is to
  make this table incapable of holding anything but an observation. Interpolation, if it is ever
  needed, gets its own table and its own name.
- Never updated. Re-observing appends (constitution VII).

### `runs`

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | UUID | no | PK |
| `kind` | TEXT | no | CHECK ∈ `collection`, `generation` |
| `flow_name` | TEXT | no | e.g. `social-harvest-window` |
| `flow_run_name` | TEXT | yes | Prefect's auto-generated run name — see below |
| `flow_run_id` | UUID | yes | Prefect's own run id |
| `client_id` | UUID | yes | → `clients(id)`; null for multi-client or unattributed runs |
| `started_at` | TIMESTAMPTZ | no | |
| `ended_at` | TIMESTAMPTZ | yes | null while in flight |
| `status` | TEXT | yes | |
| `summary` | JSONB | yes | The flow's own summary dict |

`flow_run_name` is load-bearing rather than decorative. Since the `harvest-monthly-*` deployments
stopped passing `harvest_name`, every delivered sheet row now carries the Prefect flow-run name in
its `harvest_name` column. Storing the same value here means a spreadsheet row and a database run
record join on a value that already exists in both, with no change to the delivery path.

### `briefs`

Structure only in this feature — zero rows at ship time (SC-005).

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | UUID | no | PK, assigned at generation time |
| `run_id` | UUID | yes | → `runs(id)` |
| `client_id` | UUID | no | → `clients(id)` |
| `bentuk` | TEXT | yes | `Post` / `Story` / `Short Video` |
| `topik` | TEXT | yes | |
| `planned_date` | DATE | yes | null for undated incidental ideas |
| `payload` | JSONB | yes | The full generated idea, rationale fields included |
| `created_at` | TIMESTAMPTZ | no | `now()` |

Deliberately loose: populating it is feature S-06, and over-constraining a table nothing writes to
yet would be guessing at a shape that feature has not settled.

### `schema_migrations`

| Column | Type | Null | Notes |
|---|---|---|---|
| `version` | TEXT | no | PK, e.g. `004_relational_spine` |
| `applied_at` | TIMESTAMPTZ | no | `now()` |

Records history. It does not gate execution — every migration is independently idempotent.

---

## Changes to existing entities

All additive. No column is dropped, retyped, or renamed; no existing constraint is altered.

### `harvested_signals`

| Added | Type | Null | Notes |
|---|---|---|---|
| `account_id` | UUID | yes → then constrained | → `accounts(id)` |
| `run_id` | UUID | yes | → `runs(id)` — groups observations by producing run |

- Index `(account_id)`.
- `uq_harvested_signal` on `(platform, content_id)` is **untouched** (FR-015).
- After backfill: `ALTER TABLE … ADD CONSTRAINT chk_signal_account CHECK (account_id IS NOT NULL)
  NOT VALID`, then `VALIDATE CONSTRAINT`. See research R6 — this gives FR-014's guarantee without
  the table rewrite and lock that `SET NOT NULL` requires, and reverses with one `DROP CONSTRAINT`.

### `harvested_items`

| Added | Type | Null | Notes |
|---|---|---|---|
| `account_id` | UUID | yes → then constrained | → `accounts(id)` |
| `run_id` | UUID | yes | → `runs(id)` |

- Index `(account_id)`. `uq_harvested_item` on `(platform, content_id, drive_target)` untouched.
- Same `NOT VALID` → `VALIDATE` treatment.
- `profile_key` **stays**, on both tables. It is the raw observed value and the only evidence of
  what the platform actually reported; `account_id` is the interpretation. Removing it would be
  destructive and would make the backfill unauditable.

### `knowledge_records`

| Added | Type | Null | Notes |
|---|---|---|---|
| `client_id` | UUID | yes | → `clients(id)` |

- Index `(client_id) WHERE superseded_by IS NULL`.
- Stays nullable — FR-017 requires unresolvable records to be **retained and reported**, so a
  not-null constraint would be wrong here, unlike on the harvest tables.
- The deferrable `superseded_by` FK and `uq_current_client_subject` are untouched. The supersession
  race regression test in `service/knowledge-base/tests/test_repository.py` must still pass.

---

## Backfill

Schema migrations cannot do this: the mapping lives in Google Sheets, which SQL cannot read. Two
idempotent flows, both safe to re-run.

**`roster-sync`** — clients, aliases, accounts, roles.

| Step | Source | Result |
|---|---|---|
| 1 | `Clients` worksheet + `COMPONENTS` block (union) | 21 clients; `Eskala` included from `COMPONENTS` alone |
| 2 | Both roster names, plus the 14 knowledge-base names | aliases, incl. both Sumenep spellings |
| 3 | `Clients` → `Instagram` / `TikTok` columns via `profile_handle` | 18 accounts + `owned` roles |
| 4 | `CLIENT_SOCIAL` block | 4 accounts + `competitor` roles |
| 5 | anything left | reported, never invented |

**`spine-backfill`** — one-time row linking.

| Step | Result |
|---|---|
| 1 | Create accounts for the 4 ledger handles absent from step 3/4 above (`jeceyehospital` already exists as a competitor; `rsmatadryap` and the opaque id get accounts with no role) |
| 2 | `harvested_signals.account_id` ← match on `(platform, lower(profile_key))` → `account_handles` |
| 3 | `harvested_items.account_id` ← same |
| 4 | `knowledge_records.client_id` ← match on `alias_key` |
| 5 | Report any row left unlinked; the `VALIDATE CONSTRAINT` migration runs only after this reports zero on the harvest tables |

Expected end state, from research R2: 656/656 signals linked, 691/691 items linked, and of the 62
knowledge records, all 14 names resolve.

---

## Alias seed

Derived once using the existing token-subset rule as a **proposal**, then stored. After this, the
rule is not consulted again (spec Assumptions).

| Alias | Source | → Client |
|---|---|---|
| `Lasik Asyik` | knowledge_base | LASIK Asyik by SMEC Tebet |
| `Klinik Utama GASA` | knowledge_base | Klinik Utama Gasa |
| `RS Mata SMEC` | knowledge_base | RS Mata SMEC Medan |
| `Toko Bakmi dan Kopitiam Pelita Delapan` | knowledge_base | Pelita Delapan |
| `Nirwana Pamekasan` | knowledge_base | Nirwana Coffee Space Pamekasan |
| `Nirwana Coffee Space` | knowledge_base | Nirwana Coffee Space Pamekasan |
| `Nirwana Sumenep` | knowledge_base | Nirwana Coffee Space Sumenep |
| `Nirwana Coffee Shop Sumenep` | clients_sheet | Nirwana Coffee Space Sumenep |
| `Nirwana Coffee Space Sumenep` | components_block | Nirwana Coffee Space Sumenep |

The two Nirwana entries the token-subset rule cannot separate are the reason this table exists:
`Nirwana Coffee Space` is currently a subset match for **both** outlets, and today resolves to
whichever the query returns first. Here it is one row pointing at one client, and the `UNIQUE`
index makes the ambiguity impossible to reintroduce.
