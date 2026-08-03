# Quickstart: Longitudinal Metric Capture

**Feature**: 006-longitudinal-metric-capture

Validates that engagement stops being frozen, that velocity is derived from irregular observations,
and that every absence resolves to exactly one reason — without spending a single extra platform
request or model call.

Set once:

```bash
PSQL="docker exec postgres psql -U noktah -d noktah_dashboard"
```

---

## 0. Baseline — capture the "before" numbers

Run **before** applying anything. These are what SC-003 and SC-004 are measured against.

```bash
$PSQL -c "
  SELECT count(*) AS signal_rows, count(DISTINCT profile_key) AS profiles,
         max(harvested_at)::date AS newest FROM harvested_signals;"
```

Expected on 2026-08-03: `819 | 22 | 2026-08-01`. It will be higher by the time you run this — that
is R1's point, and why nothing is pinned to a literal count.

Re-observability ceiling (R2) — how much of the corpus this feature can ever reach:

```bash
$PSQL -c "
  WITH ranked AS (
    SELECT profile_key, content_id,
           row_number() OVER (PARTITION BY profile_key ORDER BY published_at DESC NULLS LAST) AS rn
    FROM harvested_signals)
  SELECT count(*) FILTER (WHERE rn <= 30) AS reachable,
         count(*) FILTER (WHERE rn > 30)  AS permanently_frozen
  FROM ranked;"
```

Expected: about `567 | 252`. The second number never becomes reachable within this feature's scope.

---

## 1. Rehearse the migration — never straight to production

```bash
script/db_rehearsal.sh create
docker exec -i postgres psql -U noktah -d spine_rehearsal \
  < config/postgres/migrations/008_observation_history.sql
```

Confirm the objects exist and the vocabulary widened:

```bash
docker exec postgres psql -U noktah -d spine_rehearsal -c "
  SELECT tablename FROM pg_tables WHERE tablename IN ('metric_observations','velocity_intervals');
  SELECT viewname FROM pg_views WHERE viewname = 'velocity_status';
  SELECT pg_get_constraintdef(oid) FROM pg_constraint
   WHERE conname = 'chk_capture_outcomes_kind';"
```

The CHECK must list `metric_refresh` **plus all four original kinds**. A missing original is a
regression — the replacement must be a strict superset.

Prove reversibility, then that re-applying is harmless:

```bash
docker exec -i postgres psql -U noktah -d spine_rehearsal \
  < config/postgres/migrations/008_observation_history.down.sql
docker exec -i postgres psql -U noktah -d spine_rehearsal \
  < config/postgres/migrations/008_observation_history.sql
```

---

## 2. Prove the two schema paths still agree

```bash
script/verify_schema_parity.sh
```

Must exit 0. This is the check that catches `init.sql` drifting from the migration chain — declare
constraints in `init.sql` as **named ALTERs matching the migration exactly**, not inline
`REFERENCES`, or parity fails on constraint names alone.

---

## 3. Seed legacy observations (one-time)

Dry run first:

```bash
docker exec prefect python flows/observation_backfill.py --validate-only
```

Reports how many rows would be seeded — expect roughly the `signal_rows` count from step 0, minus
any row with no metric at all. Then:

```bash
docker exec prefect python flows/observation_backfill.py
```

Verify every seeded row is legacy, and that none invented a timestamp:

```bash
$PSQL -c "
  SELECT provenance, count(*), min(observed_at)::date, max(observed_at)::date
  FROM metric_observations GROUP BY 1;"
```

Expect one `legacy` group whose date range matches `harvested_signals.harvested_at`
(2026-07-15 → 2026-08-01), **not** today's date. Today's date here means the seeding invented a
capture time — a Principle VI violation, and a hard stop.

Re-run the backfill once more; the counts must not change (idempotence).

---

## 4. Observe without downloading — the core claim (SC-002)

Run a monthly harvest against a profile whose content is already fully harvested:

```bash
docker exec prefect prefect deployment run \
  'social-harvest-window/harvest-monthly-ecky-dental-center'
docker-compose logs -f prefect-worker
```

In the run summary, confirm:

- `observations_recorded` > 0
- `items_skipped_dedup` ≈ the same as before (the gate still works — FR-002)
- `items_collected` is 0 or near it (nothing new to download)

Then confirm no download and no model call happened for the refreshed items:

```bash
$PSQL -c "
  SELECT capture_kind, outcome, reason, count(*)
  FROM capture_outcomes
  WHERE capture_kind = 'metric_refresh' AND observed_at > now() - interval '1 hour'
  GROUP BY 1,2,3 ORDER BY 4 DESC;"
```

