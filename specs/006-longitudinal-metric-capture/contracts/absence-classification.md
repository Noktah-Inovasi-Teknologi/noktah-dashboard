# Contract: Absence Classification

**View**: `velocity_status` | **Feature**: 006-longitudinal-metric-capture

Answers FR-024: for any content item with no velocity, **exactly one** reason applies. Same shape as
feature 005's why-empty query — a single ordered `CASE` with a terminal `UNRESOLVED` branch that
must always return zero rows.

## Why a view

History status is derived, never stored (Q3 clarification). A stored classification column would
need maintaining on every observation write and could silently disagree with the observations it
describes — the exact failure the clarification rejected. A view cannot drift: it is recomputed from
the observations each time it is read.

Cost is irrelevant at this scale — 819 items, one index scan.

## Shape

```sql
CREATE OR REPLACE VIEW velocity_status AS
WITH obs AS (
    SELECT platform, content_id,
           count(*)                                        AS observation_count,
           count(*) FILTER (WHERE provenance = 'captured') AS captured_count
    FROM metric_observations GROUP BY 1, 2
), iv AS (
    SELECT platform, content_id,
           count(*)                                              AS interval_count,
           count(*) FILTER (WHERE rate_withheld_reason IS NULL)  AS rated_count
    FROM velocity_intervals GROUP BY 1, 2
), last_outcome AS (
    SELECT DISTINCT ON (platform, content_id) platform, content_id, outcome, reason
    FROM capture_outcomes WHERE capture_kind = 'metric_refresh'
    ORDER BY platform, content_id, observed_at DESC
)
SELECT o.platform, o.content_id, o.observation_count,
       COALESCE(iv.rated_count, 0) > 0 AS has_velocity,
       CASE
           WHEN COALESCE(iv.rated_count, 0) > 0            THEN 'has_velocity'
           WHEN o.observation_count = 1
                AND o.captured_count = 0                   THEN 'legacy_only'
           WHEN o.observation_count = 1                    THEN 'observed_once'
           WHEN iv.interval_count IS NULL                  THEN 'not_yet_derived'
           WHEN iv.rated_count = 0                         THEN 'all_intervals_too_short'
           WHEN lo.reason IN ('aged_out_of_listing',
                              'absent_within_reach',
                              'deleted')                   THEN 'no_longer_observable'
           ELSE 'UNRESOLVED'
       END AS reason
FROM obs o
LEFT JOIN iv ON iv.platform = o.platform AND iv.content_id = o.content_id
LEFT JOIN last_outcome lo ON lo.platform = o.platform AND lo.content_id = o.content_id;
```

## Vocabulary

| Reason | Means | Actionable? |
|---|---|---|
| `has_velocity` | ≥1 rated interval | — |
| `legacy_only` | Only a seeded pre-feature observation (FR-021a) | No — wait for the next harvest |
| `observed_once` | Captured once by this feature | No — wait one cycle |
| `not_yet_derived` | ≥2 observations, derivation hasn't run | Yes — run `velocity-derive` |
| `all_intervals_too_short` | Every interval under the 24h floor | Rare; investigate duplicate runs |
| `no_longer_observable` | Beyond listing reach, absent within reach, or confirmed removed | No — expected for ~252 rows (R2) |
| `UNRESOLVED` | **Always zero rows** | Yes — a defect |

Branch order matters. `legacy_only` is tested before `observed_once` because both have exactly one
observation and only provenance separates them; the `captured_count = 0` term is what distinguishes
them, and dropping it would silently reclassify every pre-feature item.

## Invariants (asserted by test)

1. **`UNRESOLVED` returns zero rows.** `test_absence_classification.py` fails the build otherwise —
   this is the FR-024 guarantee, and the same discipline as feature 005's `why_empty` query.
2. **Every item appears exactly once.** No item is missing and none is duplicated.
3. **`has_velocity` agrees with `reason`.** `has_velocity` is true iff `reason = 'has_velocity'`.
4. **Legacy items never report `observed_once`.**

## Expected distribution at first run

From [research.md R2](../research.md), immediately after seeding and before any refresh:

| Reason | Expected | Why |
|---|---|---|
| `legacy_only` | ~819 (all) | Everything is seeded, nothing re-observed yet |
| everything else | 0 | |

After one monthly cycle, roughly 567 should move to `observed_once` or `has_velocity` and roughly
252 should remain unreachable — surfacing as `legacy_only` until a refresh attempt records
`aged_out_of_listing`, after which they read `no_longer_observable`.

**That transition is the feature's real acceptance test**, and it cannot be evaluated until a
monthly harvest has run. Do not read a store full of `legacy_only` on day one as a failure.

## Operator query

```bash
docker exec postgres psql -U noktah -d noktah_dashboard -c "
  SELECT reason, count(*) FROM velocity_status GROUP BY 1 ORDER BY 2 DESC;"
```
