# Phase 0 Research: Relational Spine

**Feature**: `004-relational-spine` | **Date**: 2026-07-31 | **Spec**: [spec.md](./spec.md)

All figures below were read from the running stack on 2026-07-31, not assumed.

---

## R1. The roster has two sources, and they disagree

**Decision**: Reconciliation reads **both** the `Clients` worksheet and the `COMPONENTS` block of
the `Hashmaps` worksheet, treats their union as the roster, and reports every disagreement instead
of preferring one.

**Rationale**: The spec was written as though "the roster" were a single list of 21. Measured, it
is two lists that do not agree:

| | Count |
|---|---|
| `Clients` worksheet rows | 20 |
| `Hashmaps` → `COMPONENTS` entries | 21 |
| In `COMPONENTS`, absent from `Clients` | `Eskala`, `Nirwana Coffee Space Sumenep` |
| In `Clients`, absent from `COMPONENTS` | `Nirwana Coffee Shop Sumenep` |

Two distinct problems hide in that table:

1. **`Eskala` is a client with no `Clients` row at all.** It has a Jira component but no handles,
   no content-type amounts, no folder. Only the `COMPONENTS` block knows it exists.
2. **Sumenep is spelled differently in the two sheets** — `Nirwana Coffee **Shop** Sumenep` versus
   `Nirwana Coffee **Space** Sumenep`. Neither is a typo the other side knows about.

So the name-disagreement problem the spec set out to fix is one source wider than it recorded.
Counting the knowledge base and the social handle, a single client is named in **four** places:

| Source | Sumenep is called |
|---|---|
| `Clients` worksheet | `Nirwana Coffee Shop Sumenep` |
| `Hashmaps` → `COMPONENTS` | `Nirwana Coffee Space Sumenep` |
| `knowledge_records` | `Nirwana Sumenep` |
| Instagram handle | `nirwanacoffee_smnp` |

This strengthens rather than weakens the design: it is exactly what the alias table exists for.
The only change it forces is that `alias.source` must distinguish `clients_sheet` from
`components_block`, because the two are independent inputs that can each drift.

**Alternatives considered**: Treat `Clients` as authoritative and ignore `COMPONENTS` — rejected,
it would silently drop `Eskala`, violating FR-023. Treat `COMPONENTS` as authoritative — rejected,
it carries no handles, so the owned-account mapping would be lost.

---

## R2. The owned-account backfill is fully derivable

**Decision**: Seed `owned` roles from the `Clients` worksheet's `Instagram` and `TikTok` columns,
normalised through the existing `hashmap.profile_handle`.

**Rationale**: Verified exhaustively — the 18 handles in `harvested_signals` map 1:1 onto roster
clients with no gaps and no guesses:

- 17 Instagram handles + 1 TikTok handle (`karungjumbosidoarjo`) = **18**, exactly the count of
  distinct handles in the signal store.
- `profile_handle` already produces the same normalisation the harvest wrote (`klinikutama.sumenep`,
  `balakosa.rnp` and other dotted handles round-trip correctly).
- Three roster clients have no handle in either column (`Breko`, `The StarFit`, and `Eskala`, which
  has no row at all). They become clients with zero accounts, per the spec's edge case.

So SC-001 is achievable with zero manual mapping. The backfill is deterministic.

**Competitor roles** come from the `CLIENT_SOCIAL` block: `lasikindonesia`, `kmneyecare`,
`jeceyehospital` → `LASIK Asyik by SMEC Tebet`; `sebayadental` → `Ecky Dental Center`.

**Two ledger handles resolve to no client** and become accounts with no role, per FR-008:
`rsmatadryap`, and the opaque TikTok identifier
`MS4wLjABAAAAbSCgATHrl-SzAZ8n9B1Up_QTp2tEdIY1xm4T3CGUrbpF-Y51s2woFGy2z1gLaawf`, which is why
`identifier_kind` exists on the handle record (FR-012).

---

## R3. Instagram follower capture — **RESOLVED 2026-08-01 (see the update at the end)**

**Decision**: Implement follower capture platform-agnostically. TikTok populates from day one.
Instagram contributes nothing until a working route is found, and the feature ships regardless.

