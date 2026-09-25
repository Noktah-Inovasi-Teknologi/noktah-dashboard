# Contract — Backfill and Costing

**Feature**: `007-structured-extraction` | **Date**: 2026-08-03

Re-extraction of the existing corpus as a costed batch operation. Implements FR-031 – FR-039 and
the Constitution's "costed batch operations MUST support a dry-run mode that reports projected
model spend".

---

## Modes

```bash
# 1. What would this cost? Makes ZERO model calls.
docker exec prefect python flows/roach_extract_backfill.py --dry-run

# 2. Establish a measured baseline on a small sample. Real calls, recorded cost.
docker exec prefect python flows/roach_extract_backfill.py --pilot 12

# 3. The real thing. Refuses to start above the threshold without --confirm.
docker exec prefect python flows/roach_extract_backfill.py --confirm

# Scope selectors, combinable with any mode
  --platform instagram --content-type video --profile lasikasyik
  --include-failed          # the 18 items whose original analysis failed
  --schema-version v2       # target version; default = current
```

---

## The first dry-run is uncalibrated, and says so

There is **no cost baseline anywhere** — Phase 0 measured zero usage records in roach's logs, zero
in Prefect's `log` table, and no spend table (R3). So the first projection has nothing to rest on.

Constitution VI forbids an estimate and an observation sharing a field. The dry-run therefore
reports differently depending on whether a baseline exists, and never blurs the two:

**No baseline yet:**
```
Backfill projection — schema v1, vocabulary v1
  In scope: 819 items  (video 364 · carousel 323 · image 130 · story 2)
  Already extracted at this version: 0
  Media unreachable: not checked in dry-run

  PROJECTED SPEND: UNCALIBRATED
    No measured per-item usage exists for any content type at this schema version.
    Run `--pilot 12` to establish one. This is not an estimate; it is an absence.
```

**Baseline present:**
```
  PROJECTED SPEND: $2.41
    Basis: measured usage from 12 pilot extractions (video 4, carousel 4, image 4),
           2026-08-03, schema v1, models xiaomi/mimo-v2.5 + google/gemini-2.5-flash-lite.
    Per item: video $0.0043 (n=4) · carousel $0.0021 (n=4) · image $0.0018 (n=4)
    Not covered: story (n=0) — 2 items projected at the image rate, flagged.
```

Three things that must not be dropped from the second form: **the basis**, **the per-class sample
size**, and **the classes with no coverage at all**. A projection whose sample is `n=4` and one
whose sample is `n=400` are not the same claim, and FR-033 requires the difference be visible.

The baseline improves itself: FR-038 records measured cost on every extraction, so after one real
backfill the projection rests on hundreds of items rather than a pilot's dozen.

---

## Media comes from Drive, never from the platform

All 855 items carry a `drive_file_id` and existing OAuth scopes (`drive.readonly` + `drive.file`)
cover reading them — but **no download task exists**; `google_tasks.py` has upload and delete only
(R2). `google.drive.download-file` is new work.

```
for each item in scope:
    drive_file_id present?          no  → media_unavailable, counted, next
    fetch from Drive                fail → media_unavailable, counted, next
    hash media → already extracted at this version?  yes → cached, no call, next
    POST /analyze                   → validated → store | quarantined → store quarantine
    delete local temp file
```

A `drive_file_id` in Postgres does not prove the Drive object still exists — deletion, trashing, or
a permissions change surfaces only at fetch time. That is why FR-035 requires a classified skip:
the failure is unpreventable, so it must at least be counted. **Re-downloading from the platform is
not a fallback.** It is collection expansion and a Principle X violation, and no code path may
offer it.

---

## Idempotence is a unique constraint, not a check (FR-036)

```
uq_extraction_version UNIQUE (platform, content_id, purpose, model_requested,
                             prompt_version, schema_version, vocabulary_version)
```

Re-running at the same version conflicts and skips before any model call. This is enforcement, not
convention: a bug in the flow's skip logic cannot cause a double charge, because the row cannot be
written twice.

The content hash (FR-039) is the *second* gate, catching the case where the same media was
re-uploaded under a new `content_id`.

---

## Backfill writes a strict subset

**Backfill writes only `content_extractions`, `extraction_beats`, `extraction_attributes`, and
`extraction_quarantine`. It never touches `harvested_signals`.**

