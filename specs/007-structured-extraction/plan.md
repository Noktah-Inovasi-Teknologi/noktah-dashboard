# Implementation Plan: Structured Extraction Output

**Branch**: `007-structured-extraction` | **Date**: 2026-08-03 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/007-structured-extraction/spec.md`

## Summary

Turn one model response from three opaque strings into a validated, versioned, queryable record —
and stop storing anything that was never checked.

The prompt already asks for the right thing ("hook, setup, main point, call to action"). What is
missing is that nothing enforces it, nothing records which model or prompt produced it, and nothing
survives a failed call. This feature closes those three gaps in one pass: beats become an ordered
array over a closed vocabulary, output is validated client-side and retried once with the error fed
back, and what still fails is quarantined intact rather than collapsing to `""`.

Phase 0 measured the live system, and five findings shaped the design:

- **The corpus is 819 signals / 855 items**, not the audit's 656/691 — the same July-snapshot drift
  feature 006 corrected. Backfill scope is defined as "everything present when it runs" (R1).
- **There is no cost baseline anywhere.** Zero usage records in roach's logs, zero in Prefect's `log`
  table, no spend table. `_call_model` *discards* the usage object before returning it (R3). So the
  first dry-run cannot honestly be calibrated — it reports **uncalibrated** until a `--pilot` run
  measures one, and roach must start returning usage rather than printing it.
- **Backfill has media but no way to fetch it.** All 855 items carry a `drive_file_id`, existing
  OAuth scopes cover reading them, and there is no `drive_file_download` task — only upload and
  delete (R2).
- **The token-budget headroom is real but the evidence is right-censored.** Max stored transcript is
  4,812 chars against an 8,000-token ceiling — but a truncated response would have been silently
  salvaged into looking complete, so that max is a lower bound (R4). Detect truncation first, resize
  on honest data later.
- **The two paths are genuinely two models today** — `xiaomi/mimo-v2.5` vs
  `google/gemini-2.5-flash-lite`, confirmed from the running container — so the controlled
  comparison is meaningful. Its overlap is the 455 image-class items, and the reason the second
  model exists at all suggests that overlap may be lossy (R8).

The build is one migration (seven tables, one view, one widened CHECK), one config pair (schema + vocabulary), a rewrite
of roach's response handling, a Drive download task, and two flows. No new service.

## Technical Context

**Language/Version**: Python 3.14

**Primary Dependencies**: Prefect 3.6.9, asyncpg, pydantic. **No new dependency** — pydantic is
already present in both images (via `prefect` on one side, via `fastapi` on the other; `api.py`
already uses it). Validation is declarative rather than hand-rolled so FR-018 can quote real error
text back to the model.

**Storage**: PostgreSQL 15. Migration `009_structured_extraction` (008 is the latest applied) adds
`content_extractions`, `extraction_beats`, `extraction_attributes`, `extraction_quarantine`,
`extraction_vocabulary_terms`, `calibration_sample_members`, `extraction_batch_runs`, plus the
`extraction_status` view. `harvested_signals` gains **nothing** (R9). The one `ALTER` is a
**widening** of `runs.kind` to accept `'extraction'` — the shape `.claude/rules/backend/schema.md`
permits, and the one migration 008 already used for `chk_capture_outcomes_kind`. A costed batch is
a run, so it reuses feature 004's `runs` table rather than standing up a parallel one.

**Testing**: pytest. Schema, constraint, and vocabulary-enforcement tests run against a real
disposable PostgreSQL database (`pytestmark = pytest.mark.schema`) per
`.claude/rules/backend/schema.md`. Validation logic is unit-tested against **recorded model-response
fixtures** — malformed, truncated, out-of-vocabulary, duplicate-position — never against live
calls, per Constitution's "collection tasks MUST be developable against fixtures".

**Target Platform**: Linux containers via docker-compose. **Roach requires an image rebuild**
(`docker-compose up -d --build roach`) — its source is baked in, not mounted.

**Project Type**: Workflow-orchestration service (Prefect) + stateless FastAPI microservice (roach).
No new service.

**Performance Goals**: Not latency-sensitive — batch. The governing budget is **model spend**.

**Constraints**:
- Model spend is the hard constraint (Constitution XI). Backfill and calibration are both costed
  batch operations with dry-run projection and threshold confirmation.
- Backfill writes **only** the new extraction tables — never `harvested_signals` (R10, FR-009).
- Schema changes additive; `init.sql` parity enforced by `script/verify_schema_parity.sh`.
- No platform requests at all: backfill sources media from Drive (FR-034, Constitution X).
- Roach stays stateless — it validates and returns; it never stores.

**Scale/Scope**: 819 signal rows / 855 items across 22 profiles; ~455 image-class, 364 video.
Steady state follows the monthly harvest cadence. Small-data — the risk is correctness and honest
absence, not throughput.

## Constitution Check

*GATE: evaluated before Phase 0 and re-evaluated after Phase 1 design.*

| Principle | Gate | Status |
|---|---|---|
| **I. Prefect orchestration** | Flows kebab-case; tasks `api-group.resource.action`; flows return the standard dict and never raise | **PASS** — flows `roach-extract-backfill`, `roach-extract-calibrate`, `extraction-vocabulary-sync`; tasks `roach.extract.flow-summary`, `roach.extract.attributes`, `extraction.record.store`, `extraction.quarantine.record`, `extraction.cost.project`, `google.drive.download-file`, `calibration.agreement.compute` |
| **II. External API standards** | Model selection is config per call site; every call attributable; **responses validated against a schema, retried once with the error fed back, then quarantined; unvalidated output MUST NOT reach storage** | **PASS — and this feature is the clause.** The current state violates it (no client-side validation, salvage regex, retry re-rolls the same payload, no quarantine). FR-016 – FR-021 implement it verbatim. |
| **III. Docker-first** | Runs in existing containers; deps pinned | **PASS** — roach rebuild + one read-only compose mount for `config/extraction/`. No new service, no new dependency. |
| **IV. Security** | No credentials in source; nothing sensitive logged | **PASS** — no new external surface. Quarantine stores model output, not secrets. |
| **V. Observability** | Skipped/failed items recorded with a classified reason, never silently dropped | **PASS** — FR-021's closed outcome vocabulary; `extraction_status` view gives every item exactly one reason; quarantine retains the evidence instead of discarding it. |
| **VI. Data honesty** | Observations and estimates must not share a field; absence explicit | **PASS** — FR-010 splits three kinds of missing subtitle; FR-033 forbids an uncalibrated projection wearing a measurement's form (R3); model confidence is stored as its own per-attribute field, never folded into the value. |
| **VII. Append-only observations** | Observations immutable | **PASS** — prior extractions are never mutated (FR-029); quarantine is append-only; a re-extraction is a new row, mirroring `metric_observations`. |
| **VIII. Evidence discipline** | Sample size visible; minimum sample enforced structurally | **PASS** — FR-014, FR-025, FR-027e; the agreement measurement refuses to report below a configured minimum; R8's lossy overlap shrinks the *reported* sample rather than being hidden. |
| **IX. Identity chain** | Traceability preserved | **PASS** — extractions key to `(platform, content_id)` and carry `account_id` / `run_id`, so an extraction traces to its account and the run that produced it. |
| **X. Polite collection** | No increase in request rate; no circumvention | **PASS** — zero platform requests. Backfill reads Drive; an unreachable Drive object is a classified skip, not a re-scrape (FR-034, FR-035). |
| **XI. Model call governance** | Deterministic first; batch; cache by hash; **enforced monthly ceiling**; marginal cost stated | **PASS with a narrowed documented gap** — see Complexity Tracking. Per-operation threshold confirmation (FR-037), dry-run, content-hash cache, per-call measured cost, and a **hard-stop monthly ceiling on extraction spend** (FR-037a) all land here. The ceiling gates **all three** costed paths — the recurring forward harvest as well as backfill and calibration — because the forward path is the continuous spender; a ceiling covering only the batch operations would leave the largest recurring spend ungated. Marginal cost is stated at the MVP boundary from the forward path's own measured usage, not deferred to the backfill pilot. What remains outside scope is a *cross-service* ceiling also covering songbird's generation spend. |
| **XII. Versioned vocabularies & additive schema** | Vocabulary versioned as data, not hardcoded; extraction records prompt/schema/model version; changes additive | **PASS** — FR-004, FR-028 – FR-030, FR-043a. Migration 009 is additive: new tables, no `DROP`, nothing tightened, nothing added to `harvested_signals`. Its single `ALTER` **widens** `runs.kind` by a strict superset, which cannot reject an existing row — the same replacement 008 made to `chk_capture_outcomes_kind`, and the only shape `.claude/rules/backend/schema.md` permits. |
| **Content Intelligence Domain Rules** | "Content flow MUST be an ordered sequence of beats, each with position, function from a closed vocabulary, and description. Derived attributes MUST be single values from fixed lists. Confidence is reported per attribute, not per extraction." | **PASS** — this feature implements that paragraph directly. |

**Post-Phase-1 re-evaluation**: unchanged. The design added no service, no abstraction layer, and no
dependency. Two things were *removed* rather than added — the regex salvage fallback and the
list-coercing `_to_text` — because FR-017 and FR-006 make both unsafe. The single Complexity
Tracking entry is a scope boundary, not a new violation.

## Project Structure

### Documentation (this feature)

```text
specs/007-structured-extraction/
├── plan.md              # This file
├── research.md          # Phase 0 — measured findings (R1–R12), open probes
├── data-model.md        # Phase 1 — tables, view, vocabulary, invariants
├── quickstart.md        # Phase 1 — runnable validation
├── contracts/
│   ├── extraction-schema.md      # the model-facing schema, both paths
│   ├── validation-outcome.md     # valid → stored | invalid → retry → quarantine
│   ├── backfill-costing.md       # dry-run, pilot calibration, thresholds
│   └── agreement-measurement.md  # controlled comparison + its stated limits
├── checklists/
│   └── requirements.md
├── spec.md
└── tasks.md             # Phase 2 — NOT created by /speckit-plan
```

### Source Code (repository root)

```text
config/
├── extraction/
│   ├── schema_v1.json                          # NEW — single source: response_format + validation
│   └── vocabulary_v1.yaml                      # NEW — beat functions; S-05 adds attribute dimensions here
└── postgres/
    ├── init.sql                                # MODIFIED — mirror 009 (greenfield parity)
    └── migrations/
        ├── 009_structured_extraction.sql       # NEW — 6 tables, 1 view, additive only
        └── 009_structured_extraction.down.sql  # NEW — real reversal