> **Superseded.** The route below was later made to work — the failure was a missing set of Relay
> feature-flag variables, **not** a rotated doc_id. See "Resolution" at the end of this section.
> The decision above is preserved because it was correct at the time and is why the feature could
> ship without waiting on this.

**Rationale**: The Instaloader lead recorded in the spec was probed against the live platform and
**does not work as described**. Two calls, then stopped per `.claude/rules/backend/roach.md`.

| Probe | Request | Result |
|---|---|---|
| 1 | `POST /graphql/query`, `doc_id=27937681195819736`, `variables={"id":…,"render_surface":"PROFILE"}`, roach's impersonated session | `HTTP 200` — `{"errors":[{"message":"execution error","severity":"CRITICAL"}×4],"data":null,"status":"ok"}` |
| 2 | Same, corrected to match Instaloader exactly: added `server_timestamps=true`, dropped `X-Requested-With` (Instaloader strips it via `empty_session_only`), `Accept: */*` | Byte-identical failure |

What the result rules **out** is as useful as what it shows:

- **Not throttling or blocking.** A `200` with a GraphQL-level error is not `429`, not `403`, not a
  challenge. The TLS impersonation and the burner cookies were accepted.
- **Not the app id.** Roach's `IG_APP_ID` is `936619743392459`, identical to Instaloader's.
- **Not the two header/param differences**, which probe 2 eliminated.

Remaining hypotheses, in order: the persisted-query `doc_id` has been rotated (Instagram rotates
these frequently, and a value read from Instaloader's `master` need not match what our session's
app version will execute); or the query requires session-derived parameters our cookie jar does not
carry. Both are cheap to test **once a real signal is worth spending probes on** — and neither is
worth spending them now, because nothing in this feature depends on the answer.

**Consequence for the plan**: FR-013a is satisfied by writing an observation whenever a count is
returned. At ship time (2026-08-01) that meant one TikTok account (`karungjumbosidoarjo`) and zero
Instagram accounts. That is the honest state, and the spec already permits it — absence is recorded
as absence, never as zero. The follower-capture task is sequenced last so it cannot gate US1.

**Update, same day, post-ship**: a live end-to-end test against real client `lasikasyik` and
competitor `jeceyehospital` captured `follower_count=312201` for `jeceyehospital` via
`_instagram_profile_info` (the `web_profile_info` route, **not** the GraphQL route probed above) —
the upstream breakage `.claude/rules/backend/roach.md` documents appears to be clearing. The same
run still returned nothing for `lasikasyik`, so this is intermittent/per-account, not a fix. No
further probing was done — this was incidental to testing the write path, not a new investigation,
and the platform-agnostic design already handles either outcome correctly without change.

---

### Resolution (2026-08-01) — the GraphQL route works; the diagnosis above was wrong

Prompted to check what instaloader had actually fixed, the answer turned out to invalidate this
section's conclusion rather than confirm it.

**The doc_id never rotated.** `27937681195819736` is still current in instaloader's
`Profile._obtain_metadata`. What changed is that Instagram now **requires** a set of Relay
feature-flag variables alongside `id`/`render_surface`:

```
__relay_internal__pv__PolarisCannesGuardianExperienceEnabledrelayprovider: True
__relay_internal__pv__PolarisCASB976ProfileEnabledrelayprovider:           False
__relay_internal__pv__PolarisRepostsConsumptionEnabledrelayprovider:       False
__relay_internal__pv__PolarisWebSchoolsEnabledrelayprovider:               False
enable_integrity_filters:                                                 True
```

Omit any of them and the server returns `HTTP 200` with
`{"errors": [… "execution error", "severity": "CRITICAL" …], "data": null}` — the exact response
both probes above got. That error is generic enough to look identical to a dead doc_id, an auth
problem, or a throttle, which is why two probes were spent ruling out the wrong things. **The
hypothesis this section proposed for "the next attempt" (rotated doc_id) was the wrong one.**

**Verified live, in order:**

| Step | Result |
|---|---|
| Probe with the full Relay variable set (`id=25025320`) | `HTTP 200`, `follower_count=685830423` |
| `_instagram_profile_info_graphql` added to `collect.py` as a failure-path fallback; roach rebuilt | roach healthy, 50/50 tests pass |
| Live `/list` for **`lasikasyik`** — the account that returned nothing hours earlier | **`follower_count=1342`** |

roach's own logs show the intended sequence exactly:

