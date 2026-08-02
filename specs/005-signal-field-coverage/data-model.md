# Phase 1 Data Model: Signal Field Coverage

**Feature**: `005-signal-field-coverage` | **Date**: 2026-08-02

Two new tables and one new column. All changes additive, per Constitution XII and the migration
contract in `.claude/rules/backend/schema.md`.

---

## 1. `harvested_signals.shares` (new column)

```
shares  BIGINT NULL
```

The public share/repost count for a content item, as observed. Nullable, because its emptiness is
meaningful and resolved by the availability record rather than by a sentinel.

**Validation**

- Non-negative when present. Zero is a real value (a post genuinely shared zero times) and MUST NOT
  be conflated with absence — FR-005, Edge Case "A field is present but zero".
- Populated from roach's `public_counts.shares`, which is already emitted on both TikTok paths and
  explicitly `None` on all Instagram paths.

**Why a column and not a JSONB metrics blob**: `views`/`likes`/`comments` are already columns;
splitting one metric into a different storage shape would break every existing query's symmetry for
no benefit. Additive column is the smaller change.

---

## 2. `field_availability` (new table)

The answer to *"can this ever be known?"*, keyed by (platform, content type, field). Mirrored from
`config/field_availability.yaml` by `field-availability-sync` (FR-001a/FR-001b) — `config/`, not
`data/`, because `.gitignore:111` would leave the latter untracked.

```
id                UUID PRIMARY KEY DEFAULT gen_random_uuid()
platform          TEXT NOT NULL CHECK (platform IN ('instagram','tiktok'))
content_type      TEXT NOT NULL      -- 'video','carousel','image','story', or 'account'
field_name        TEXT NOT NULL      -- 'likes','comments','views','shares','comment_text','follower_count'
status            TEXT NOT NULL CHECK (status IN (
                      'available',
                      'unavailable_platform_limit',
                      'not_collected_by_decision',
                      'inconclusive',
                      'undetermined'))
reason            TEXT NOT NULL
evidence          TEXT NULL          -- how it was determined; probe count where applicable
determined_on     DATE NOT NULL
source_version    TEXT NOT NULL      -- content hash / version of the YAML that produced this row
synced_at         TIMESTAMPTZ NOT NULL DEFAULT now()

UNIQUE (platform, content_type, field_name)
```

**Relationships**: none enforced by FK. It is a **reference table**, joined to `harvested_signals`
on `(platform, content_type)` at query time. Deliberately *not* FK-linked to observations — a
determination must be updatable without touching a single stored observation (FR-007).

**Validation**

- `status` is a **closed vocabulary** (FR-002a). A value outside the five is rejected at the
  database boundary by the CHECK, not merely validated in the sync task — a determination is
  reference data that other systems read, so the constraint belongs where it cannot be bypassed.
- `reason` is `NOT NULL` and non-empty. A status without a reason is not a determination; it is an
  assertion.
- `content_type = 'account'` carries account-level fields (`follower_count`), so one table answers
  both per-post and per-account availability rather than requiring consumers to check two places.

**Sync semantics** (FR-001b): idempotent upsert on the unique key. The sync MUST NOT delete rows
absent from the YAML without an explicit flag — a truncate-and-reload would silently erase a
determination if the file failed to parse.

### Seed content (from research R0, measured)

| platform | content_type | field | status | why |
|---|---|---|---|---|
| instagram | video | likes, comments, views | `available` | 291/291 populated |
| instagram | video | shares | `unavailable_platform_limit` | IG publishes no share count |
| instagram | carousel | likes | `available` | 291/291 |
| instagram | carousel | views | `unavailable_platform_limit` | no post-level play count for non-video |
| instagram | carousel | comments | `undetermined` → resolved by R1 probe | 0/291; candidate path identified |
| instagram | carousel | shares | `unavailable_platform_limit` | |
| instagram | image | *(as carousel)* | | 86 rows, same pattern |
| instagram | story | likes, comments, views, shares | `unavailable_platform_limit` | ephemeral; no public counts on any field |
| instagram | account | follower_count | `available` | via `web_profile_info` + GraphQL fallback |
| instagram | *any* | comment_text | `not_collected_by_decision` | R6 — ≈4–10× baseline requests |
| tiktok | video | likes, comments, views, shares | `available` | 26/26; shares parsed, pending storage |
| tiktok | carousel, image, story | *all* | `undetermined` | **0 rows ever observed** — code path exists but is unverified (Constitution VIII) |
| tiktok | account | follower_count | `available` | yt-dlp `channel_follower_count` |

---

## 3. `capture_outcomes` (new table)

The answer to *"was it known this time?"*. Append-only; one row per (content item, capture kind,
attempt).