service/roach/
├── analyze.py                                  # MODIFIED — structured schema, client-side validation,
│                                               #   error-fed-back retry, quarantine envelope, usage
│                                               #   returned; regex salvage + _to_text coercion REMOVED
├── extraction_models.py                        # NEW — pydantic response model + cross-row validators
│                                               #   (contiguity, >=1 beat, one value per dimension)
├── api.py                                      # MODIFIED — /analyze returns analysis + usage
└── tests/
    └── test_analyze_validation.py              # NEW — fixture-driven; no live calls

service/prefect/
├── flows/
│   ├── extraction_vocabulary_sync.py           # NEW — config → extraction_vocabulary_terms
│   ├── roach_extract_backfill.py               # NEW — dry-run / --pilot / real; Drive-sourced
│   ├── roach_extract_calibrate.py              # NEW — controlled comparison over a fixed sample
│   └── common/
│       └── social_harvest.py                   # MODIFIED — persist structured extraction alongside
│                                               #   today's prose write; render beats for the sheet
├── tasks/
│   ├── extraction_tasks.py                     # NEW — store, quarantine, cost projection, ceiling,
│   │                                           #   render, per-path parity report
│   ├── extraction_vocabulary_tasks.py          # NEW — config -> table sync, frozen-version guard
│   ├── calibration_tasks.py                    # NEW — sample membership, agreement computation
│   └── google_tasks.py                         # MODIFIED — google.drive.download-file (does not exist)
├── prefect.yaml                                # MODIFIED — vocabulary-sync deployment; backfill and
│                                               #   calibrate unscheduled (costed, manual)
└── tests/
    ├── test_extraction_schema.py               # NEW — migration/constraint tests (real Postgres)
    ├── test_extraction_store.py                # NEW — versioning, idempotence, backfill isolation
    ├── test_extraction_quarantine.py           # NEW — countability, append-only
    ├── test_calibration_agreement.py           # NEW — sample fixity, exclusion from corpus
    └── test_schema_parity.py                   # unchanged; must still pass

