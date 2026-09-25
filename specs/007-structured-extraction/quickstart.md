# Quickstart — Validating Structured Extraction Output

**Feature**: `007-structured-extraction` | **Date**: 2026-08-03

Runnable checks that prove the feature works end to end. Ordered so the free ones run first — steps
1–6 cost nothing, step 7 is the first that spends money.

Baseline as measured 2026-08-03: **819 signal rows, 855 items** (video 364 · carousel 323 · image
130 · story 2), 18 items with `analysis_status = 'failed'`, migrations applied through `008`.

---

## Prerequisites

```bash
docker-compose ps          # postgres, prefect, prefect-worker, roach all healthy
docker exec postgres psql -U noktah -d noktah_dashboard -c \
  "SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 1;"   # expect 008_...
```

---

## 1. Apply the migration and prove both schema paths agree

```bash
docker exec -i postgres psql -U noktah -d noktah_dashboard \
  < config/postgres/migrations/009_structured_extraction.sql

script/verify_schema_parity.sh          # must exit 0
```

Never glob the migrations directory — `009_*.sql` also matches `009_*.down.sql`, and applying a
migration followed by its own reversal silently undoes it (`.claude/rules/backend/schema.md`).

Expect seven new tables and one view. Note the glob has to cover three prefixes — `\dt extraction*`
alone silently misses `content_extractions` and `calibration_sample_members`:

```bash
docker exec postgres psql -U noktah -d noktah_dashboard -c \
  "\dt extraction*|content_extractions|calibration_sample_members"
docker exec postgres psql -U noktah -d noktah_dashboard -c "\dv extraction_status"
```

Confirm the one widened constraint took, and that it is a superset rather than a replacement —
all three kinds must be accepted:

```sql
SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname LIKE '%runs_kind%';
-- must list 'collection', 'generation', AND 'extraction'
```

---

## 2. Sync the vocabulary and confirm it is closed

```bash
docker exec prefect python flows/extraction_vocabulary_sync.py --validate-only
docker exec prefect python flows/extraction_vocabulary_sync.py
```

```sql
SELECT term, is_residual FROM extraction_vocabulary_terms
WHERE version = 'v1' AND dimension = 'beat_function' ORDER BY ordinal;
```

Expect exactly five: `hook`, `setup`, `main_point`, `call_to_action`, and `unclassified`
(`is_residual = true`). Deliberately minimal — the residual share is the measurement that tells us
what v2 should contain (R11).

**The closed vocabulary should be unbypassable.** This must fail:

```sql
INSERT INTO extraction_beats (extraction_id, vocabulary_version, position, function, description)
VALUES ('<any existing id>', 'v1', 1, 'demonstration', 'x');
-- ERROR: insert or update violates foreign key constraint "fk_beat_function"
```

---

## 3. Prove the validator rejects what it must — no model calls

```bash
cd service/roach && python -m pytest tests/test_analyze_validation.py -v
```

Fixture-driven, no network. Each case asserts *invalid*, and that nothing reaches storage:

| Fixture | Expected |
|---|---|
| `finish_reason: "length"` with well-formed JSON | `truncated` — rejected **before** parsing |
| JSON wrapped in prose, no fences | `schema_invalid` — the regex fallback is gone |
| `beats: []` | `schema_invalid` (FR-003) |
| positions `[1, 3]` | `schema_invalid` — contiguity |
| positions `[1, 1]` | `schema_invalid` — uniqueness |
| `function: "demonstration"` | `out_of_vocabulary` (FR-006) — **not** coerced to `unclassified` |
| `flow` returned as a JSON array | `schema_invalid` — `_to_text` coercion is gone |
| two values for one dimension | `schema_invalid` (FR-012) |
| `subtitle: null`, `subtitle_absence: null` | `schema_invalid` (FR-010) |

The truncation case is the important one: the fragment is *valid JSON satisfying the schema*. Only
the `finish_reason` check catches it, which is why it runs first.

Then confirm the salvage paths are **deleted**, not merely unused:

```bash
grep -n 're\.search(r"\\{\.\*\\}"' service/roach/analyze.py   # expect no match
grep -n 'def _to_text' service/roach/analyze.py               # expect no match
```

---

## 4. Prove the retry carries the error

```bash
cd service/roach && python -m pytest tests/test_analyze_validation.py -k retry -v
```

Asserts, against a stubbed transport: exactly **two** model calls; the second request contains the
first response verbatim plus the validator's own error text; the media part is **not** re-sent; a
second failure produces a quarantine and no `content_extractions` row.

---

## 5. Storage invariants against a real database

```bash
cd service/prefect && ./.venv/Scripts/python.exe -m pytest tests/ -m schema -v
```

Covers the invariants that fail *silently* and so matter most (data-model invariants 6, 7, 9):

- re-running backfill at the same version inserts nothing and charges nothing (FR-036)
- backfill writes **zero** rows to `harvested_signals` — subtitles byte-identical before and after
  (FR-009, R10)
- a frozen vocabulary version cannot be mutated by a re-sync (FR-030)
- `purpose = 'calibration'` rows are absent from every analytical figure (SC-018)
- `cost_usd` is never written from an estimate