```
id             BIGSERIAL PRIMARY KEY
platform       TEXT NOT NULL CHECK (platform IN ('instagram','tiktok'))
content_id     TEXT NOT NULL
capture_kind   TEXT NOT NULL CHECK (capture_kind IN (
                   'instagram_clip_stats',
                   'instagram_feed_stats',
                   'instagram_profile_info',
                   'tiktok_stats'))
outcome        TEXT NOT NULL CHECK (outcome IN ('success','no_match','failed','not_attempted'))
reason         TEXT NULL          -- required when outcome='failed'; classified (see below)
account_id     UUID NULL REFERENCES accounts(id)
run_id         UUID NULL REFERENCES runs(id)
observed_at    TIMESTAMPTZ NOT NULL DEFAULT now()

INDEX (platform, content_id, observed_at DESC)
INDEX (run_id)
```

**Key design decision — why `(platform, content_id)` and not `harvested_signals.id`:**

A capture can fail *before* a signal row exists. Keying to the signal row's id would make those
failures unrecordable, silently dropping precisely the failures Constitution V says must never be
dropped. `(platform, content_id)` is the item's natural identity and exists independently of
whether delivery succeeded. It matches the dedup key `harvested_items` already uses.

**Validation**

- Append-only. No `UPDATE`, no `DELETE` (FR-003a). Enforced by convention + test, as with
  `account_follower_observations`.
- `reason` MUST be present when `outcome = 'failed'`, drawn from the classified vocabulary
  (FR-004): `not_found`, `private`, `deleted`, `blocked`, `parse_failure`, `timeout`,
  `unexpected_structure`.
- `outcome = 'no_match'` (FR-003b) means the pass ran successfully and returned nothing for this
  item — the live case where the Reels-keyed clips response has no entry for a carousel.
  Distinguishing it from `failed` is what stops a normal non-Reel post looking like an error.

**Account-level captures**: `instagram_profile_info` rows carry `account_id` with `content_id` set
to the handle-derived account key, so a follower-capture miss (FR-018) is queryable through the
same table rather than a parallel mechanism.

---

## 4. Existing entities — what changes

| Entity | Change |
|---|---|
| `harvested_signals` | `+ shares` column. No other change; upsert semantics unchanged. |
| `account_follower_observations` | **No schema change.** Misses are recorded in `capture_outcomes`, not here — an observation table must contain only observations (Constitution VII). |
| `runs` | No schema change. `run_id` is referenced by `capture_outcomes`. |
| `accounts` | No change. |
| Reviewer sheet (`ACCOUNT_HEADER`) | `+ shares` as a trailing 20th column; see [contracts/delivery-surface.md](./contracts/delivery-surface.md). |

---

## 5. Provenance boundary for pre-feature rows (FR-006)

The 696 existing rows have **no** `capture_outcomes` entries and must not be counted as successful
captures. The distinction is derivable without backfilling anything:

```sql
-- A row has capture provenance only if a capture outcome exists for it.
LEFT JOIN capture_outcomes co
  ON co.platform = s.platform AND co.content_id = s.content_id
-- co.id IS NULL  ⇒  pre-feature row, provenance unknown
```

No sentinel column, no backfill, no rewriting of history. Absence of an outcome row *is* the
"lacking provenance" marker. Coverage statistics MUST exclude these rows from their denominator
rather than assuming success.

---

## 6. Query that proves the feature works

Resolving every empty value to exactly one cause (SC-002):

```sql
SELECT s.platform, s.content_type, s.content_id,
       fa.status        AS field_availability,
       co.outcome       AS capture_outcome,
       co.reason        AS failure_reason,
       CASE
         WHEN s.comments IS NOT NULL                       THEN 'present'
         WHEN fa.status = 'unavailable_platform_limit'     THEN 'platform does not publish it'
         WHEN fa.status = 'not_collected_by_decision'      THEN 'deliberately not collected'
         WHEN co.outcome = 'failed'                        THEN 'capture failed: ' || co.reason
         WHEN co.outcome = 'no_match'                      THEN 'capture ran, item not in result'
         WHEN co.id IS NULL                                THEN 'pre-feature row, provenance unknown'
         ELSE 'UNRESOLVED — this row is a bug'
       END AS why_empty
FROM harvested_signals s
LEFT JOIN field_availability fa
       ON fa.platform = s.platform
      AND fa.content_type = s.content_type
      AND fa.field_name = 'comments'
LEFT JOIN LATERAL (
      SELECT * FROM capture_outcomes c
      WHERE c.platform = s.platform AND c.content_id = s.content_id
      ORDER BY c.observed_at DESC LIMIT 1
) co ON true;
```

**The feature is correct when `why_empty = 'UNRESOLVED — this row is a bug'` returns zero rows for
content harvested after the feature ships.**