docker-compose.yml                              # MODIFIED — mount config/extraction into roach + prefect
```

**Structure Decision**: Extends both existing services in place, matching features 004–006 (additive
migration, tasks beside their peers, thin flow entrypoints). Extraction storage gets its own task
module rather than joining `social_tasks.py`, because it performs no collection and no platform
I/O — the same split feature 006 made for `velocity_tasks.py`, and for the same reason: Constitution
II's retry carve-out distinguishes collection tasks from everything else, and mixing them blurs it.

## Design Decisions

### The three-way split of where things live

Roach holds the model call, so it owns validation and the retry — FR-018 requires the error go back
*to the model*, and only roach has the prompt and the media payload in hand. Routing that through
Prefect would mean shipping base64 media across the network to re-ask a question roach is already
positioned to ask (R6).

Roach still stores nothing. It returns one of two shapes, both `200 OK`:

- valid → `{ok: true, analysis: {status: "success", subtitle, beats[], attributes[], summary}, usage}`
- twice-invalid → `{ok: true, analysis: {status: "quarantined", attempts[], raw}, usage}`

**A quarantine must not be an HTTP error.** `.claude/rules/backend/roach.md` is explicit that the
`{ok: false, code, reason}` envelope means *platform* failure and that the harvest flow branches on
it — 404 skips the profile, 429 backs off and rotates egress. A model that wrote bad JSON is neither.
Returning 4xx/5xx here would make a healthy profile look blocked. This mirrors how analysis failure
is already reported today: `status: "failed"` inside a 200 body.

**One half of this was already broken, and is now fixed (2026-08-03, ahead of the rest of the
feature).** `INTERNAL_BUG_ERRORS` — the carve-out that stops roach's own bugs being reported as
platform failures — was wired into `/list` and `/download` but **not** the analysis path.
`analyze_item`'s blanket `except Exception` turned a `TypeError` from a signature drift into
`{status: "failed"}`: a well-formed result the harvest records against the item as
`analysis_status = 'failed'` before moving on, leaving the run green and nothing surfaced. It is why
the audit's 18 failed items cannot be told apart from roach bugs after the fact.

The tuple now lives in `analyze.py` (single definition, imported by `api.py`) and `analyze_item`
re-raises it. Verified in-container: a missing media file still returns `status="failed"` unchanged,
while an injected `TypeError` propagates. **Takes effect on the next
`docker-compose up -d --build roach`** — roach's source is baked into its image.

This matters for the feature, not just for hygiene: the quarantine vocabulary (`schema_invalid`,
`truncated`, `provider_error`, …) is only meaningful if roach's own defects cannot land in it. An
absorbent catch-all upstream would let a bug be counted as a model failure, and FR-020's counts
would quietly measure the wrong thing.

### One schema declaration, two consumers

The JSON Schema is written once in `config/extraction/schema_v1.json` and used for roach's
`response_format`, roach's client-side validation, and Prefect's storage-boundary re-validation.
Declared three times it would drift silently. Mounted read-only, following the
`config/field_availability.yaml` precedent exactly (R7).

The vocabulary is a file *and* a table: the file is the write path and reviewable as a diff, the
table is the read path that lets SC-002 ("every beat function valid under its recorded version") be
a SQL query rather than an application walk.

### Backfill writes a strict subset of what the forward path writes

The forward path writes the new extraction rows **and** today's prose columns. Backfill writes
**only** the extraction rows.

This is the single most dangerous line in the feature. Re-extraction produces a new subtitle; had
backfill written it to `harvested_signals.subtitle`, it would overwrite the stored transcript,
violating FR-009 and destroying data that cannot be regenerated without paying for it again.
`.claude/rules/backend/songbird.md` documents this exact bug class for metric refresh — writing
through `social.signal.record` instead of `record-metrics` blanks stored analysis "silently, and
this feature cannot regenerate it". The rule generalises: **a re-processing path must write a strict
subset of what the original path wrote** (R10).

### The dry-run is honest before it is useful

No cost baseline exists — not in roach's stdout, not in Prefect's log table, not in any table (R3).
So the first `--dry-run` cannot produce a calibrated number, and Constitution VI forbids dressing an
estimate as a measurement. The flow therefore has three modes:

| Mode | Model calls | Reports |
|---|---|---|
| `--dry-run` (no baseline yet) | 0 | item count in scope, and **"uncalibrated: no measured baseline for content_type X"** |
| `--pilot N` | N, real, recorded | measured per-item usage and cost by content type — this *is* the baseline |
| `--dry-run` (baseline present) | 0 | item count × measured per-item cost, stating the sample it rests on |
| (real run) | all in scope | actual spend, compared against the projection |

The baseline builds itself: FR-038 records measured cost on every extraction, so after the first
real backfill the projection is calibrated on hundreds of items rather than a pilot's handful.

### Absence gets a view, not a column

`extraction_status` answers "why does this item have no structured extraction" with exactly one
reason — never extracted, quarantined, media unreachable, cached at an older version, excluded as
calibration. This is the third time this shape appears (feature 005's why-empty query, feature 006's
`velocity_status`), and for the same reason: a stored status column needs maintaining on every write
and can silently disagree with the rows it describes.

### What gets deleted

Two existing behaviours are removed, not extended:

- `_extract_json`'s `\{.*\}` regex fallback ([analyze.py:171-174](../../service/roach/analyze.py#L171-L174)) — FR-017 makes a truncated response invalid, and this is the code path that makes it look valid.
- `_to_text`'s list coercion ([analyze.py:371-374](../../service/roach/analyze.py#L371-L374)) — it newline-joins arrays "because the model sometimes returns arrays despite the schema". Under FR-006 that response is invalid and must be retried, not silently reshaped.

Both deletions are the point of the feature, so both need a regression test asserting the old
behaviour is gone rather than merely unused.

## Marginal cost per extraction (FR-040, Constitution XI)

**Measured 2026-08-08** over **17 real extractions** — 5 through
`social-harvest-recent` (the forward path) and 12 through `--pilot 12` (the
backfill path). Read from the provider's own reported `cost_usd`, never
reconstructed from a price list (FR-038).

| content_type | media path | model served | n | avg cost |
|---|---|---|---|---|
| `video` | video | `xiaomi/mimo-v2.5` | 9 | **$0.000956** |
| `carousel` | image | `google/gemini-2.5-flash-lite` | 5 | **$0.000513** |
| `image` | image | `google/gemini-2.5-flash-lite` | 3 | **$0.000404** |
| `story` | — | — | 0 | **no measurement** |

**The video figure halved between the two samples** ($0.00193 at n=2 →
$0.000956 at n=9), which is the useful part of the exercise: per-item cost tracks
transcript length and video duration, so a 2-item sample was not a baseline in any
meaningful sense. It was still worth stating — Constitution XI requires marginal
cost *before* shipping, and "we'll measure later" is how a system ends up with no
baseline at all (research.md R3). It was labelled n=2 and has now been superseded.

**Steady state.** The monthly harvest is the recurring spender. At ~50 new items a
month the harvest costs roughly **$0.03–0.05**.
`EXTRACTION_MONTHLY_CEILING_USD` (default $25) is nowhere near binding at this
volume — it is a guard against a runaway batch, not a budget being approached.

**Full-corpus backfill**, as the flow itself now reports it:

```
covered_projection_usd : $0.5571     LOWER BOUND, 805 of 807 items
uncovered              : 2 story items — NOT estimated
```

`story` has no measurement of its own, and the flow refuses to borrow `image`'s.
Two items at any plausible rate is under a cent, so the practical bill is
**≈$0.56**, and the honest report still declines to state a single total.

**The second backfill pass (FR-043b)** is the same arithmetic on a corpus grown by
one month: **≈$0.58–0.62**. That is what makes "ship ahead of S-05" a decision
against a *known* second bill — at well under a dollar a pass, paying twice is
plainly cheaper than delaying the forward path, and the argument holds only while
the corpus is this size, which is itself the argument for running it sooner.

## Complexity Tracking

> Filled because Constitution Check flagged one item under Principle XI.

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| Constitution XI requires "a configured monthly ceiling MUST be enforced, not merely reported". This feature enforces a hard-stop ceiling over **extraction** spend (FR-037a), but not a **cross-service** ceiling that also counts songbird's generation spend. | A cross-service ceiling needs a shared spend ledger and a policy engine (halt / downgrade / defer) spanning two services that currently record nothing — Phase 0 measured zero usage records in roach's logs, zero in Prefect's `log` table, and no spend table anywhere (R3). That is a feature in its own right, not a rider on this one. This feature creates the per-call cost rows such a ledger would consume, and hard-stops its own spend against them in the meantime. | **Originally this entry claimed no ceiling at all was implementable here, and `/speckit-analyze` was right to reject that.** The argument was that a ceiling over extraction alone would report "within budget" while most spend sat outside the count. That risk is real but it is a *labelling* problem, not a reason to leave the largest new spender ungated — FR-037b resolves it by requiring the ceiling be surfaced as extraction-only, never as a system budget. Since FR-038 creates the cost rows from the moment US1 ships, the cumulative check costs one query. **Follow-up**: a cross-service spend-ledger feature consuming these rows. |

**Not a violation, but recorded because it looks like one:** `extraction_vocabulary_terms` is synced
from a version-controlled file on every run of `extraction-vocabulary-sync`, which updates rows in
place. This does not breach Principle VII — a vocabulary term is reference data, not an observation,
and the same pattern is already established by `field_availability` (feature 005). No extraction and
no assignment is ever mutated; a vocabulary *version* is immutable once any extraction has recorded
it, which `data-model.md` enforces with a constraint rather than a convention.
