# Contract: Observation Record

**Task**: `social.observation.record` | **Feature**: 006-longitudinal-metric-capture

Records one metric capture for one content item. The only writer to `metric_observations`.

## Signature

```python
@task(name="social.observation.record", retries=2, retry_delay_seconds=10)
async def social_observation_record(
    platform: str,              # 'instagram' | 'tiktok'
    content_id: str,
    views: Any = None,          # coerced via the existing _coerce_count ("1.2K" -> 1200)
    likes: Any = None,
    comments: Any = None,
    shares: Any = None,
    observed_at: Optional[str] = None,   # ISO 8601; defaults to now() at the DB
    provenance: str = "captured",        # 'captured' | 'legacy'
    account_id: Optional[str] = None,
    run_id: Optional[str] = None,
) -> Optional[int]:                      # observation id, or None if nothing was stored
```

`retries=2` is correct here despite Constitution II's collection carve-out: this task performs **no
platform I/O**. It writes to the local database from values already in memory. The carve-out exists
to stop rapid retry against a *blocking target*; there is no target.

## Behaviour

1. Coerce all four counts. Reuse `_coerce_count` — its `"1.2K" → 1200` quantization is why FR-014
   and midrank handling exist elsewhere, and observations must quantize identically or a delta will
   register phantom movement.
2. If **all four** coerced values are NULL, write nothing and return `None`. The caller records a
   `capture_outcomes` row with `outcome = 'no_match'` instead. An empty observation row is forbidden
   by `chk_metric_observations_has_a_value`.
3. Insert. On conflict with `uq_metric_observation_capture` (same item, same instant), do nothing
   and return the existing id — makes the pass safely retryable.
4. Never update an existing row's values (FR-007).

## Guarantees

- **Append-only.** This task issues no `UPDATE` and no `DELETE`. Ever.
- **A zero is stored as zero.** Only a genuinely absent value is NULL. Do not COALESCE a missing
  count to 0 — Principle VI, and the same reasoning as `shares` in `social.signal.record`.
- **Negative movement is not this task's concern.** It records what it was given; comparison happens
  in derivation.

## Caller contract (harvest engine)

Runs as one pass over `all_items`, positioned after account resolution and before the per-item
download loop ([research.md R4](../research.md)).

```
for item in all_items:                      # NOT the depth-selected `items`
    counts = item.get("public_counts") or {}
    obs_id = await social_observation_record(..., run_id=run_record_id, account_id=account_id)
    await social_capture_record_outcome(
        capture_kind="metric_refresh",
        outcome="success" if obs_id else "no_match", ...)
```

Failure of this pass is **best-effort and non-fatal** — a harvest that delivered content must not be
failed by an observation write, exactly as `social.signal.record` is treated today. Wrap, log, and
record the miss.

Run summary gains: `observations_recorded`, `observations_skipped_no_counts`,
`observations_failed`.

## Latest-value update (FR-010 / FR-010a)

Maintaining `harvested_signals` as the latest-value convenience is a **separate** write that touches
metric columns and `harvested_at` only.

**It MUST NOT write `subtitle`, `content_flow`, `summary`, or `advertisement`.** The existing
`social.signal.record` upsert overwrites the three analysis columns unconditionally
([social_tasks.py:292-294](../../../service/prefect/tasks/social_tasks.py#L292-L294)); routing a
refresh through it would blank analysis output on every refreshed row, and this feature cannot
re-create it — analysis is out of scope and re-running it would breach FR-025. Use a dedicated
metrics-only statement.

## Legacy seeding (FR-021)

One-time, via `velocity.observation.backfill`:

```sql
INSERT INTO metric_observations
    (platform, content_id, observed_at, provenance, views, likes, comments, shares, account_id)
SELECT platform, content_id, harvested_at, 'legacy', views, likes, comments, shares, account_id
FROM harvested_signals s
WHERE (views IS NOT NULL OR likes IS NOT NULL OR comments IS NOT NULL OR shares IS NOT NULL)
  AND NOT EXISTS (SELECT 1 FROM metric_observations o
                  WHERE o.platform = s.platform AND o.content_id = s.content_id)
```

- `observed_at` is the row's real `harvested_at` — no time is invented (Principle VI).
- `NOT EXISTS` makes re-running harmless.
- Rows with no metrics at all are skipped; they have nothing to observe and would violate the
  has-a-value CHECK.
- Reports the count seeded. Per R1 that is ~819 and rising, not the spec's literal 656.