Re-extraction produces a new subtitle. Writing it back to `harvested_signals.subtitle` would
overwrite the stored transcript — violating FR-009 and destroying data that cannot be regenerated
without paying for it again.

`.claude/rules/backend/songbird.md` documents this exact bug class for metric refresh: routing
through `social.signal.record` instead of `social.signal.record-metrics` blanks stored analysis "on
every refreshed row, silently, and this feature cannot regenerate it". The generalisation is the
rule: **a re-processing path writes a strict subset of what the original path wrote.** The forward
harvest path writes both; backfill writes only the new tables.

This gets a test, not a comment (data-model invariant 7).

---

## Threshold confirmation (FR-037)

A projection above `EXTRACTION_SPEND_THRESHOLD_USD` (default $5.00) refuses to run without
`--confirm`:

```
PROJECTED SPEND $12.80 exceeds threshold $5.00.
Re-run with --confirm to proceed. No model calls were made.
```

The threshold is env configuration, not a flag — a per-run override would make it a formality. It is
deliberately low relative to a plausible monthly budget, because its job is to catch a scope selector
that matched far more than intended, not to be a budget.

## The monthly ceiling is separate, and is a hard stop (FR-037a)

The per-run threshold above catches one oversized run. It does nothing about many small runs
accumulating, which is what Constitution XI's "a configured monthly ceiling MUST be **enforced**,
not merely reported" is about.

Before any costed run — backfill *or* calibration — the month-to-date measured spend is summed and
the run's projection added:

```sql
SELECT coalesce(sum(cost_usd), 0) FROM (
  SELECT cost_usd FROM content_extractions  WHERE extracted_at    >= date_trunc('month', now())
  UNION ALL
  SELECT cost_usd FROM extraction_quarantine WHERE quarantined_at >= date_trunc('month', now())
) t;
```

Quarantine rows are in the sum deliberately: those tokens were spent. Omitting them would let a
pathologically-failing item consume budget invisibly.

If month-to-date + projection exceeds `EXTRACTION_MONTHLY_CEILING_USD`, the run **halts**. Not a
warning, not a `--confirm` prompt — enforcement is the requirement.

```
HALTED: month-to-date extraction spend $18.40 + projection $6.20 = $24.60
        exceeds EXTRACTION_MONTHLY_CEILING_USD ($20.00).
        No model calls were made.
        NOTE: this ceiling covers EXTRACTION spend only. Songbird generation
        spend is not counted here and is not bounded by it.
```

**That last line is required, not decoration** (FR-037b). Presenting this as a system budget while
generation spend sits outside the count would report "within budget" on incomplete evidence — the
confident-and-wrong number Constitution VI exists to prevent. A cross-service ceiling needs a shared
spend ledger and is a separate feature; this one gates the spend it introduces.

---

## The second backfill is planned (FR-043b)

Shipping ahead of S-05 means the corpus is re-extracted twice. That is a known, accepted cost:

| Pass | When | Scope |
|---|---|---|
| 1 | this feature | 819 items at schema v1, vocabulary v1 — flow only, `attributes` empty |
| 2 | when S-05 lands | the corpus at that time, at v2 — flow **and** attributes |

Pass 2 uses the same machinery on the same terms, and its cost is projectable by the same dry-run
before anyone commits. FR-043b requires that projection be statable when *this* feature ships — once
pass 1's measured baseline exists, pass 2's bill is a multiplication, not a guess. That is what makes
"ship ahead" a decision against a known second bill rather than an open-ended one.

---

## What the run reports

Every item resolves to exactly one outcome (SC-010):

```
Backfill complete — schema v1, vocabulary v1
  extracted   784    quarantined  21    cached  0    media_unavailable  14
  ---------------------------------------------------------------------
  total       819    (unclassified: 0)   ← anything but 0 here is a bug

  Actual spend: $2.18   Projected: $2.41   Delta: -9.5%  (within ±25%, SC-008)
  Quarantine by kind: truncated 12 · schema_invalid 6 · unsupported_media 3
```

`unclassified: 0` is the invariant, mirroring feature 006's `velocity_status` and feature 005's
why-empty query. A non-zero value there means an item fell through every branch — which is exactly
the silent partial success Constitution V calls "the most dangerous failure mode in this system".
