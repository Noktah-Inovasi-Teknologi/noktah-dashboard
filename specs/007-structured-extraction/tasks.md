---

description: "Task list for 007-structured-extraction"
---

# Tasks: Structured Extraction Output

**Input**: Design documents from `/specs/007-structured-extraction/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md),
[data-model.md](./data-model.md), [contracts/](./contracts/), [quickstart.md](./quickstart.md)

**Tests**: Included, and not optional here. Two classes, for two different reasons:

- **Fixture-driven validation tests** (no network). The Constitution requires collection and
  extraction logic be developable against fixtures rather than live targets, and every rejection
  rule in [contracts/validation-outcome.md](./contracts/validation-outcome.md) is only observable by
  feeding the validator a bad response on purpose.
- **Real-Postgres schema tests** (`pytestmark = pytest.mark.schema`). `.claude/rules/backend/schema.md`
  requires partial indexes, composite FKs, and CHECK behaviour be tested against a real disposable
  database — this feature is entirely that class of change.

**Organization**: Grouped by user story so each is independently implementable and testable.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: US1–US5, mapping to spec.md user stories
- Exact file paths are given in every task

## Path Conventions

Existing repository layout — no new project scaffold, no new service:

- Config: `config/extraction/`, migrations `config/postgres/migrations/` mirrored into `init.sql`
- Roach (FastAPI, **source baked into the image — rebuild required**): `service/roach/`
- Prefect service: `service/prefect/{flows,tasks,tests}/`
- Deployments: `service/prefect/prefect.yaml`

---

## Phase 1: Setup

**Purpose**: Stand up the versioned config surface and the rehearsal clone before anything is
written, and record the one correctness fix already made.

- [X] T001 **Already done (2026-08-03, ahead of the rest of the feature).** Move `INTERNAL_BUG_ERRORS` into [service/roach/analyze.py](../../service/roach/analyze.py) as the single definition, import it in [service/roach/api.py](../../service/roach/api.py), and re-raise it in `analyze_item` before the catch-all. The guard existed for `/list` and `/download` but not the analysis path, so a `TypeError` became `{status: "failed"}` — indistinguishable from a model refusal, which is why the audit's 18 failed items cannot be attributed. Verified in-container: missing media still returns `status="failed"`; an injected `TypeError` propagates. **Takes effect on the roach rebuild in T066.**
- [X] T002 [P] Create `config/extraction/schema_v1.json` — the object schema from [contracts/extraction-schema.md](./contracts/extraction-schema.md): `subtitle`, `subtitle_absence`, `beats[]` (`minItems: 1`), `attributes[]`, `summary`; `additionalProperties: false` throughout. The `beats.function` enum is a placeholder here and is **generated from the vocabulary at load time** (T015), never hand-maintained in two places
- [X] T003 [P] Create `config/extraction/vocabulary_v1.yaml` — `dimension: beat_function` with exactly five terms per [data-model.md](./data-model.md) § `extraction_vocabulary_terms`: `hook`, `setup`, `main_point`, `call_to_action`, and `unclassified` (`is_residual: true`), each with the `description` the prompt is built from. Deliberately minimal per [research.md](./research.md) R11 — the residual share is the measurement that decides v2's membership
- [X] T004 Add read-only mounts for `./config/extraction` to **both** the `prefect` and `roach` services in [docker-compose.yml](../../docker-compose.yml), following the `config/field_availability.yaml` precedent at line 49. Roach currently mounts only `data/`, `secrets/`, and `social_data`
- [X] T005 [P] Create the rehearsal database clone so nothing is applied to `noktah_dashboard` while iterating: `script/db_rehearsal.sh create`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Schema, vocabulary, and the usage passthrough. Every user story writes
`content_extractions`, so none can begin until this is done.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

**Why the usage passthrough is foundational rather than part of US5**: FR-038 requires measured cost
on *every* extraction, not just backfilled ones. `_call_model` currently logs the usage object and
discards it before returning ([analyze.py:140-161](../../service/roach/analyze.py#L140-L161)), which
is why no cost baseline exists anywhere (R3). If US1 ships without this, every extraction it writes
has a null cost and the baseline starts accumulating late.

- [X] T006 Write `config/postgres/migrations/009_structured_extraction.sql` — the seven tables and one view from [data-model.md](./data-model.md): `content_extractions`, `extraction_beats`, `extraction_attributes`, `extraction_quarantine`, `extraction_vocabulary_terms`, `calibration_sample_members`, `extraction_batch_runs`, and the `extraction_status` view. Idempotent (`IF NOT EXISTS`, `pg_constraint` guards for named constraints), transactional, self-recording into `schema_migrations`. Additive — nothing added to `harvested_signals`, no `DROP`, nothing tightened
- [X] T006a **Widen `runs.kind`** from `('collection', 'generation')` to include `'extraction'`, by DROP + ADD with a strict superset, inside 009. This is the single `ALTER` in the migration and it is the **one shape of constraint replacement `.claude/rules/backend/schema.md` permits** — a superset cannot reject an existing row. Migration 008 set the precedent when it added `metric_refresh` to `chk_capture_outcomes_kind`. **Verify the re-added list still contains both original kinds** — a widening that silently drops one is a tightening wearing a superset's clothes
- [X] T007 Write `config/postgres/migrations/009_structured_extraction.down.sql` — a **real** reversal dropping all seven tables and the view, **and restoring `runs.kind`'s original two-value CHECK**. Unlike baseline migrations 001–003, every table here is new, so dropping destroys nothing that predates the migration. The `runs.kind` reversal will fail if any `kind = 'extraction'` row survives — drop those first, in the same script, since they are this migration's own rows
- [X] T008 Mirror 009 into [config/postgres/init.sql](../../config/postgres/init.sql) in the same order, with **constraint names matching exactly** — a named `ADD CONSTRAINT` in the migration versus an inline `REFERENCES` in `init.sql` produces different auto-generated names and fails parity (`.claude/rules/backend/schema.md`)
- [X] T009 Run `script/verify_schema_parity.sh` and confirm exit 0. Re-run after **any** later edit to either file
- [X] T010 Apply 009 to the rehearsal clone and confirm the seven tables and the view exist, and that `runs.kind` now accepts `'extraction'`. Apply an explicit filename — **never glob** `009_*.sql`, which also matches `009_*.down.sql` and would silently undo the migration
- [X] T011 [P] Create `service/prefect/tasks/extraction_vocabulary_tasks.py` with `extraction.vocabulary.sync` and `extraction.vocabulary.get` — read `config/extraction/vocabulary_v1.yaml`, upsert into `extraction_vocabulary_terms`, and **fail loudly rather than mutating a version whose `frozen_at` is set** (data-model invariant 5; silently mutating makes FR-030 retroactively false for every stored row). Also **fail when a synced dimension publishes no residual term** — FR-003 expresses "no discernible structure" as one beat carrying the residual, which is unsatisfiable without one, and `uq_vocabulary_residual` only bounds the count from above
- [X] T012 Create `service/prefect/flows/extraction_vocabulary_sync.py` — the `extraction-vocabulary-sync` flow with `--validate-only`, returning the standard `{start_time, end_time, data, summary, error}` dict and never raising (Constitution I)
- [X] T013 Return usage from roach: have `_call_model` in [service/roach/analyze.py](../../service/roach/analyze.py) return the usage object alongside the parsed content, and add `usage` plus `model_served`/`provider` (read from the response body, not assumed from the request) to the `/analyze` response in [service/roach/api.py](../../service/roach/api.py) — per [contracts/extraction-schema.md](./contracts/extraction-schema.md) § Response envelope. FR-024, FR-038
- [X] T014 [P] Write `service/prefect/tests/test_extraction_schema.py` (`pytestmark = pytest.mark.schema`) — asserts the composite FK rejects an out-of-vocabulary beat function, `uq_attribute_dimension` rejects a second value for one dimension, `uq_extraction_version` rejects a duplicate at the same version, and `chk_extraction_subtitle_absence` rejects a null subtitle with a null reason
- [X] T014a Apply 009 to `noktah_dashboard` — but **only** after T010 has exercised it on the rehearsal clone. Explicit filename, never a glob. The migration adds only new tables and widens one CHECK by a superset, so applying it ahead of the code that uses it is safe; it must happen here rather than in Phase 8 because T022 wires the **live** forward path in Phase 3 and T024 queries it, both against this database

**Checkpoint**: Schema, vocabulary, and cost passthrough ready — user stories can begin.

---

## Phase 3: User Story 1 — Count structural patterns across the corpus (Priority: P1) 🎯 MVP

**Goal**: Content flow becomes an ordered array of beats over a closed vocabulary, stored and
queryable without text parsing.

**Independent Test**: Harvest a few items, then run the beat-function query from
[quickstart.md](./quickstart.md) § 8 and get counts — no `LIKE`, no trigram, no manual reading.

- [X] T015 [US1] Add schema + vocabulary loading to [service/roach/analyze.py](../../service/roach/analyze.py): read `config/extraction/schema_v1.json` and the beat-function terms, and **generate the `beats.function` enum from the vocabulary** so the schema and the vocabulary cannot become two sources of truth for one closed list
- [X] T016 [US1] Define the pydantic response model in `service/roach/extraction_models.py` — `ExtractionResult` with `subtitle`, `subtitle_absence`, `beats[]`, `attributes[]`, `summary`. Pydantic rather than `jsonschema`: it is already present via FastAPI (R5) and its structured errors are what T027 quotes back to the model
- [X] T017 [US1] Rewrite `VIDEO_PROMPT` and `IMAGE_PROMPT` in [service/roach/analyze.py](../../service/roach/analyze.py) — name the vocabulary terms with their `description` text from the vocabulary, and state the three rules from [contracts/extraction-schema.md](./contracts/extraction-schema.md) § Prompt changes: every function must be a listed term (use `unclassified`, never invent), at least one beat, transcript verbatim and never trimmed to make room. Set `prompt_version = "v1"`
- [X] T017a [US1] Size `ANALYZE_VIDEO_MAX_TOKENS` for transcript **plus** structure in [service/roach/analyze.py](../../service/roach/analyze.py) (FR-011) — the schema adds `beats[]` and `attributes[]` to a response that previously carried three strings, so the output load rises the day US1 ships. Compute the ceiling from the observed transcript maximum plus the schema's worst-case structured payload, and **record the margin and its reasoning in [research.md](./research.md) R4**. R4's 4,812-char maximum is a *lower bound* — the salvage path T026 deletes was hiding truncation — so the margin must cover a maximum not yet observed. T036b's truncation count revises this on honest data after one month
- [X] T018 [US1] Unify the two paths in [service/roach/analyze.py](../../service/roach/analyze.py) so `analyze_video` and `analyze_images` request the **identical key set** (FR-023). Replace the image path's client-side `result.setdefault("subtitle", "")` ([analyze.py:312](../../service/roach/analyze.py#L312)) with an explicit `subtitle_absence = "not_applicable_no_audio"` (FR-010)
- [X] T019 [US1] Record the media path actually taken in the `/analyze` provenance — derived from the files analysed, not from the `content_type` label. A mixed carousel routes to the image path today ([analyze.py:350-352](../../service/roach/analyze.py#L350-L352)), and 6 rows in the live corpus are carousels holding real transcripts because of it (R1). FR-024
- [X] T019a [US1] Rebuild roach so T015–T019 take effect: `docker-compose up -d --build roach`. Roach's source is baked into its image, not volume-mounted — until this runs `/analyze` still returns three prose strings, T020's store task has no beats to write, and T024's end-to-end check cannot pass
- [X] T020 [P] [US1] Create `service/prefect/tasks/extraction_tasks.py` with `extraction.record.store` — writes one `content_extractions` row plus its `extraction_beats` rows in a single transaction, re-validating against the recorded schema version at the storage boundary (FR-016: no unvalidated path may exist). **This module must not import from `tasks/social_tasks.py`** — see T057
- [X] T021 [P] [US1] Add `extraction.flow.render` to `service/prefect/tasks/extraction_tasks.py` — deterministic beats → readable text, no model call, for the sheet's `content_flow` column so the reviewer workflow is not degraded (FR-041)
- [X] T022 [US1] Wire the forward path in [service/prefect/flows/common/social_harvest.py](../../service/prefect/flows/common/social_harvest.py) — store the structured extraction **alongside** today's prose write, using the rendered text for the sheet. The forward path writes both; backfill will write only the new tables (R10)
- [X] T023 [P] [US1] Write `service/prefect/tests/test_extraction_store.py` (`pytestmark = pytest.mark.schema`) — beat positions contiguous from 1, at least one beat per extraction, and the zero-row invariant queries from [data-model.md](./data-model.md) § Invariants (1) and (2)
- [X] T023a [P] [US1] Add a field-set parity assertion to `service/prefect/tests/test_extraction_store.py` — compare the stored field set of `media_path = 'video'` rows against `media_path = 'image'` rows and assert no structural difference other than subtitle content (SC-004). T018 unifies the two paths; this is what proves it stayed unified
- [X] T023b [P] [US1] Add `extraction.vocabulary.residual-share` to `service/prefect/tasks/extraction_tasks.py` — the share of beats carrying the residual function, groupable by month, per the trend query in [quickstart.md](./quickstart.md) § 8 (FR-005, SC-011). Without this the v2 vocabulary decision has no evidence, which is the entire reason R11 chose a minimal v1
- [X] T024 [US1] Verify SC-001 end to end: run the beat-function query from [quickstart.md](./quickstart.md) § 8 and confirm counts return with no text matching anywhere in the path
- [X] T024a [US1] State the marginal cost per extraction from the **forward path's own measured usage** (FR-040, Constitution XI) — after T024's first validated harvests, read `avg(cost_usd)` and token counts grouped by `content_type` from `content_extractions` and record them in [plan.md](./plan.md) with the sample size they rest on. T013 lands the usage passthrough in Phase 2, so this costs **zero extra model calls**: it reads spend the harvest was going to incur regardless. **The MVP does not ship until this is stated** — Constitution XI requires marginal cost before a feature adding model calls ships, and the pilot T069 depends on (T056) is three phases away

**Checkpoint**: Newly harvested items carry queryable structure. US1 is shippable alone.

---

## Phase 4: User Story 2 — Trust that what is stored was actually valid (Priority: P1)

**Goal**: Nothing reaches storage unvalidated. Invalid output retries once with the error fed back,
then quarantines intact and countable.

**Independent Test**: Feed the validator the nine bad-response fixtures from
[quickstart.md](./quickstart.md) § 3 and confirm each is rejected, nothing reaches
`content_extractions`, and the quarantine count increments.

**Why this is P1 alongside US1**: structured data that might be salvage is worse than prose, because
prose is visibly unreliable and salvaged structure is not.

- [X] T025 [US2] Check `finish_reason == "length"` **before parsing** in [service/roach/analyze.py](../../service/roach/analyze.py) and reject as `truncated` (FR-017). This is the single most important line in the change: a beat array cut off after three complete beats is *valid JSON satisfying the schema*, so only this check can catch it. `analyze.py` does not inspect `finish_reason` at all today
- [X] T026 [US2] **Delete** `_extract_json`'s `\{.*\}` regex fallback ([analyze.py:171-174](../../service/roach/analyze.py#L171-L174)) and **delete** `_to_text`'s list coercion ([analyze.py:371-374](../../service/roach/analyze.py#L371-L374)). Strict parse only. Removal, not bypass — the deletion is the point, and `_to_text` exists precisely because the model sometimes ignores the schema, which under FR-006 is a retry, not a repair
- [X] T027 [US2] Add the cross-row validators to `service/roach/extraction_models.py` — position contiguity and uniqueness, at least one beat, at most one value per dimension, `subtitle_absence` set iff `subtitle` is null, and every term a member of the recorded vocabulary version. JSON Schema can express `minItems` but not "contiguous" ([contracts/extraction-schema.md](./contracts/extraction-schema.md) § What the schema cannot express)
- [X] T028 [US2] Implement the single error-fed-back retry in [service/roach/analyze.py](../../service/roach/analyze.py) — append the invalid output and the validator's **own error text, quoted not paraphrased**, as a follow-up turn. Exactly one retry. **Do not re-send the media**; it is already in the conversation, and re-uploading base64 video to restate a JSON complaint would roughly double the input cost of every retry
- [X] T029 [US2] Return the quarantine envelope from `/analyze` in [service/roach/api.py](../../service/roach/api.py) as **`200 OK`** with `{ok: true, analysis: {status: "quarantined", attempts[], raw}}`. **Not 4xx/5xx**: the `{ok: false, code}` envelope means *platform* failure and the harvest flow branches on it — 429 backs off and rotates egress, 404 skips the profile. A model writing bad JSON would make a healthy profile look blocked ([contracts/validation-outcome.md](./contracts/validation-outcome.md))
- [X] T029a [US2] Rebuild roach so T025–T029 take effect: `docker-compose up -d --build roach`. T032 wires the flow to a quarantine envelope that does not exist until this runs
- [X] T030 [P] [US2] Add `extraction.quarantine.record` to `service/prefect/tasks/extraction_tasks.py` — append-only write of raw output, both attempts' errors, full version/model provenance, the owning `run_id`, and **the measured cost** (tokens were spent whether or not the output was usable; omitting it biases the baseline low). `run_id` is what lets a quarantine be attributed to the batch that produced it — without it SC-010 is unanswerable once the run ends
- [X] T031 [US2] Implement the closed `failure_kind` classification from [data-model.md](./data-model.md) — `schema_invalid`, `truncated`, `empty_content`, `out_of_vocabulary`, `provider_error`, `rate_limited`, `media_unavailable`, `unsupported_media`. **No `other`**: an escape hatch would absorb exactly the novel failures worth noticing (FR-021)
- [X] T032 [US2] Wire quarantine into [service/prefect/flows/common/social_harvest.py](../../service/prefect/flows/common/social_harvest.py) so a quarantined item **still** has its media delivered and metrics recorded (FR-022), preserving today's behaviour at [social_harvest.py:480-487](../../service/prefect/flows/common/social_harvest.py#L480-L487)
- [X] T032a [US2] Add `extraction.cost.ceiling-check` to `service/prefect/tasks/extraction_tasks.py` — sum measured `cost_usd` across `content_extractions` and `extraction_quarantine` for the current month, add the run's projection, and **hard-stop** when it would exceed `EXTRACTION_MONTHLY_CEILING_USD` (FR-037a, SC-019). Not a warning: Constitution XI requires the ceiling be *enforced*. **Landed here rather than in Phase 7** because the forward harvest is the continuous spender and the MVP ships at the end of this phase — a ceiling that arrives three phases after the spending starts is not a ceiling
- [X] T032b [US2] Gate the forward extraction path in [service/prefect/flows/common/social_harvest.py](../../service/prefect/flows/common/social_harvest.py) with `extraction.cost.ceiling-check` (FR-037a's "before **any** costed run begins"). When the ceiling is reached, extraction is skipped and recorded as `spend_ceiling_reached` — but **media is still delivered and metrics still recorded**, exactly as for a quarantine (FR-022). Halting collection to save extraction spend would trade an irreplaceable observation for a replaceable one
- [X] T033 [P] [US2] Write `service/roach/tests/test_analyze_validation.py` — the nine fixtures in [quickstart.md](./quickstart.md) § 3, no network. The truncation fixture must be well-formed valid JSON, so it only fails if T025 runs first
- [X] T034 [P] [US2] Add retry assertions to `service/roach/tests/test_analyze_validation.py` — exactly two model calls; the second request contains the first response verbatim plus the validator's error text; the media part is **not** re-sent; a second failure yields a quarantine and zero `content_extractions` rows
- [X] T035 [P] [US2] Add deletion-regression assertions to `service/roach/tests/test_analyze_validation.py` — grep the module source and assert the regex fallback and `_to_text` are **absent**, not merely uncalled. A test that stops calling them leaves SC-006 unverifiable the moment someone reintroduces the path
- [X] T036 [US2] Write `service/prefect/tests/test_extraction_quarantine.py` (`pytestmark = pytest.mark.schema`) — quarantine is append-only, counts group by model/prompt version/schema version/failure kind (FR-020), and every row can be opened to its raw text
- [X] T036a [US2] Add `extraction.store.revalidate` to `service/prefect/tasks/extraction_tasks.py` — walk the whole store and re-validate each row against **its own recorded schema version**, reporting zero failures (SC-005). Distinct from write-time validation: it catches a row stored before a validator bug was fixed, and it is the only check that a *past* version is still interpretable (FR-030)
- [X] T036b [US2] Break the `truncated` count out by `content_type` in `extraction.quarantine.count` (`service/prefect/tasks/extraction_tasks.py`), so T017a's budget can be revised on evidence after one month (FR-011). The observed 4,812-char transcript maximum is right-censored by the salvage path T026 deletes (R4), so the budget cannot be sized honestly until truncation is countable — and it must be countable from the day the forward path ships, not from whenever backfill first runs

**Checkpoint**: Nothing unvalidated can reach storage. US1 + US2 is the honest MVP.

---

## Phase 5: User Story 3 — Per-attribute confidence (Priority: P2)

**Goal**: Attributes emitted one-per-dimension with their own confidence, filterable by a minimum.
**Inert until S-05 publishes dimensions** — that is FR-043's correct state, not a defect.

**Independent Test**: With at least one dimension published, confirm each dimension holds at most one
value with its own confidence, then apply a minimum-confidence filter and see both the survivors and
their count.

- [X] T037 [US3] Extend the vocabulary sync in `service/prefect/tasks/extraction_vocabulary_tasks.py` to carry attribute dimensions alongside `beat_function`, so S-05 populates this mechanism rather than introducing a second one (FR-043a)
- [X] T038 [US3] Parse and validate `attributes[]` in `service/roach/extraction_models.py` — at most one value per dimension, `confidence` ∈ `high`/`medium`/`low`. An ordered scale, not a float: a self-reported `0.83` implies a calibration the model does not have (Constitution VI)
- [X] T039 [P] [US3] Extend `extraction.record.store` in `service/prefect/tasks/extraction_tasks.py` to write `extraction_attributes` rows with their `vocabulary_version` (FR-015)
- [X] T040 [US3] Confirm graceful degradation in `service/roach/extraction_models.py` and `service/prefect/tasks/extraction_tasks.py`: with **zero** published dimensions, extraction still produces flow, subtitle, and summary, records zero attributes, and invents no dimension (FR-015, FR-043)
- [X] T041 [P] [US3] Add a confidence-filter query to [quickstart.md](./quickstart.md) validation and a test in `service/prefect/tests/test_extraction_store.py` asserting the filter returns both survivors and their count (FR-014, SC-012)

**Checkpoint**: The attribute mechanism is built and correct while empty.

---

## Phase 6: User Story 4 — Know whether the two models agree (Priority: P2)

**Goal**: Agreement established by controlled comparison — same items, same input, two models —
never inferred from side-by-side distributions.

**Independent Test**: Run the calibration and confirm the report carries per-dimension and
per-beat-position agreement with sample sizes, names the covered media classes, and states that
video is not covered.

**⚠️ Spends money.** T042's single probe gates the rest.

**⚠️ Phase number ≠ delivery order.** Phases are numbered by spec priority (US4 is P2, US5 is P3),
but **Phase 7 must run before Phase 6** — not merely as a preference. Backfill establishes the
measured cost baseline that makes this phase's dry-run meaningful, and T044a consumes T053 and
T061 directly. See Implementation Strategy.

**T051a/T051b cost nothing.** The observational parity report is a query over extractions that
already exist; only T042–T050 spend.

- [X] T042 [US4] **One live probe, not a batch**: send a single image item to `xiaomi/mimo-v2.5` and record the outcome. R8 infers from `analyze.py`'s own comments that the video model's providers did not reliably accept image input — which is why `OPENROUTER_IMAGE_MODEL` exists. If still true, part of the 455-item overlap will fail, and **that is a finding, not sample attrition**. Choose the sample size after this
- [X] T043 [P] [US4] Add `calibration.sample.populate` to `service/prefect/tasks/calibration_tasks.py` — fixed membership in `calibration_sample_members` over image-class items only (`image`, `carousel`, `story`; 455 of 819 live rows). A `LIMIT` or a random draw would mean a changed agreement rate could be the new model or the new sample (FR-027b)
- [X] T044 [US4] Create `service/prefect/flows/roach_extract_calibrate.py` — `roach-extract-calibrate` with `--dry-run`, `--sample`, `--confirm`, and `--report`, extracting each sampled item under **both** models at the same prompt and schema version, stored with `purpose = 'calibration'`
- [X] T044a [US4] Put calibration on backfill's costing terms in `service/prefect/flows/roach_extract_calibrate.py` (FR-027e) — call `extraction.cost.ceiling-check` (T032a) before any model call, wire `extraction.cost.project` (T053) behind `--dry-run`, and `EXTRACTION_SPEND_THRESHOLD_USD` (T061) behind `--confirm`. Record the run through `extraction.batch.record-start`/`-finish` (T053a) with `batch_kind = 'calibration'`, so a calibration's cost and outcomes are auditable on the same terms as a backfill's. **Depends on T053, T053a and T061**, which is why the delivery order runs Phase 7 first. Flags without a projection behind them are a formality, not a control
- [X] T045 [US4] Add `calibration.agreement.compute` to `service/prefect/tasks/calibration_tasks.py` — computed from stored extractions on demand, never stored. A stored rate could silently disagree with the extractions it summarises, and re-deriving after a vocabulary change would need a migration
- [X] T046 [US4] Enumerate disagreements **with direction** (`A=hook B=setup` × 9) in `service/prefect/tasks/calibration_tasks.py`, rather than a single blended score, and report per-position rather than one averaged figure (FR-026). Neither model is treated as ground truth — there is no "accuracy", only agreement
- [X] T047 [US4] Implement the same-model short-circuit: when both paths resolve to one model, report that no cross-model comparison applies and make **zero** calls. `IMAGE_MODEL` falls back to `MODEL` when unset ([analyze.py:26](../../service/roach/analyze.py#L26)), so this is one env var away (FR-027d)
- [X] T048 [US4] Exclude pairs whose `model_served` does not match the intended pair in `service/prefect/tasks/calibration_tasks.py`, with the exclusion counted — `allow_fallbacks: true` means a pair can be served by other models, in which case the comparison is not between the two models it claims to compare
- [X] T049 [US4] Enforce `CALIBRATION_MIN_SAMPLE` (default 30 usable pairs) in `service/prefect/tasks/calibration_tasks.py` — below it, report insufficient data and emit **no** agreement rate, not a weak one. Not overridable by flag (Constitution VIII)
- [X] T050 [US4] State the uncovered classes in the report emitted by `service/prefect/flows/roach_extract_calibrate.py`, in the same output as the number — video (364 items) is not covered and no figure is asserted for it (FR-027a, SC-017). A rate quoted without this reads as a property of the system rather than of one media class
- [X] T051 [P] [US4] Write `service/prefect/tests/test_calibration_agreement.py` (`pytestmark = pytest.mark.schema`) — sample fixity across re-runs, and that `purpose = 'calibration'` rows appear in **no** analytical figure (SC-018, data-model invariant 6)
- [X] T051a [P] [US4] Add `extraction.parity.report` to `service/prefect/tasks/extraction_tasks.py` — the **observational** per-path report: beat-function and attribute distributions grouped by `media_path`, each with its observation count, over `purpose = 'production'` rows only (FR-025, SC-013). Costs **zero model calls** — it is a query over extractions that already exist. This is the spec's "Parity report" entity, which until now appeared in no artifact but `spec.md`
- [X] T051b [US4] Make `extraction.parity.report` in `service/prefect/tasks/extraction_tasks.py` state its confound in its own output: video and image content differ substantively as well as by model, so a divergence here is **not** evidence of a model difference unless the controlled comparison (T045) supports it (FR-026, FR-027f). A divergence must carry its observation count and must never be normalised into a combined figure

**Checkpoint**: Cross-format findings can state their model effect instead of carrying it unmeasured.

---

## Phase 7: User Story 5 — Re-extract the corpus, knowing the bill first (Priority: P3)

**Goal**: Backfill as a costed batch operation with an honest dry-run, sourcing media from Drive and
never from a platform.

**Independent Test**: Dry-run over the full corpus reports scope and projection with zero model
calls; a small real run lands within ±25% of the projection.

**⚠️ Spends money** from T055 onward.

- [X] T052 [US5] Add `google.drive.download-file` to [service/prefect/tasks/google_tasks.py](../../service/prefect/tasks/google_tasks.py) — **it does not exist**; only `drive_file_upload` (:367) and `drive_file_delete` (:455) do (R2). Existing scopes (`drive.readonly` + `drive.file`) already cover reading files this app created, so no re-consent is needed
- [X] T053 [P] [US5] Add `extraction.cost.project` to `service/prefect/tasks/extraction_tasks.py` — reads measured per-item usage from `content_extractions` and `extraction_quarantine`, grouped by content type, returning both the projection **and** its basis and per-class sample size
- [X] T053a [P] [US5] Add `extraction.batch.record-start` and `extraction.batch.record-finish` to `service/prefect/tasks/extraction_tasks.py` — write the `runs` row (`kind = 'extraction'`) and its `extraction_batch_runs` detail row: scope description, items in scope, projection and its basis, ceiling and month-to-date at the moment of the check, confirmation state; then at finish, measured `actual_usd` and the five outcome counts. **This is the spec's "Backfill batch" entity, which had no persistent home** — without it SC-010 and SC-008 are answerable only while watching the run, and a quarantine cannot be attributed to the batch that caused it
- [X] T054 [US5] Create `service/prefect/flows/roach_extract_backfill.py` — `roach-extract-backfill` with `--dry-run`, `--pilot N`, `--confirm`, and the scope selectors in [contracts/backfill-costing.md](./contracts/backfill-costing.md). Scope is "everything present when the run starts", **never a literal count** — the corpus was 656 at audit and is 819 today (R1)
- [X] T055 [US5] Implement the uncalibrated dry-run in `service/prefect/flows/roach_extract_backfill.py`: with no measured baseline, report `PROJECTED SPEND: UNCALIBRATED` naming the uncovered content types. This is the correct first output, not a failure — no cost baseline exists anywhere in this system (R3), and a confident dollar figure would be a fabrication sharing a field with a measurement (Constitution VI, FR-033)
- [X] T056 [US5] Implement `--pilot N` in `service/prefect/flows/roach_extract_backfill.py` — a small measured sample that establishes the baseline, after which `--dry-run` reports a projection with its basis and sample size
- [X] T057 [US5] **Backfill writes only the new extraction tables — never `harvested_signals`.** Re-extraction produces a new subtitle; writing it back would overwrite the stored transcript irreversibly, since `harvested_signals` holds exactly one row per item. Same bug class `.claude/rules/backend/songbird.md` documents for metric refresh (R10, FR-009)
- [X] T058 [P] [US5] Add the two-layer guard to `service/prefect/tests/test_extraction_store.py` per [data-model.md](./data-model.md) § Invariant 7 — a **static import guard** asserting `extraction_tasks.py` does not import the signal writer, plus a **before/after snapshot** asserting `harvested_signals` is byte-identical across a backfill run. The first says *this cannot be written*; the second says *this did not happen*
- [X] T059 [US5] Classify unreachable media as `media_unavailable` and count it, in `service/prefect/flows/roach_extract_backfill.py` (FR-035). All 855 items carry a `drive_file_id`, but that does not prove the Drive object still exists — deletion or a permissions change surfaces only at fetch time. **Re-downloading from the platform is not a fallback and no code path may offer it** (Constitution X)
- [X] T060 [US5] Rely on `uq_extraction_version` for idempotence in `service/prefect/flows/roach_extract_backfill.py` (FR-036) — a re-run conflicts and skips before any model call, so a bug in the skip logic cannot cause a double charge. Add the content-hash cache as the second gate (FR-039)
- [X] T061 [US5] Implement `EXTRACTION_SPEND_THRESHOLD_USD` (default $5.00) in `service/prefect/flows/roach_extract_backfill.py`, refusing to run without `--confirm`, as env configuration rather than a flag — a per-run override would make it a formality (FR-037)
- [X] T061a [US5] Call `extraction.cost.ceiling-check` (landed as T032a in Phase 4) from `service/prefect/flows/roach_extract_backfill.py` before any model call — a hard stop, not a warning (FR-037a, SC-019). Backfill is the largest single spender this feature introduces, so it is gated at the flow boundary as well as by the per-run threshold in T061
- [X] T061b [US5] Label **every** surfacing of the ceiling figure as **extraction spend only** (FR-037b) — in `service/prefect/flows/roach_extract_backfill.py` (T061a), in `service/prefect/flows/roach_extract_calibrate.py` (T044a), and in the forward path's summary (T032b). Presenting it as a system budget while songbird's generation spend sits outside the count would report "within budget" on incomplete evidence — the failure Constitution VI names
- [X] T062 [US5] Emit the run summary from `service/prefect/flows/roach_extract_backfill.py` **and persist it** via `extraction.batch.record-finish` (T053a) with every item resolving to exactly one outcome and `unclassified: 0` (SC-010). A non-zero value there is the silent partial success Constitution V calls the most dangerous failure mode in this system — so it is **recorded, not rejected**: a schema constraint forbidding it would make the run fail to report its own defect
- [X] T062a [US5] Report `actual_usd` against `projection_usd` in the same summary (SC-008) — within ±25%, or **the divergence with its cause**. Both figures live on the `extraction_batch_runs` row (T053a), so this is a comparison over stored values rather than a number that exists only in a log line
- [X] T062b [P] [US5] Add the batch-accounting assertion to `service/prefect/tests/test_extraction_store.py` — the zero-row query from [data-model.md](./data-model.md) § `extraction_batch_runs` proving every finished non-dry-run accounts for every item in scope (invariant 10, SC-010)

**Checkpoint**: The existing corpus is re-extractable against a known bill.

---

## Phase 8: Polish & Cross-Cutting Concerns

- [X] T063 [P] Validate the `extraction_status` view defined in `config/postgres/migrations/009_structured_extraction.sql` against **real** data — confirm its item universe is the UNION of `harvested_signals` and `content_extractions`, not extractions alone. Feature 006 found 2 of 819 rows sitting in exactly that gap, and only against real data, not from any test written beforehand
- [X] T064 [P] Add the `extraction-vocabulary-sync` deployment to [service/prefect/prefect.yaml](../../service/prefect/prefect.yaml); leave `roach-extract-backfill` and `roach-extract-calibrate` **unscheduled** — both spend money and are triggered by hand
- [X] T065 Register the deployments declared in [service/prefect/prefect.yaml](../../service/prefect/prefect.yaml): `docker exec prefect prefect deploy --all`
- [X] T066 Rebuild roach from [service/roach/Dockerfile](../../service/roach/Dockerfile) so every `service/roach/` change in this feature — including T001, already committed — takes effect: `docker-compose up -d --build roach`. Roach's source is baked into its image, not volume-mounted
- [X] T067 Re-run `script/verify_schema_parity.sh` and confirm the seven tables and the view exist in `noktah_dashboard`. The migration itself was applied in T014a — it has to precede Phase 3, because T022 wires the live forward path and T024 queries it
- [X] T068 [P] Update [.claude/CLAUDE.md](../../.claude/CLAUDE.md) and [.claude/rules/backend/roach.md](../../.claude/rules/backend/roach.md) — the new flows and their commands, the quarantine envelope's 200-not-4xx rule, the vocabulary config location, and the backfill-writes-a-subset rule. `.claude/rules/backend/schema.md` gains the 009 tables
- [X] T069 **Refine** T024a's marginal-cost statement in [plan.md](./plan.md) using the pilot's and the completed backfill's measured usage — a far larger and more content-type-balanced sample than the forward path alone (FR-040, Constitution XI). **Also state the projected cost of the second backfill pass** (FR-043b): once pass 1's baseline exists this is a multiplication, not a guess, and it is what makes "ship ahead of S-05" a decision against a known second bill
- [X] T070 Run the full [quickstart.md](./quickstart.md) sequence top to bottom and confirm each expected output, including § 9's `UNRESOLVED` returning zero rows

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: no dependencies. T001 is already complete
- **Foundational (Phase 2)**: needs Phase 1 — **blocks every user story**
- **US1 (Phase 3)**: needs Phase 2. No dependency on other stories
- **US2 (Phase 4)**: needs Phase 2. Touches the same roach module as US1, so run **after** US1 rather than beside it
- **US3 (Phase 5)**: needs Phase 2 and US1's store task (T020)
- **US4 (Phase 6)**: needs US1 and US2 — a calibration compares *validated* extractions, so quarantine must exist first — **and needs US5's costing tasks T053 and T061**, because FR-027e puts calibration on backfill's terms (T044a). This is a hard dependency, not a preference: Phase 7 must precede Phase 6
- **US5 (Phase 7)**: needs US1 and US2 for the same reason
- **Polish (Phase 8)**: after the stories being shipped

### Story Independence

US1 and US2 are both P1 and are the honest MVP together: US1 alone produces structured data whose
trustworthiness is unverified, which is the failure mode US2 exists to prevent. US3, US4, and US5
are each independently testable once US1+US2 land, and can be delivered in any order.

### Parallel Opportunities

- T002, T003, T005 (Phase 1) — different files
- T011, T014 (Phase 2) — task module and its tests
- T020, T021, T023 (US1) — store task, render task, tests
- T030, T033, T034, T035 (US2) — the quarantine task and three independent test files
- T043, T051 (US4); T053, T058 (US5); T063, T064, T068 (Polish)

**Not parallel, despite appearances**: T015–T019, T025–T029 all edit
`service/roach/analyze.py`. They are sequential regardless of story.

---

## Parallel Example: User Story 2

```bash
# After T025-T032 land, the four test tasks touch four different files:
Task: "T030 extraction.quarantine.record in service/prefect/tasks/extraction_tasks.py"
Task: "T033 nine rejection fixtures in service/roach/tests/test_analyze_validation.py"
Task: "T034 retry assertions in service/roach/tests/test_analyze_validation.py"
Task: "T036 quarantine countability in service/prefect/tests/test_extraction_quarantine.py"
```

---

## Implementation Strategy

### MVP (US1 + US2)

1. Phase 1 Setup → Phase 2 Foundational
2. Phase 3 (US1) → structured beats stored and queryable
3. Phase 4 (US2) → nothing unvalidated can reach storage
4. **STOP and VALIDATE**: quickstart §§ 1–5 and § 8
5. **Ship gates** — the MVP is not shippable until both are met: marginal cost per extraction is
   stated (T024a, Constitution XI) and the monthly ceiling gates the forward path (T032a/T032b,
   FR-037a). Both were originally in Phase 7, three phases after the spending starts
6. New harvests accrue structure from this point; the existing corpus waits for US5

Shipping US1 without US2 is explicitly **not** the MVP. It produces a store that looks authoritative
and is not — which is worse than the prose it replaces, because prose is visibly unreliable.

### Incremental Delivery

| Increment | Adds | Spends |
|---|---|---|
| US1 + US2 | forward path produces validated structure | normal harvest analysis only |
| US5 | the 819-row existing corpus | pilot + backfill, projected first |
| US4 | measured model agreement | calibration over ~455 items |
| US3 | attributes | nothing until S-05 publishes dimensions |

US5 before US4 is **required**, not merely deliberate: backfill establishes the measured cost
baseline that makes US4's dry-run meaningful, T044a consumes T053 and T061 directly, and US5 is the
increment that makes the whole corpus queryable rather than only new arrivals.

---

## Notes

- `[P]` = different files, no dependencies on incomplete tasks
- Two roach changes are **deletions** (T026) — verify the code is gone, not merely unused (T035)
- Every roach change needs a rebuild to take effect; the Prefect service is volume-mounted and does
  not. Rebuilds land **inside** the phases that depend on them (T019a, T029a), with T066 as a final
  catch-all — a rebuild deferred to Polish would leave both MVP checkpoints unreachable
- Probe sparingly (T042): each live call is a real request, and `.claude/rules/backend/roach.md`
  warns that a handful of exploratory calls earns a throttle the next scheduled harvest pays for
- Commit after each task or logical group; stop at any checkpoint to validate independently