---

## 6. Dry-run the backfill — still zero model calls

```bash
docker exec prefect python flows/roach_extract_backfill.py --dry-run
```

On a fresh install this **must** report `PROJECTED SPEND: UNCALIBRATED`, naming the content types
with no measured baseline. That is the correct output, not a failure — no cost baseline exists
anywhere in this system today (R3, measured: zero usage records in roach's logs, zero in Prefect's
`log` table, no spend table). A confident dollar figure here would be a fabrication.

Confirm it made no calls:

```sql
SELECT count(*) FROM content_extractions;   -- still 0
SELECT count(*) FROM extraction_quarantine; -- still 0
```

---

## 7. First real spend: the pilot

```bash
docker exec prefect python flows/roach_extract_backfill.py --pilot 12
```

Twelve items, mixed content types. This *is* the baseline. Then:

```bash
docker exec prefect python flows/roach_extract_backfill.py --dry-run
```

Now reports a projection with its basis and per-class sample sizes. Verify cost was measured, not
guessed:

```sql
SELECT content_type, count(*), round(avg(cost_usd)::numeric, 6) AS avg_cost,
       sum(prompt_tokens) AS prompt_tok, sum(completion_tokens) AS completion_tok
FROM content_extractions GROUP BY 1;
```

Every row must have non-null `cost_usd` — if any is null, roach is still discarding the usage object
and FR-038 is unmet.

---

## 8. The question the feature exists to answer

```sql
-- How many items open with a hook, and how do they perform?
SELECT b.function, count(*) AS items,
       round(avg(s.likes + coalesce(s.comments, 0))) AS avg_engagement
FROM content_extractions e
JOIN extraction_beats b ON b.extraction_id = e.id AND b.position = 1
JOIN harvested_signals s ON s.platform = e.platform AND s.content_id = e.content_id
WHERE e.purpose = 'production' AND s.advertisement = false
GROUP BY 1 ORDER BY 2 DESC;
```

No `LIKE`, no trigram, no text parsing (SC-001). Before this feature the same question required
reading 819 rows of prose by hand.

Vocabulary-gap trend (FR-005, SC-011):

```sql
SELECT date_trunc('month', e.extracted_at) AS month,
       round(100.0 * count(*) FILTER (WHERE b.function = 'unclassified') / count(*), 1) AS pct_unclassified,
       count(*) AS beats
FROM content_extractions e JOIN extraction_beats b ON b.extraction_id = e.id
WHERE e.purpose = 'production' GROUP BY 1 ORDER BY 1;
```

A rising percentage means v1's five terms are too few — which is the signal R11 chose a minimal
vocabulary in order to receive.

---

## 9. Every item has exactly one status

```sql
SELECT reason, count(*) FROM extraction_status GROUP BY 1 ORDER BY 2 DESC;
```

`UNRESOLVED` must return **zero rows** (SC-010). A non-zero count means an item fell through every
branch — the silent partial success Constitution V calls the most dangerous failure mode here.

Cross-check the universe is the union, not just the extraction table:

```sql
SELECT (SELECT count(*) FROM extraction_status) AS in_view,
       (SELECT count(DISTINCT (platform, content_id)) FROM harvested_signals) AS in_corpus;
```

These must match. Feature 006 found 2 of 819 rows sitting in exactly this gap, and only against real
data — not from any test written beforehand.

---

## 10. Calibration — the second real spend

Settle R8's open question with **one** call before choosing a sample size:

```bash
key=$(grep ROACH_API_KEY service/roach/.env | cut -d= -f2-)
# one image item, sent to the video model — does mimo accept image input today?
```

Then:

```bash
docker exec prefect python flows/roach_extract_calibrate.py --dry-run
docker exec prefect python flows/roach_extract_calibrate.py --sample image_overlap_v1 --confirm
docker exec prefect python flows/roach_extract_calibrate.py --report
```

The report must state the covered classes **and** that video (364 items) is not covered (FR-027a),
enumerate disagreements with direction rather than a single blended score, and report insufficient
data rather than a weak figure below 30 usable pairs.

If `OPENROUTER_IMAGE_MODEL` is unset, expect `No cross-model comparison applies` and zero model
calls — verify by temporarily unsetting it (FR-027d).

---

## 11. Collection is unaffected

```bash
docker exec prefect prefect deployment run 'social-harvest-recent/social-harvest-recent' \
  --param profiles='["https://www.instagram.com/lasikasyik/"]' --param n=3
```

Every delivered item must still reach Drive and the sheet, including any whose extraction
quarantined (FR-022, SC-015). Confirm the sheet's `content_flow` column is populated with a readable
rendering of the beats (FR-041) — the reviewer workflow must not have degraded.

Probe sparingly: each of these is a real request from your egress IP
(`.claude/rules/backend/roach.md`).

---

## Rollback

```bash
docker exec -i postgres psql -U noktah -d noktah_dashboard \
  < config/postgres/migrations/009_structured_extraction.down.sql
```

A real reversal — every table 009 creates is new, so dropping them destroys nothing that predates
the migration. Extraction data is lost; `harvested_signals` is untouched, because backfill never
wrote to it.