**SC-004 check** — model spend must not move. The harvest log's OpenRouter usage lines should be
absent for skipped items; a refresh triggering `social.item.analyze` is a direct FR-025 failure.

**SC-003 check** — request volume must not move. Compare `request_stats` in this run's summary
against a pre-feature run for the same profile. Same listing passes, same direct API requests.

Confirm observations landed on items *outside* the 31-day window (FR-004) — this is the difference
between the feature working and barely working:

```bash
$PSQL -c "
  SELECT count(*) AS observed_outside_window
  FROM metric_observations o
  JOIN harvested_signals s USING (platform, content_id)
  WHERE o.provenance = 'captured'
    AND s.published_at < now() - interval '31 days';"
```

Must be > 0. Zero means the pass is iterating the depth-selected `items` instead of `all_items`.

---

## 5. Derive velocity

```bash
docker exec prefect python flows/velocity_derive.py --validate-only
docker exec prefect python flows/velocity_derive.py
```

```bash
$PSQL -c "
  SELECT count(*) AS intervals,
         count(*) FILTER (WHERE rate_likes_per_day IS NOT NULL) AS rated,
         count(*) FILTER (WHERE rate_withheld_reason = 'interval_too_short') AS withheld,
         count(*) FILTER (WHERE is_acceleration) AS accelerations
  FROM velocity_intervals;"
```

Irregular spacing is handled correctly (SC-007) — elapsed time must vary, and rates must not be
proportional to a fixed interval:

```bash
$PSQL -c "
  SELECT content_id, round(elapsed_seconds/86400.0, 2) AS days,
         delta_likes, round(rate_likes_per_day::numeric, 3) AS per_day
  FROM velocity_intervals
  WHERE rate_likes_per_day IS NOT NULL
  ORDER BY elapsed_seconds LIMIT 10;"
```

Check `per_day × days ≈ delta_likes` on each row. If `per_day` equals `delta_likes` regardless of
`days`, elapsed time is being ignored — the FR-013 failure this feature exists to prevent.

**Idempotence (FR-016c)**: re-run and confirm the counts above are unchanged.

---

## 6. Every absence has exactly one reason (SC-009)

```bash
$PSQL -c "SELECT reason, count(*) FROM velocity_status GROUP BY 1 ORDER BY 2 DESC;"
```

**`UNRESOLVED` must be absent.** Any row there is a defect, not a curiosity.

Immediately after step 3 and before any harvest, expect everything in `legacy_only` — that is
correct, not a failure. After one monthly cycle, expect roughly 567 items to have moved on and
roughly 252 to remain unreachable.

Confirm pre-existing items are excluded from velocity and not treated as zero (SC-008):

```bash
$PSQL -c "
  SELECT count(*) AS legacy_with_velocity FROM velocity_status
  WHERE reason = 'legacy_only' AND has_velocity;"
```

Must be `0`.

---

## 7. Analysis output survived the refresh (FR-010a)

The regression this feature is most likely to cause, and the least visible:

```bash
$PSQL -c "
  SELECT count(*) FILTER (WHERE summary IS NOT NULL AND btrim(summary) <> '') AS has_summary,
         count(*) FILTER (WHERE content_flow IS NOT NULL AND btrim(content_flow) <> '') AS has_flow,
         count(*) AS total
  FROM harvested_signals;"
```

Record these **before** step 4 and compare after. Any decrease means a refresh wrote empty analysis
over stored analysis — stop and fix, because this feature cannot regenerate it (analysis is out of
scope, and re-running it would breach FR-025).

---

## 8. Test suite

```bash
cd service/prefect
./.venv/Scripts/python.exe -m pytest tests/ -m schema -v
./.venv/Scripts/python.exe -m pytest tests/test_velocity_derivation.py tests/test_metric_observation.py \
                                    tests/test_absence_classification.py -v
```

Schema-marked tests auto-skip when no database is reachable at `SPINE_TEST_DATABASE_URL` — a green
run that skipped everything proves nothing. Confirm they actually ran.

---

## 9. Production

Only after steps 1–8 pass against the rehearsal clone:

```bash
docker exec -i postgres psql -U noktah -d noktah_dashboard \
  < config/postgres/migrations/008_observation_history.sql
docker exec prefect python flows/observation_backfill.py
docker exec prefect prefect deploy --all
script/db_rehearsal.sh drop
```

Then wait for the next monthly cycle. **The feature cannot be fully validated on the day it ships** —
its central claim is about a second observation, and the second observation arrives next month.
Re-run steps 5 and 6 then.
