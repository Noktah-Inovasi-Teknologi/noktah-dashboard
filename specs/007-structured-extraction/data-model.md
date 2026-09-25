# Phase 1 — Data Model: Structured Extraction Output

**Feature**: `007-structured-extraction` | **Migration**: `009_structured_extraction` | **Date**: 2026-08-03

Seven tables and one view. Additive — nothing added to `harvested_signals` (R9), no `DROP`, no
constraint tightened. Migration 008 is the latest applied, so 009 is next.

**One exception to "no `ALTER`", and it is the permitted shape.** `runs.kind` is widened from
`('collection', 'generation')` to include `'extraction'`, by DROP + ADD with a strict superset.
Migration 008 did exactly this to `chk_capture_outcomes_kind` when it added `metric_refresh`, and
`.claude/rules/backend/schema.md` names widening as the one constraint replacement it allows —
because a superset cannot reject an existing row. Verify the re-added list still contains both
original kinds.

---

## Why these are new tables and not columns

`harvested_signals` is `UNIQUE (platform, content_id)` — exactly one row per item. This feature needs
*several* extractions per item: one per schema version (FR-029), one more when S-05 lands and the
second backfill runs (FR-043b), and two at once for every calibration pair (FR-027). A
one-row-per-item table has no slot for any of that.