```
[instagram] lasikasyik: profile stats unavailable (HTTP 400: {"message":"Asset asset://laser.provider/ig_business_category_subvertical has been deleted..."})
[instagram] lasikasyik: follower stats via graphql fallback
[instagram] lasikasyik: follower_count=1342
```

**Consequence**: FR-013a now has a working Instagram path. `web_profile_info` remains broken
upstream and is left as the primary (it costs one request and returns more fields when it works);
the GraphQL query runs only when it yields no count, so the healthy path is unchanged. The
`user_id` the fallback needs comes from `_owner_id_from_entries`, which never depended on the
broken endpoint — which is why this was reachable at all.

Nothing in the relational-spine schema or flows changed: the design already recorded absence as
absence and a returned count as an observation, so the fix lands entirely inside roach.

**Alternatives considered**: Add Instaloader as a dependency — rejected outright, it issues plain
`requests` with no browser TLS fingerprint and would be throttled on sight (roach Rule #1); the
value was only ever the route, and the route does not currently work. Defer the observation table
until a working route exists — rejected, a follower count can only be observed in the present and
can never be backfilled, so the table must exist before the route is found, not after.

---

## R4. Migration mechanism — numbered SQL, idempotent, with a tracking table

**Decision**: Plain numbered `.sql` files under `config/postgres/migrations/`, applied by piping
into `psql`, matching the existing `001_knowledge_records.sql` convention. Each forward migration
has a matching `NNN_name.down.sql`. Add a small `schema_migrations(version, applied_at)` table.

**Rationale**: The spec's assumption is that migrations are applied by hand and no framework is
introduced. A tracking table is not a framework — it is one table that answers "what has been
applied here", which is currently unanswerable and which the parity check in R5 needs. Every
migration stays independently idempotent (`IF NOT EXISTS`, `ADD COLUMN IF NOT EXISTS`), so the
table records history rather than gating execution; a mis-recorded row cannot corrupt anything.

**The baseline migrations get a deliberately empty down script.** `002_harvested_items` and
`003_harvested_signals` reproduce tables that already exist in production. Their reversal would be
`DROP TABLE`, which destroys 691 and 656 rows that predate the migration — precisely what FR-027
forbids ("removes what it introduced without affecting data that existed before it"). Since those
migrations introduce nothing on an existing database, their correct reversal is a no-op, stated
explicitly in the file rather than left implied.

**Alternatives considered**: Alembic or `yoyo` — rejected, the constitution's Docker-first principle
and the existing one-file convention make a dependency and a new runtime step unjustifiable for
five migrations. Fold the new schema into `init.sql` only — rejected, that is the exact defect this
feature exists to fix.

---

## R5. Proving `init.sql` and the migration chain cannot drift (FR-029)

**Decision**: A parity check that builds two throwaway databases — one from `init.sql`, one from
the migration chain — and diffs `information_schema` (tables, columns, types, nullability, defaults)
plus `pg_indexes` and `pg_constraint`. Ship it as a script and as a pytest that skips without a
database.

**Rationale**: FR-029 is the requirement that keeps the two paths honest, and it is untestable by
inspection — the two files are written by hand and will drift the moment someone edits one. There is
precedent in this repo: the knowledge-base service already tests against a real disposable Postgres
rather than mocks, for exactly the reason that schema bugs do not reproduce against fakes
(`.claude/rules/backend/knowledge-base.md`).

**Alternatives considered**: Generate `init.sql` from the migrations at build time — rejected as a
larger change than the feature warrants, and it would alter first-boot behaviour for every
environment. Trust review — rejected; the audit found `harvested_items` and `harvested_signals`
already drifted out of migration coverage entirely, which is what produced this feature.

---

## R6. Making the account link mandatory without a destructive migration

**Decision**: Add `account_id` as a **nullable** column, backfill it to 100%, then add
`CHECK (account_id IS NOT NULL)` as `NOT VALID` and immediately `VALIDATE CONSTRAINT`.

**Rationale**: FR-014 wants every row linked; FR-026 forbids anything that could reject existing
rows, and FR-027 wants clean reversal. `SET NOT NULL` on a populated table takes an `ACCESS
EXCLUSIVE` lock and a full rewrite, and reversing it is awkward. The `NOT VALID` → `VALIDATE`
sequence takes only a `SHARE UPDATE EXCLUSIVE` lock, gives the identical guarantee, and reverses
with a single `DROP CONSTRAINT` that touches no data. It also separates cleanly: the column
migration can land before the backfill flow has run, and the constraint migration afterwards.

**Alternatives considered**: `NOT NULL` from the start — impossible, the column has no value for
existing rows at creation. Leave it nullable and rely on convention — rejected, that is the status
quo the feature is replacing, and SC-001 would be unenforceable.

---

## R7. Enforcing the identity rules in the database, not in code

**Decision**: Every uniqueness rule from the clarification session becomes an index or constraint.

| Rule | Enforcement |
|---|---|
| FR-003 — an alias resolves to at most one client | `UNIQUE (alias_key)` on `client_aliases` |
| FR-009 — at most one `owned` relationship per account | `UNIQUE (account_id) WHERE role = 'owned' AND is_active` |
| FR-010 — one relationship per client-account pair | `UNIQUE (client_id, account_id)` |
| FR-011c — a handle resolves to one account, per platform | `UNIQUE (platform, handle_key)` on `account_handles` |
| One current handle per account | `UNIQUE (account_id) WHERE is_current` |
| Handle's platform matches its account's | composite FK `(account_id, platform)` → `accounts(id, platform)` |

**Rationale**: The defect this feature exists to fix is a matching rule living in code where it
could be ambiguous and unobservable. Re-implementing the new rules as application checks would
reproduce that. A database constraint fails loudly at write time, is visible to anyone inspecting
the schema, and cannot be bypassed by a second code path. FR-003 and FR-009 both specify "rejected
and reported" — a unique violation *is* the rejection, and reconciliation catches it and reports.

**Alternatives considered**: Application-level validation — rejected as above. Triggers — rejected
as more machinery than partial indexes need.

---

## R8. Where the run identity comes from

**Decision**: `runs.flow_run_name` stores Prefect's auto-generated flow-run name, and
`runs.flow_run_id` its run id.

**Rationale**: A change made immediately before this plan turned out to matter here. The
`harvest-monthly-*` deployments no longer pass a `harvest_name`, so `_resolve_harvest_name`
([social_harvest.py:122](../../service/prefect/flows/common/social_harvest.py#L122)) falls back to
the Prefect flow-run name, which is now written into the `harvest_name` column of every delivered
sheet row. Storing that same value on the run record makes the Google Sheet row and the database
run record join on a value that already exists in both — without adding a column to the sheet or
changing the delivery path. Previously that column held the client name, which was identical across
every run and joined to nothing.

**Alternatives considered**: A separate generated run key — rejected, it would need to be plumbed
into the sheet writer, and a second identifier for the same run is what causes drift.

---

## R9. Naming for the new flows and tasks

**Decision**:

| Kind | Name |
|---|---|
| Flow | `roster-sync` — sheets → clients, aliases, accounts, roles |
| Flow | `spine-backfill` — one-time linking of existing rows |
| Tasks | `roster.client.upsert`, `roster.alias.upsert`, `roster.account.upsert`, `roster.role.upsert`, `roster.report.build` |
| Tasks | `spine.signal.link`, `spine.item.link`, `spine.knowledge.link` |
| Tasks | `social.account.resolve`, `social.account.record-followers` |
| Tasks | `run.record.start`, `run.record.finish` |

**Rationale**: Constitution I mandates kebab-case flows and `api-group.resource.action` tasks. The
constitution's "Flow & Task Naming for Roach and Songbird" section enumerates names for collection,
generation, and analysis but does not cover roster or schema work, so the general rule applies.
`roster-sync` deliberately echoes the existing `social-harvest-sync`, which is the closest existing
thing — a scheduled daily flow that reconciles a spreadsheet against the database.

---

## Unresolved, and deliberately so

| Item | Why it is not blocking |
|---|---|
| A working Instagram follower route (R3) | Nothing in the feature depends on it. Absence is a valid recorded state. Revisit when a probe budget is justified. |
| Whether `Eskala` should have a `Clients` row (R1) | An operator decision about the spreadsheet, not about this schema. Reconciliation reports it either way. |
| Whether `Nirwana Coffee Shop/Space Sumenep` should be spelled consistently (R1) | Same — the alias table makes the system correct regardless of which spelling wins. |
