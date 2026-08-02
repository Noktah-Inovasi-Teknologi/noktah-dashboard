# Contract: Capture Outcomes

**Feature**: `005-signal-field-coverage` | Satisfies FR-003, FR-003a, FR-003b, FR-004, FR-006, FR-018

Records, per content item and per supplementary capture pass, whether that pass actually produced a
value **this run**. Append-only.

## 1. Outcome vocabulary (closed)

| Outcome | Means | Typical cause |
|---|---|---|
| `success` | The pass ran and returned a value for this item | Normal |
| `no_match` | The pass ran successfully but returned **nothing for this item** | A carousel is absent from a Reels-keyed clips response |
| `failed` | The pass itself failed | HTTP error, throttle, parse failure, timeout |
| `not_attempted` | The pass was skipped for this item | No cookie session; the pass was disabled; upstream dependency missing |

**`no_match` vs `failed` is the distinction that carries the feature.** Today, a carousel absent
from the clips response and a clips call that 429'd both produce an empty `comments`. Collapsing
them would leave the corpus exactly as ambiguous as it is now, with more tables.

## 2. Failure reason vocabulary (closed; required when `outcome = 'failed'`)

Drawn from the collection-failure classification Constitution X already mandates:

`not_found` · `private` · `deleted` · `blocked` · `parse_failure` · `timeout` ·
`unexpected_structure`

A `failed` outcome with no reason MUST be rejected by the task, not defaulted. "Unknown failure" is
a classification decision that belongs to a human reading a traceback, not to a `COALESCE`.

## 3. Capture kinds

| Kind | Pass | Scope |
|---|---|---|
| `instagram_clip_stats` | `_instagram_clip_stats` — Reels grid | per item |
| `instagram_feed_stats` | `/v1/feed/user/` comment counts — **only if the R1 probe confirms** | per item |
| `instagram_profile_info` | `_instagram_profile_info` + GraphQL fallback | per account |
| `tiktok_stats` | gallery-dl `stats` object parse | per item |

The **primary listing is not a capture kind.** Its failure aborts the harvest of that profile and
is already recorded at run level; modelling it here would double-count.

## 4. Write contract

```python
@task(name="social.capture.record-outcome")
async def social_capture_record_outcome(
    platform: str,
    content_id: str,
    capture_kind: str,
    outcome: str,
    reason: str | None = None,
    account_id: str | None = None,
    run_id: str | None = None,
) -> None
```

- **Append-only** (FR-003a). Insert only — no upsert, no `ON CONFLICT DO UPDATE`. A re-harvest that
  re-attempts a capture adds a row.
- **Best-effort at the call site**: a failure to record an outcome MUST NOT abort a harvest, matching
  how `social.signal.record` already behaves. An unrecorded outcome degrades to "pre-feature row,
  provenance unknown" — the safe reading.
- Validates `outcome` and `reason` against the closed vocabularies and raises on violation
  (Constitution I: tasks raise).
- **Not** subject to the `retries=2` mandate — it is a local database write, and it is on the path
  of collection tasks whose retry carve-out applies (Constitution II).

## 5. Account-level captures

`instagram_profile_info` rows carry `account_id` and use the account key as `content_id`. This is
how FR-018 is satisfied: a follower-capture miss becomes a queryable per-account fact rather than a
`follower_capture_missed` list that exists only inside one run's summary.

```sql
-- Accounts with no follower observation this run, and why (FR-018)
SELECT a.id, co.outcome, co.reason
FROM accounts a
LEFT JOIN capture_outcomes co
       ON co.account_id = a.id
      AND co.capture_kind = 'instagram_profile_info'
      AND co.run_id = $1
WHERE NOT EXISTS (
    SELECT 1 FROM account_follower_observations o
    WHERE o.account_id = a.id AND o.run_id = $1
);
-- co.outcome IS NULL ⇒ never attempted; 'failed' ⇒ attempted and missed, with reason
```

## 6. Retention

Never pruned as part of this feature. These rows are the evidence base for future velocity and
coverage work (Constitution VII: "this principle is load-bearing, not stylistic"). At ~1–2 rows per
item per harvest and a monthly cadence, growth is trivially bounded.

## 7. What this contract does not cover

- It does not say whether a field is *knowable* — that is
  [field-availability.md](./field-availability.md).
- It does not store values, only whether a value was obtained. The value itself stays in
  `harvested_signals`.