**Keyed by `(platform, content_id)`, not by `harvested_signals.id`** — the same choice features 005
and 006 made, for the same reason: the failure-retry purge can delete a signal row
([social_harvest.py:848](../../service/prefect/flows/common/social_harvest.py#L848)), and extraction
history must outlive it.

---

## `content_extractions`

One row per **validated** extraction. Nothing that failed validation ever appears here — that is
`extraction_quarantine`.

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | UUID | no | PK, `gen_random_uuid()` |
| `platform` | TEXT | no | CHECK ∈ `instagram`, `tiktok` |
| `content_id` | TEXT | no | with `platform`, identifies the item |
| `account_id` | UUID | yes | FK → `accounts(id)`; identity chain (Principle IX) |
| `run_id` | UUID | yes | FK → `runs(id)`; which run produced it |
| `purpose` | TEXT | no | CHECK ∈ `production`, `calibration`. **Load-bearing** — see below |
| `content_type` | TEXT | no | as harvested |
| `media_path` | TEXT | no | CHECK ∈ `video`, `image`. The path **actually taken**, not the label (FR-024) |
| `subtitle` | TEXT | yes | verbatim, exactly as returned (FR-008) |
| `subtitle_absence` | TEXT | yes | CHECK ∈ `not_applicable_no_audio`, `attempted_none_found`; NULL when `subtitle` is present (FR-010) |
| `summary` | TEXT | no | non-empty |
| `model_requested` | TEXT | no | what we asked for |
| `model_served` | TEXT | no | what OpenRouter actually served (FR-024) |
| `provider` | TEXT | yes | serving provider where reported |
| `prompt_version` | TEXT | no | FR-028 |
| `schema_version` | TEXT | no | FR-028 |
| `vocabulary_version` | TEXT | no | FR-028; FK-checked against `extraction_vocabulary_terms` |
| `content_hash` | TEXT | no | hash of the media actually sent — the cache key (FR-039) |
| `prompt_tokens` | INTEGER | yes | measured, from the provider (FR-038) |
| `completion_tokens` | INTEGER | yes | measured |
| `cost_usd` | NUMERIC(12,6) | yes | measured; **never** estimated into this column (Constitution VI) |
| `attempts` | SMALLINT | no | 1 or 2 — 2 means the retry succeeded |
| `extracted_at` | TIMESTAMPTZ | no | `now()` |

**Constraints**

```
uq_extraction_version  UNIQUE (platform, content_id, purpose, model_requested,
                               prompt_version, schema_version, vocabulary_version)
uq_extraction_id_vocab UNIQUE (id, vocabulary_version)   -- target of fk_beat_extraction_vocab
chk_extraction_subtitle_absence  CHECK (subtitle IS NOT NULL OR subtitle_absence IS NOT NULL)
chk_extraction_summary_nonempty  CHECK (btrim(summary) <> '')
chk_extraction_attempts          CHECK (attempts BETWEEN 1 AND 2)
```

`uq_extraction_id_vocab` is redundant as a uniqueness statement — `id` is already the PK — and
exists solely because Postgres requires a unique constraint covering exactly the columns a foreign
key references. It is what lets `extraction_beats` be held to its parent's vocabulary version
declaratively rather than by trigger. Mirror it into `init.sql` by explicit `ADD CONSTRAINT` with
this name, not as an inline modifier, or parity fails.

`uq_extraction_version` **is** FR-036's idempotence: re-running a backfill at the same version
conflicts and is skipped rather than re-charged. `model_requested` is in the key because a
calibration extracts the same item at the same version under a different model.

`chk_extraction_subtitle_absence` is FR-010's teeth. An empty string alone can no longer stand for
three different facts — either there is a transcript, or there is a recorded reason there is not.
The third case ("extraction did not complete") is not representable here at all, because such a run
produces a quarantine row instead, never a `content_extractions` row.

> **`purpose` is what keeps SC-018 true.** Every calibration item is extracted twice; if both copies
> sat here as `production`, every distribution over the calibration sample would be double-weighted.
> Every analytical query filters `purpose = 'production'`, and the `extraction_status` view treats
> `calibration` as its own terminal state rather than as evidence about the item.

---

## `extraction_beats`

The ordered flow. One row per beat (FR-001).

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | UUID | no | PK |
| `extraction_id` | UUID | no | FK → `content_extractions(id)` ON DELETE CASCADE |
| `vocabulary_version` | TEXT | no | denormalised from the parent **purely so `fk_beat_function` is expressible**; held equal to the parent's by `fk_beat_extraction_vocab` |
| `dimension` | TEXT | no | `DEFAULT 'beat_function'`, `CHECK (dimension = 'beat_function')` — constant by construction, present only so the FK can reach the vocabulary PK |
| `position` | SMALLINT | no | 1-based, contiguous, unique within the extraction (FR-002) |
| `function` | TEXT | no | member of the extraction's `vocabulary_version` (FR-006) |
| `description` | TEXT | no | non-empty |

**Constraints**

```
uq_beat_position          UNIQUE (extraction_id, position)
chk_beat_position         CHECK (position >= 1)
chk_beat_description      CHECK (btrim(description) <> '')
chk_beat_dimension        CHECK (dimension = 'beat_function')
fk_beat_function          FOREIGN KEY (vocabulary_version, dimension, function)
                          REFERENCES extraction_vocabulary_terms (version, dimension, term)
fk_beat_extraction_vocab  FOREIGN KEY (extraction_id, vocabulary_version)
                          REFERENCES content_extractions (id, vocabulary_version)
                          ON DELETE CASCADE
```

The composite FK on `(vocabulary_version, dimension, function)` is what makes SC-002 structurally
true rather than a query someone remembers to run: a beat function outside its recorded vocabulary
version **cannot be inserted**. `vocabulary_version` is carried on the beat row (denormalised from
its extraction) purely so this FK is expressible.

**Two details that are easy to get wrong, and both cost a migration to fix later:**

- **The FK targets the vocabulary's full PK `(version, dimension, term)`, not `(version, term)`.**
  Targeting the shorter key would require a `UNIQUE (version, term)` index on
  `extraction_vocabulary_terms`, which forbids two dimensions sharing a term string inside one
  version. FR-043a requires S-05's attribute dimensions populate *this* mechanism without
  introducing a second one — and a dimension publishing a value like `hook` or `setup` is entirely
  plausible. The constant `dimension` column on the beat costs one byte per row and keeps that door
  open. `fk_attribute_term` already targets the same PK, so both FKs are consistent.
- **Equality with the parent's version needs an FK, not a `CHECK`.** A row-level `CHECK` cannot
  reference another table, so `fk_beat_extraction_vocab` does the work — which in turn requires a
  redundant `uq_extraction_id_vocab UNIQUE (id, vocabulary_version)` on `content_extractions`
  (redundant because `id` is already the PK, but Postgres requires a unique constraint covering
  exactly the referenced columns). The alternative is a trigger, which is neither declarative nor
  visible to the parity check.

Contiguity (no gaps) is not expressible as a row constraint. It is enforced at validation time in
roach and re-checked at the storage boundary, and asserted over the whole table by a test:

```sql
-- must return zero rows
SELECT extraction_id FROM extraction_beats
GROUP BY extraction_id
HAVING count(*) <> max(position) OR min(position) <> 1;
```

FR-003's "at least one beat" is likewise a cross-row invariant, checked at write time and asserted
in test as a zero-row query against `content_extractions` with no beats.

---

## `extraction_attributes`

At most one value per dimension per extraction (FR-012), each with its own confidence (FR-013).
**Empty until S-05 publishes dimensions** — which is FR-043's inert-but-correct state, not a defect.

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | UUID | no | PK |
| `extraction_id` | UUID | no | FK → `content_extractions(id)` ON DELETE CASCADE |
| `dimension` | TEXT | no | vocabulary dimension |
| `value` | TEXT | no | member of that dimension at `vocabulary_version` |
| `confidence` | TEXT | no | CHECK ∈ `high`, `medium`, `low` — ordered, not continuous |
| `vocabulary_version` | TEXT | no | FR-015 |

**Constraints**

```
uq_attribute_dimension  UNIQUE (extraction_id, dimension)     -- FR-012, at most one value
fk_attribute_term       FOREIGN KEY (vocabulary_version, dimension, value)
                        REFERENCES extraction_vocabulary_terms (version, dimension, term)
```

`uq_attribute_dimension` is FR-012 enforced at the database boundary, not merely in the validator.

**On the confidence scale.** Three ordered levels, stored as text, not a float. A model's
self-reported `0.83` implies a calibration it does not have; Constitution VI forbids presenting
false precision, and Constitution VIII requires filtering by a *minimum*, which an ordered scale
supports exactly as well. Filtering is by rank, so the vocabulary is closed at the database boundary
and a fourth level cannot appear.

---

## `extraction_quarantine`

**Append-only.** Model output that failed validation twice (FR-019). Never joined into an analytical
query — it exists to be counted (FR-020) and read by a human.

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | UUID | no | PK |
| `platform` | TEXT | no | |
| `content_id` | TEXT | no | |
| `run_id` | UUID | yes | FK → `runs(id)` — which run produced this outcome. Without it a quarantine row cannot be attributed to the batch that caused it, and SC-010 becomes unanswerable after the run ends |
| `purpose` | TEXT | no | CHECK ∈ `production`, `calibration` |
| `failure_kind` | TEXT | no | CHECK ∈ the closed list below (FR-021) |
| `raw_output` | TEXT | yes | what the model actually returned; NULL only when nothing arrived |
| `validation_errors` | JSONB | no | both attempts' errors, ordered |
| `model_requested` | TEXT | no | |
| `model_served` | TEXT | yes | NULL when the call never reached a model |
| `prompt_version` | TEXT | no | |
| `schema_version` | TEXT | no | |
| `vocabulary_version` | TEXT | no | |
| `prompt_tokens` / `completion_tokens` / `cost_usd` | | yes | **spend still happened** — recorded |
| `quarantined_at` | TIMESTAMPTZ | no | `now()` |

**`failure_kind` vocabulary** (closed, enforced by CHECK):

| Value | Means |
|---|---|
| `schema_invalid` | parsed, but did not satisfy the schema |
| `truncated` | `finish_reason = "length"` — invalid regardless of whether the fragment parses (FR-017) |
| `empty_content` | model returned no content |
| `out_of_vocabulary` | a beat function or attribute value outside the recorded version (FR-006) |
| `provider_error` | non-429 HTTP failure from the provider |
| `rate_limited` | 429 after back-off exhausted |
| `media_unavailable` | the retained media could not be fetched (FR-035) |
| `unsupported_media` | the model rejected this media class — R8's expected calibration outcome |
| `spend_ceiling_reached` | the monthly extraction ceiling was reached, so no call was made (FR-037a) |

There is **no `other`**. A tenth failure mode must be added deliberately, by migration, because this
vocabulary is what FR-020's counts are grouped by and an escape hatch would quietly absorb exactly
the novel failures worth noticing.

> **Three of these ten record an outcome where no model call happened at all** —
> `media_unavailable`, `unsupported_media` where the provider refused before generating, and
> `spend_ceiling_reached`. `raw_output` and `model_served` are nullable precisely for this: the table
> is the record of *every attempt's outcome*, not only of bad model output. A skipped extraction that
> left no row anywhere would be the silent drop Constitution V forbids, and would make SC-010's
> "exactly one outcome per item" unverifiable.

> **Cost is recorded on quarantine rows too.** A failed extraction that burned tokens still cost
> money. Omitting it would make the measured baseline (R3) systematically low, and would let a
> pathological item that quarantines on every attempt look free.

---

## `extraction_vocabulary_terms`

The versioned closed vocabulary (FR-004, FR-043a). Synced from `config/extraction/vocabulary_v1.yaml`
by `extraction-vocabulary-sync`, mirroring how `field_availability` is synced from
`config/field_availability.yaml`.

| Column | Type | Null | Notes |
|---|---|---|---|
| `version` | TEXT | no | PK part; e.g. `v1` |
| `dimension` | TEXT | no | PK part; `beat_function` for this feature's terms |
| `term` | TEXT | no | PK part |
| `is_residual` | BOOLEAN | no | default `false`; exactly one per dimension per version |
| `ordinal` | SMALLINT | yes | display order |
| `description` | TEXT | no | what the term means — this is what the prompt is built from |
| `frozen_at` | TIMESTAMPTZ | yes | set once any extraction records this version |

**Constraints**

```
pk_vocabulary_term        PRIMARY KEY (version, dimension, term)
uq_vocabulary_residual    UNIQUE (version, dimension) WHERE is_residual
```

**There is deliberately no `UNIQUE (version, term)`.** An earlier draft carried one to support the
beats FK, but it forbids two dimensions sharing a term string inside a version — which would make
FR-043a false the moment S-05 publishes a dimension containing a value like `hook`. The beats FK
targets the full PK instead (see `extraction_beats`), so terms need only be unique per
`(version, dimension)`, which the PK already gives.

`uq_vocabulary_residual` bounds the residual count from **above** — at most one per dimension per
version. Nothing in the schema requires one to *exist*, and FR-003's "no discernible structure is
recorded as a single beat carrying the residual function" is unsatisfiable without it. That
existence check lives in the sync task, which fails loudly on a dimension with no residual.

**v1 membership** (`dimension = 'beat_function'`), deliberately minimal per R11:

| term | residual | description |
|---|---|---|
| `hook` | | opens the content and creates the reason to keep watching |
| `setup` | | establishes context, problem, or premise |
| `main_point` | | the substantive content — the claim, demonstration, or payload |
| `call_to_action` | | asks the viewer to do something |
| `unclassified` | ✔ | structure present but not describable by the above |

Four functions plus a residual — the concepts FR-004 requires and nothing else. The `unclassified`
share is the measurement (FR-005) that tells us what v2 should contain; pre-loading eight plausible
terms would pre-empt the very finding that mechanism exists to produce.

**`frozen_at` is the immutability latch.** Once any `content_extractions` row records a version, the
sync flow may add a *new* version but must not alter that one — otherwise FR-030 ("interpretable
against the version it recorded") silently becomes false for every row already stored. The sync task
fails loudly rather than mutating a frozen version.

---

## `calibration_sample_members`

The fixed, re-runnable sample (FR-027b). Membership is data so the same items are re-measured across
model and version changes.

| Column | Type | Null | Notes |
|---|---|---|---|
| `sample_key` | TEXT | no | PK part; names the sample, e.g. `image_overlap_v1` |
| `platform` | TEXT | no | PK part |
| `content_id` | TEXT | no | PK part |
| `added_at` | TIMESTAMPTZ | no | `now()` |
| `media_class` | TEXT | no | `image`, `carousel`, `story` — what R8 measured as the overlap |

Agreement itself is **not stored**. It is computed from the `purpose = 'calibration'` extractions on
demand, so it can never disagree with the extractions it summarises, and re-deriving after a
vocabulary change needs no migration. FR-027b's "carries the versions it was measured under" is
satisfied because those versions are columns on the extractions being compared.

---

## `extraction_batch_runs`

The spec's **Backfill batch** entity — "a selected set of items to re-extract, its dry-run
projection, its confirmation state, and its per-item outcomes including classified skips."

**It is a detail table on `runs`, not a run table of its own.** `runs` (feature 004) already holds
`flow_name`, `flow_run_name`, `started_at`, `ended_at`, `status`, and a `summary` JSONB; duplicating
those would be the second-mechanism mistake this feature argues against elsewhere. `runs.kind` is
widened to accept `'extraction'` and this table carries only what is specific to a **costed** batch.

Serves backfill **and** calibration, because FR-027e puts calibration on backfill's terms — same
dry-run projection, same threshold confirmation, same measured cost.

| Column | Type | Null | Notes |
|---|---|---|---|
| `run_id` | UUID | no | PK, FK → `runs(id)` |
| `batch_kind` | TEXT | no | CHECK ∈ `backfill`, `calibration` |
| `mode` | TEXT | no | CHECK ∈ `dry_run`, `pilot`, `real` |
| `scope_description` | TEXT | no | **how** the set was selected — never a literal count (R1: the corpus was 656 at audit and 819 today) |
| `items_in_scope` | INTEGER | no | resolved at run start |
| `projection_basis` | TEXT | no | CHECK ∈ `measured`, `uncalibrated` (FR-033) |
| `projection_usd` | NUMERIC(12,6) | yes | NULL when uncalibrated — **the honest first output** |
| `projection_sample_size` | INTEGER | yes | the sample the projection rests on |
| `ceiling_usd` | NUMERIC(12,6) | yes | the ceiling in force (FR-037a) |
| `month_to_date_usd` | NUMERIC(12,6) | yes | measured extraction spend before this run began |
| `confirmed` | BOOLEAN | no | whether `--confirm` was given (FR-037) |
| `actual_usd` | NUMERIC(12,6) | yes | measured at finish; compared against `projection_usd` (SC-008) |
| `items_extracted` / `items_cached` / `items_quarantined` / `items_skipped` / `items_unclassified` | INTEGER | no | default 0; filled at finish (SC-010) |

**Constraints**

```
pk_batch_run            PRIMARY KEY (run_id)
chk_batch_kind          CHECK (batch_kind IN ('backfill', 'calibration'))
chk_batch_mode          CHECK (mode IN ('dry_run', 'pilot', 'real'))
chk_batch_projection    CHECK (projection_basis <> 'measured'
                               OR (projection_usd IS NOT NULL
                                   AND projection_sample_size IS NOT NULL))
```

`chk_batch_projection` forbids the one thing Constitution VI actually forbids: a projection claiming
a measured basis while carrying no measurement. Note that **both terms are `IS NOT NULL`
predicates**, which never evaluate to NULL — the trap that let a NULL `reason` slip past
`chk_capture_outcomes_kind` in feature 005 does not apply here. That was worth checking rather than
assuming.

**Three things deliberately have no constraint:**

- **`items_unclassified = 0` is not a CHECK.** SC-010 wants unclassified outcomes *counted*, and a
  constraint would make the defect unrecordable — the run would fail to write its own summary rather
  than reporting the problem. The zero assertion belongs in a test and in the run summary, not in
  the schema.
- **The outcome counts summing to `items_in_scope` is not a CHECK.** It is only true once the run
  finishes, and the finish signal lives on `runs.ended_at` in another table. Asserted by query,
  the same way beat contiguity is:

```sql
-- must return zero rows: every finished run accounts for every item in scope
SELECT b.run_id FROM extraction_batch_runs b JOIN runs r ON r.id = b.run_id
WHERE r.ended_at IS NOT NULL AND b.mode <> 'dry_run'
  AND b.items_extracted + b.items_cached + b.items_quarantined
    + b.items_skipped + b.items_unclassified <> b.items_in_scope;
```

- **`actual_usd` within ±25% of `projection_usd` is not a CHECK.** SC-008 permits divergence *with
  its cause reported*; a constraint would reject the run rather than the finding.

---

## `extraction_status` (view)

"Why does this item have no production extraction at the current version?" — exactly one reason per
item. The third occurrence of this shape (feature 005's why-empty query, feature 006's
`velocity_status`), and it exists for the same reason: a stored status column needs maintaining on
every write and can silently disagree with the rows it describes.

Item universe is the **UNION of `harvested_signals` and `content_extractions`** — not extractions
alone. An item never successfully extracted has zero extraction rows; driven by extractions alone it
would vanish from the view, having neither a result nor a reason, which is what FR-021 and SC-010
forbid. Feature 006 learned this the hard way against real data.

| `reason` | Means |
|---|---|
| `extracted` | a current-version production extraction exists |
| `superseded_version_only` | extracted, but only under an older schema/vocabulary version — backfill candidate |
| `quarantined` | most recent attempt failed validation twice |
| `media_unavailable` | retained media could not be fetched (FR-035) |
| `never_attempted` | in the corpus, no extraction and no quarantine row |
| `calibration_only` | extractions exist but all are `purpose = 'calibration'` — not corpus evidence |
| `ceiling_deferred` | skipped because the monthly extraction ceiling was reached (FR-037a) — a **deferral, not a failure**: the item is a backfill candidate next month, and collection was unaffected |

Ordering matters, as it did in feature 006: **`never_attempted` must be tested before the
version-comparison branches**, or an item with no rows falls through into `superseded_version_only`.
An `UNRESOLVED` row is a bug — SC-010's "count of unclassified outcomes is zero" is this query
returning nothing for that label.

---

## Invariants worth a regression test

| # | Invariant | Enforced by | Why it is fragile |
|---|---|---|---|
| 1 | Beat positions are contiguous from 1 | validator + test query | Not expressible as a row constraint; a gap looks like valid data |
| 2 | Every extraction has ≥1 beat | validator + test query | FR-003; an empty array is otherwise a plausible model response |
| 3 | Beat function ∈ its recorded vocabulary version | composite FK | The FK is the whole point of denormalising `vocabulary_version` onto the beat |
| 4 | At most one value per dimension | `uq_attribute_dimension` | FR-012 must not depend on the validator alone |
| 5 | A frozen vocabulary version is never altered | sync task fails loudly | Silent mutation makes FR-030 false retroactively for every stored row |
| 6 | Calibration extractions never enter an analytical figure | `purpose` filter + view branch | SC-018; the failure is invisible — figures just quietly double-weight |
| 7 | Backfill never writes `harvested_signals` | **two layers** — static import guard + before/after snapshot test | R10; the failure destroys transcripts irreversibly |
| 8 | Quarantine is append-only | no update/delete path | FR-019; the evidence is the product |
| 9 | `cost_usd` holds measured values only | task boundary + test | Constitution VI; an estimate here is indistinguishable from a measurement forever after |
| 10 | Every finished batch run accounts for every item in scope | test query (above) | SC-010; a run that quietly drops items still reports success, and the evidence is gone once the flow exits |

Invariants 6, 7, and 9 share a property that makes them the dangerous ones: **each fails silently
and produces confident, wrong output.** They are the ones to write tests for first.

### Invariant 7 gets two layers, because one is too late

A snapshot test ("run backfill, assert `harvested_signals` is byte-identical") only fails once
someone has already written the wrong line and a test happens to exercise that path. The cheaper
guard fails at the moment the line is written:

```python
# tests/test_extraction_store.py
def test_backfill_cannot_reach_the_signal_write_path():
    """extraction_tasks must not import the writer that owns harvested_signals.

    Reusing social.signal.record from backfill is the natural-looking mistake —
    it is the function that already knows how to store an extraction result.
    It also overwrites `subtitle`, and harvested_signals holds exactly one row
    per item, so the overwrite is unrecoverable (R10).
    """
    src = (REPO / "service/prefect/tasks/extraction_tasks.py").read_text(encoding="utf-8")
    assert "social_signal_record" not in src
    assert "from tasks.social_tasks import" not in src
```

Paired with the behavioural test:

```python
async def test_backfill_leaves_every_signal_row_byte_identical(spine_db):
    before = await snapshot(spine_db, "SELECT content_id, subtitle, content_flow, summary "
                                      "FROM harvested_signals ORDER BY content_id")
    await roach_extract_backfill(confirm=True)
    assert await snapshot(spine_db, ...) == before
```

The first says *this cannot be written*; the second says *this did not happen*. Both are cheap, and
the failure mode they guard is irreversible — a re-extraction cannot restore the transcript it
replaced, it can only produce a third different one, at cost.

---

## Parity and reversal

Mirrored into `init.sql` in the same order, per `.claude/rules/backend/schema.md`, with constraint
names matching exactly — a migration's named `ADD CONSTRAINT` and an inline `REFERENCES` in
`init.sql` produce different auto-generated names and fail parity. Verified by
`script/verify_schema_parity.sh`.

`009_structured_extraction.down.sql` is a **real reversal** — every table this migration creates is
new, so dropping them destroys nothing that predates the migration. This is unlike the baseline
migrations 001–003, whose down scripts are deliberate no-ops.
