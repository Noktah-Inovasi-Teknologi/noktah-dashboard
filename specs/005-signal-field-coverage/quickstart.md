# Quickstart: Signal Field Coverage

**Feature**: `005-signal-field-coverage` | **Date**: 2026-08-02

Validation guide. Every step below is **offline** — none issues a platform request — except step 7,
which is the single capped probe and is explicitly marked.

## Prerequisites

```bash
docker-compose ps          # postgres, prefect, prefect-worker, roach healthy
```

Schema tests need a reachable Postgres at `SPINE_TEST_DATABASE_URL`; they auto-skip otherwise
(`service/prefect/tests/conftest.py`).

---

## 1. Rehearse the migration before touching production

Never apply 007 to `noktah_dashboard` first.

```bash
script/db_rehearsal.sh create
docker exec -i postgres psql -U "$POSTGRES_USER" -d spine_rehearsal \
  < config/postgres/migrations/007_signal_field_coverage.sql
```

Expected: no error; re-running is a no-op (idempotence contract).

**Do not glob the migrations directory** — `007_*.sql` also matches `007_*.down.sql`, and applying
both back-to-back silently undoes the migration.

## 2. Prove init.sql and the migration chain still agree

```bash
script/verify_schema_parity.sh
```

Expected: exit 0. This is the step that catches the classic failure — mirroring the migration into
`init.sql` as an inline `REFERENCES` when the migration used a named `ALTER … ADD CONSTRAINT`
produces different `pg_constraint` names and fails here.

```bash
cd service/prefect && ./.venv/Scripts/python.exe -m pytest tests/ -m schema -v
```

## 3. Load the availability determinations

```bash
docker exec prefect python flows/field_availability_sync.py --validate-only
docker exec prefect python flows/field_availability_sync.py
```

Expected `summary`: `rejected: 0`, and `coverage_gaps: []` — every (platform, content_type, field)
present in `harvested_signals` has a determination (SC-001).

Verify the closed vocabulary is enforced at the database boundary, not just in the task:

```bash
docker exec postgres psql -U noktah -d noktah_dashboard -c \
  "INSERT INTO field_availability (platform, content_type, field_name, status, reason, determined_on, source_version)
   VALUES ('instagram','video','likes','probably_fine','x',current_date,'v1');"
```

Expected: **fails** on the CHECK constraint. If this succeeds, FR-002a is not implemented.

## 4. Prove the 361→377 non-video rows are now explained (SC-008)

```bash
docker exec postgres psql -U noktah -d noktah_dashboard -c "
  SELECT s.content_type, fa.status, count(*)
  FROM harvested_signals s
  JOIN field_availability fa
    ON fa.platform = s.platform AND fa.content_type = s.content_type
   AND fa.field_name = 'views'
  WHERE s.platform = 'instagram' AND s.views IS NULL
  GROUP BY 1,2;"
```

Expected: every Instagram carousel/image/story row resolves to
`unavailable_platform_limit` — classified as a platform limit, **not** counted as a collection
failure.

## 5. Run the resolution query — the feature's actual acceptance test

Run the `why_empty` query in [data-model.md §6](./data-model.md).

**Expected: zero rows returning `UNRESOLVED — this row is a bug`** for content harvested after the
feature ships (SC-002). Pre-feature rows returning `pre-feature row, provenance unknown` are
correct, not failures (FR-006).

## 6. Verify share counts survive the storage boundary

Trigger a TikTok harvest that was going to run anyway, then:

```bash
docker exec postgres psql -U noktah -d noktah_dashboard -c "
  SELECT content_type, count(*) AS rows, count(shares) AS with_shares
  FROM harvested_signals WHERE platform='tiktok' GROUP BY 1;"
```

Expected: `with_shares = rows` for video (SC-003). Confirm on the reviewer sheet that `shares` is
the trailing column and `advertisement` still round-trips through `social-harvest-sync` — that is
the regression FR-016b guards against.

Baseline check (SC-004): the run's request count must be **unchanged**. Share capture parses
already-fetched payloads.

## 7. The single probe — ISSUES LIVE REQUESTS

**Everything above is offline. This step is not.** Budget: ≤10 requests, expected 1. Target: a
designated **non-client** account. Stop immediately on any 429 or challenge (FR-012c) and record
`inconclusive`.

Question: does one page of `/api/v1/feed/user/<user_id>/` carry `comment_count` for carousel and
image items — and does it also carry `play_count` for video?

- **Present + play_count present** → the feed pass subsumes `_instagram_clip_stats`; marginal
  requests **≤ 0**.
- **Present, no play_count** → feed pass runs alongside clips; **+1 to +2 per account per run**
  (+10–29% of the R5 baseline). State and accept per FR-024 before merging.
- **Absent** → record `unavailable_platform_limit` with evidence and **write no collection code**
  (FR-011). This is a successful outcome.

Record the probe count in the determination's `evidence` field either way (FR-012b).

## 8. Confirm no collection-rate increase (SC-004, SC-010)

```bash
docker-compose logs prefect-worker | grep -i "request_count\|capture_kind"
```

Compare per-account request counts before and after. Any increase must trace to an accepted FR-024
statement. The comment-text determination must read `not_collected_by_decision` — never
`unavailable_platform_limit` (FR-023).

---

## Rollback

```bash
docker exec -i postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  < config/postgres/migrations/007_signal_field_coverage.down.sql
```

Drops the two new tables and the `shares` column. Safe: all three are additive and hold only
data this feature created. No pre-existing row is touched.

If roach was rebuilt for step 7's outcome, revert `collect.py` and
`docker-compose up -d --build roach` — roach source is baked into the image, not volume-mounted.
