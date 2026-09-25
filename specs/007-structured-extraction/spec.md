# Feature Specification: Structured Extraction Output

**Feature Directory**: `specs/007-structured-extraction`

**Created**: 2026-08-03

**Status**: Draft

**Input**: User description: "Convert Roach's extraction output from free prose into validated structured data, so content attributes become queryable rather than requiring text parsing."

---

## Correction to the input premise

The brief states that "`openrouter_tasks.py` already uses strict `json_schema` for Songbird but
`analyze.py` does not — it parses JSON from the response body directly." Read against the source,
that is half right, and the half that is wrong changes what the work actually is.

**Both call sites already send `strict: true` `json_schema`** —
[analyze.py:100-118](service/roach/analyze.py#L100-L118) and
[openrouter_tasks.py:135-165](service/prefect/tasks/openrouter_tasks.py#L135-L165). Adding
`json_schema` to `analyze.py` is not the gap. The real gaps, shared by both paths, are:

1. **No client-side validation of any kind.** Provider-side `strict` is honoured only by providers
   that implement it, and `analyze.py` routes across four providers with `allow_fallbacks: true`
   ([analyze.py:39-45](service/roach/analyze.py#L39-L45)). Whatever comes back is parsed and used.
2. **Truncation is salvaged, not rejected.** `_extract_json` falls back to a `\{.*\}` regex over
   the raw text ([analyze.py:164-175](service/roach/analyze.py#L164-L175)), so a response cut off
   at `max_tokens` can parse successfully with content silently missing. The Songbird side at
   least warns on `finish_reason == "length"`
   ([openrouter_tasks.py:258-265](service/prefect/tasks/openrouter_tasks.py#L258-L265));
   `analyze.py` does not check `finish_reason` at all.
3. **Retries resend the identical payload.** Neither path feeds the validation error back to the
   model, so a retry is a re-roll, not a correction.
4. **No quarantine.** Every failure collapses to `status: "failed"` with all three fields `""`
   ([analyze.py:367-368](service/roach/analyze.py#L367-L368)). The raw output is discarded, so a
   schema failure is indistinguishable from a network failure after the fact.
5. **List-valued output is coerced, not rejected.** `_to_text`
   ([analyze.py:371-374](service/roach/analyze.py#L371-L374)) newline-joins arrays "because the
   model sometimes returns arrays despite the schema" — which is direct evidence that provider-side
   `strict` is not being honoured on every call.

So the requirement is not "apply `json_schema` to `analyze.py`". It is: **validate client-side,
stop salvaging, feed the error back on retry, and quarantine what still fails** — while extending
the schema from three strings to a structured shape. This spec is written against that.

---

## Clarifications

### Session 2026-08-03

- **Q: How is comparability across the two model paths established?**
  **A: By controlled comparison.** A fixed sample is run through both models on identical input and
  the agreement rate is reported. Observational side-by-side distributions were rejected as
  insufficient: video and image content differ substantively, so an observational divergence cannot
  be attributed to the model. The controlled comparison costs additional model calls and is bounded
  by an asymmetry — only media both models accept can be compared — and both consequences are
  specified rather than assumed away (FR-027 – FR-027e).

- **Q: Does this feature ship ahead of the attribute vocabulary (S-05), or wait for it?**
  **A: Ship ahead.** Flow structure, validation, quarantine, and versioning land first; the
  attribute mechanism is built and inert until the vocabulary publishes dimensions. This accepts a
  second backfill when S-05 lands. It also means **this feature establishes the vocabulary storage
  and versioning mechanism** that S-05 then populates, rather than S-05 inventing one (FR-043,
  FR-043a).

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Count structural patterns across the corpus (Priority: P1)

A strategist wants to know how many of an account's best-performing Reels open with a question
hook, and whether the ones that do outperform the ones that open with a demonstration. Today the
only way to answer this is to read every row of prose by hand, or to write text-matching heuristics
over `content_flow` that will quietly misfire on phrasing the model happened to vary.

After this feature, content flow is an ordered sequence of beats, each carrying a position, a
function drawn from a closed vocabulary, and a description. The question becomes a query that
returns counts.

**Why this priority**: This is the feature. Everything else — attributes, confidence, parity,
backfill — is either a guard on this data or an extension of it. Structured flow on newly harvested
items alone is a shippable, useful product: the corpus grows monthly, and from the day this lands,
new content is countable.

**Independent Test**: Harvest a handful of items, then ask "how many beats of function `hook`
exist, grouped by content type" and get an answer with no text parsing anywhere in the path.

**Acceptance Scenarios**:

1. **Given** a newly harvested video item, **When** extraction completes successfully, **Then** its
   flow is stored as an ordered list of beats, each with a contiguous position, a function that is
   a member of the beat-function vocabulary, and a non-empty description.
2. **Given** a stored corpus of extractions, **When** an analyst filters by beat function,
   **Then** the result is produced by structured filtering, and the same query run twice on
   unchanged data returns identical counts.
3. **Given** an item whose structure the model cannot identify, **When** extraction completes,
   **Then** the item has exactly one beat carrying the explicit residual function — never zero
   beats, and never a fabricated structure.
4. **Given** any stored extraction, **When** its beat functions are checked against the vocabulary
   version it recorded, **Then** every function is a member of that version.

---

### User Story 2 - Trust that what is stored was actually valid (Priority: P1)

An analyst runs a count and gets a number. That number is only worth anything if nothing in the
store arrived by salvage. Today a truncated response can be regex-scraped into something that
parses, and a model that ignores the schema has its arrays newline-joined into text. Both produce
storable-looking output from a failed call.

After this feature, model output is validated against the declared schema by the system itself
before anything is stored. Invalid output is retried exactly once with the validation error fed
back to the model. If it fails again it is quarantined — retained in full, never stored as an
extraction — and quarantined extractions are countable.

**Why this priority**: Equal to US1. Structured data that might be salvage is worse than prose,
because prose is visibly unreliable and salvaged structure is not. Shipping US1 without US2 creates
a store that looks authoritative and is not.

**Independent Test**: Feed the validator known-bad outputs — a truncated array, an out-of-vocabulary
function, duplicate positions, a missing required field — and confirm nothing reaches the extraction
store, the retry carried the error text, and the quarantine count incremented for each.

**Acceptance Scenarios**:

1. **Given** model output that does not satisfy the declared schema, **When** it is received,
   **Then** it is not written to the extraction store under any circumstance.
2. **Given** a first invalid response, **When** the system retries, **Then** the retry request
   includes the specific validation error from the first attempt, and there is exactly one retry.
3. **Given** a second invalid response, **When** the retry also fails validation, **Then** the raw
   output, the validation errors from both attempts, and the version metadata are retained as a
   quarantine record.
4. **Given** a response whose completion was cut off at the token limit, **When** it is processed,
   **Then** it is treated as invalid regardless of whether the visible fragment happens to parse.
5. **Given** a set of quarantine records, **When** an operator asks for quarantine counts,
   **Then** counts are available broken down by model, prompt version, schema version, and failure
   kind.
6. **Given** an extraction that quarantines, **When** the harvest continues, **Then** the item's
   media is still delivered and its metrics are still recorded — extraction failure never fails
   collection.

---

### User Story 3 - Exclude low-confidence attributes rather than silently inheriting them (Priority: P2)

An analyst wants to segment content by a categorical attribute, but only where the extraction was
actually confident about it. Today confidence does not exist at all; if it existed per extraction,
one shaky attribute would either poison the whole record or, worse, ride along invisibly with the
confident ones.

After this feature, extraction emits at most one value per attribute dimension, each carrying its
own confidence, so a low-confidence assignment can be excluded without discarding the rest of the
extraction.

**Why this priority**: Depends on the attribute vocabulary, which this feature does not define
(see Dependencies). The mechanism — per-attribute assignment with per-attribute confidence,
recorded against a vocabulary version — is built here and is inert but correct when the vocabulary
publishes no dimensions.

**Independent Test**: With a vocabulary of at least one dimension published, extract items and
confirm each dimension holds at most one value with its own confidence; then apply a minimum
confidence filter and confirm both the surviving assignments and their count are reported.

**Acceptance Scenarios**:

1. **Given** an extraction, **When** its attributes are inspected, **Then** each dimension carries
   at most one value, and each assigned value carries its own confidence.
2. **Given** a mix of confidences within one extraction, **When** a minimum confidence filter is
   applied, **Then** assignments below the threshold are excluded while the extraction's other
   assignments, its flow, and its subtitle remain available.
3. **Given** any aggregate computed over attribute assignments, **When** it is surfaced, **Then**
   it reports the number of assignments it rests on.
4. **Given** an attribute vocabulary that publishes no dimensions, **When** extraction runs,
   **Then** flow, subtitle, and summary are still produced, zero attributes are recorded, and no
   dimension is invented.
5. **Given** any attribute assignment, **When** it is read, **Then** it carries the vocabulary
   version under which it was assigned.

---

### User Story 4 - Know whether the two models actually agree (Priority: P2)

Video items are analysed by one model and image items by another. If those two models label content
differently — one favouring `hook` where the other says `setup`, one more willing to assign a
confident attribute — then every cross-format comparison in the system is measuring the model, not
the content. Reading the two paths' distributions side by side cannot tell them apart, because
video and image content genuinely differ: a Reel really does open differently from a carousel.

So agreement is established by running a fixed sample of items through **both** models on identical
input and measuring how often they agree. That is the only comparison that isolates the model.

**Why this priority**: The divergence risk exists the moment US1 ships, and the store records
enough to detect it after the fact, so it need not gate the first release. It must not be deferred
indefinitely: every cross-format finding produced before this exists carries an unmeasured
model effect.

**Independent Test**: Run the calibration over its fixed sample and confirm it reports a
per-dimension and per-beat-function agreement rate with its sample size, names the media class the
comparison was possible on, and states that the result does not extend to media only one model can
accept.

**Acceptance Scenarios**:

1. **Given** a fixed calibration sample of items both models accept, **When** the comparison runs,
   **Then** each item is extracted by both models from identical input under the same prompt and
   schema version, and an agreement rate is reported per attribute dimension and per beat function
   position, each with its observation count.
2. **Given** disagreements between the two models, **When** results are reported, **Then** the
   disagreeing cases are enumerated and their direction shown — never reduced to a single averaged
   score, and never reconciled by preferring one model's label.
3. **Given** a media class only one model can accept, **When** results are reported, **Then** the
   report states that the measured agreement does not extend to it, and no agreement figure is
   asserted for that class.
4. **Given** the two paths are configured to the same model, **When** the comparison is requested,
   **Then** it reports that no cross-model comparison applies rather than producing a trivial 100%
   agreement figure.
5. **Given** a calibration run, **When** its extractions are produced, **Then** they are marked as
   calibration and excluded from the analytical corpus, so no item is double-counted in any
   distribution.
6. **Given** a change of model, prompt version, or schema version on either path, **When** an
   agreement figure is read, **Then** it carries the versions it was measured under, and a figure
   measured under superseded versions is not presented as current.
7. **Given** any extraction, **When** its provenance is read, **Then** it names the model that
   actually served the request — including when provider fallback substituted for the requested
   one — not merely the model that was asked for.

---

### User Story 5 - Re-extract the existing corpus, knowing the bill first (Priority: P3)

The signal rows already collected (**819** as of 2026-08-03, and growing) hold prose flow, unusable
by the new queries. An operator wants them re-extracted under the new schema, and wants to know what
that costs before committing. At today's corpus size the answer is "not much"; at ten times the size
it will not be.

**Why this priority**: The existing corpus is valuable but finite and static — it can be backfilled
at any point after the forward path works. Shipping the forward path first means new content starts
accruing structure immediately while the backfill is prepared.

**Independent Test**: Run the backfill in dry-run over the full corpus and confirm it reports item
count and projected spend without issuing a single model call; then run it for real over a small
subset and confirm actual spend lands within the stated tolerance of the projection.

**Acceptance Scenarios**:

1. **Given** the existing corpus, **When** backfill is invoked in dry-run mode, **Then** it reports
   the number of items in scope and the projected model spend, and makes no model calls.
2. **Given** a dry-run projection derived from measured per-item usage, **When** it is presented,
   **Then** it states that basis and the sample it was calibrated on; **and given** no measured
   baseline exists for a class of item, **Then** the projection says so rather than presenting a
   guess as a measurement.
3. **Given** a backfill run, **When** it needs media for an item, **Then** it sources it from the
   already-retained copy and contacts no social platform.
4. **Given** an item whose retained media is no longer retrievable, **When** backfill reaches it,
   **Then** it is recorded with a classified reason and counted — never silently skipped.
5. **Given** a completed backfill, **When** it is run again at the same version, **Then** items
   already extracted at that version are not re-extracted and are not charged for again.
6. **Given** a projection above the configured spend threshold, **When** a real run is requested,
   **Then** it does not proceed without explicit confirmation.
7. **Given** any prior extraction, **When** backfill writes a new one, **Then** the prior extraction
   is unchanged and remains valid under the version it recorded.

---

### Edge Cases

- **A long transcript exhausts the output budget before the beats are written.** Verbatim
  transcripts are unbounded and the new schema adds beats and attributes to the same response. A
  response truncated for this reason must be classified as truncated and rejected — never stored
  with a complete subtitle and a half-written beat array. The budget must be sized for both, and
  transcript length must not be reduced to make room (see FR-011).
- **The model returns a function that is not in the vocabulary.** Rejected as invalid and retried
  with the error, never coerced onto the residual function — coercion would hide vocabulary drift
  behind a plausible-looking value.
- **Beat positions arrive duplicated, non-contiguous, or out of order.** Invalid.
- **Zero beats.** Invalid. "No discernible structure" is expressed as one beat carrying the residual
  function, so the absence is recorded rather than inferred from an empty list.
- **A video with no speech and no on-screen text.** The subtitle is absent by nature, and must be
  distinguishable from "transcription was attempted and returned nothing" and from "extraction
  failed".
- **A mixed carousel containing both video and image files.** Today these are routed to the image
  path regardless of the `content_type` label
  ([analyze.py:350-352](service/roach/analyze.py#L350-L352)). Provenance must record the path
  actually taken, not the label.
- **Provider fallback serves a different model than requested.** Recorded as served, not as
  requested.
- **The vocabulary version changes mid-backfill.** Each extraction records the version it was
  assigned under; a run must not silently mix versions without that being visible per row.
- **An item whose original extraction failed** (18 of 691 items, 2.60% at audit) has no prose to
  preserve and must be eligible for backfill on the same terms as a successful one.
- **The same item is re-extracted at the same version with unchanged input.** Served from cache; no
  model call, no charge.
- **The residual function's share rises over time.** This is a vocabulary gap becoming visible, and
  must be reportable as such rather than absorbed as noise.
- **Neither model accepts the other's media, so the calibration overlap is empty.** The comparison
  must report that it could not be run. It must not silently fall back to observational
  distributions presented as a controlled result.
- **Both paths are configured to the same model.** `IMAGE_MODEL` defaults to `MODEL` when unset
  ([analyze.py:26](service/roach/analyze.py#L26)), so this is the default configuration, not an
  exotic case. The comparison must report "no cross-model comparison applies", not 100% agreement.
- **A calibration extraction leaks into the corpus.** Every item in the calibration sample is
  extracted twice; if both copies are stored as ordinary extractions, every distribution over that
  sample is double-weighted. Calibration output must be marked and excluded.
- **Provider fallback substitutes a model mid-calibration.** The comparison is then not between the
  two models it claims to compare. The served model is recorded per extraction, and a calibration
  whose served models do not match the intended pair must be reported as such rather than counted.
- **The attribute vocabulary lands and triggers the second backfill.** Items already extracted at
  the flow-only version are re-extracted at the new version; the prior extractions remain valid and
  unchanged, and the two versions must be distinguishable per row.

## Requirements *(mandatory)*

### Functional Requirements — structured flow

- **FR-001**: Extraction MUST produce content flow as an ordered sequence of beats. Each beat MUST
  carry a position, a function drawn from a closed vocabulary, and a free-text description.
- **FR-002**: Beat positions MUST be contiguous, MUST start at the first beat, MUST be unique
  within an extraction, and MUST reflect the order in which the beats occur in the content.
- **FR-003**: A successful extraction MUST contain at least one beat. Content whose structure the
  model cannot identify MUST be recorded as a single beat carrying an explicit residual function,
  not as an empty sequence.
- **FR-004**: The beat-function vocabulary MUST be a closed, versioned list held as data rather
  than embedded in prompt text or code. Its initial membership MUST include the concepts the
  current prompts already name — hook, setup, main point, call to action — and an explicit residual
  member.
- **FR-005**: The proportion of beats assigned the residual function MUST be reportable, so that a
  vocabulary gap is visible as a rising share rather than absorbed silently.
- **FR-006**: A beat function outside the recorded vocabulary version MUST be rejected as invalid.
  It MUST NOT be coerced onto the residual member or onto any other member.
- **FR-007**: Stored beats MUST be filterable and countable as structured data, with no text
  parsing anywhere in the query path.

### Functional Requirements — subtitle preservation

- **FR-008**: The verbatim transcript MUST be retained exactly as the model produced it — not
  segmented, aligned to beats, summarised, normalised, or truncated.
- **FR-009**: Subtitles already stored MUST NOT be modified by this feature, including by backfill.
- **FR-010**: An absent subtitle MUST be distinguishable between at least: the content carries no
  audio and no on-screen text; transcription was attempted and produced nothing; and extraction did
  not complete. An empty value alone MUST NOT be the sole record of any of these.
- **FR-011**: The output budget MUST accommodate a full transcript together with the structured
  fields. Transcript completeness MUST NOT be traded away to make room for structure; where both
  cannot fit, the response is truncated and is handled under FR-017.

### Functional Requirements — derived attributes and confidence

- **FR-012**: Extraction MUST emit at most one value per attribute dimension, drawn from the
  controlled vocabulary defined externally (see Dependencies).
- **FR-013**: Confidence MUST be reported per attribute, never per extraction.
- **FR-014**: Consumers MUST be able to exclude attribute assignments below a minimum confidence
  while retaining the rest of the extraction, and any aggregate over assignments MUST report the
  number of assignments it rests on.
- **FR-015**: Every attribute assignment MUST record the vocabulary version under which it was
  assigned. Where the vocabulary publishes no dimensions, extraction MUST still produce flow,
  subtitle, and summary, recording zero attributes and inventing no dimension.

### Functional Requirements — validation, retry, quarantine

- **FR-016**: Model output MUST be validated against the declared schema by this system before
  storage. Provider-side schema enforcement MUST NOT be the only check, and no code path that
  stores unvalidated output may exist.
- **FR-017**: A response whose completion was cut short MUST be treated as invalid. Partial output
  MUST NOT be salvaged by pattern extraction from raw text.
- **FR-018**: Invalid output MUST be retried exactly once, and the retry MUST carry the specific
  validation error from the failed attempt back to the model.
- **FR-019**: Output that fails validation twice MUST be quarantined: retained with its raw text,
  the validation errors from both attempts, and full version metadata, and MUST NOT be written to
  the extraction store.
- **FR-020**: Quarantined extractions MUST be countable, broken down by model, prompt version,
  schema version, and failure kind.
- **FR-021**: Extraction outcomes MUST be classified — at minimum: schema-invalid, truncated, empty
  content, provider error, rate limited, media unavailable, unsupported media. A failure MUST NOT
  be recorded only as a generic error string.
- **FR-022**: A failed or quarantined extraction MUST NOT prevent the item's media from being
  delivered or its metrics from being recorded.

### Functional Requirements — two-model parity and provenance

- **FR-023**: The video and image paths MUST emit an identical output schema. The only permitted
  difference is subtitle content, handled under FR-010.
- **FR-024**: Every extraction MUST record the model that actually served it, including where
  provider fallback substituted a different one, and the model path taken — determined by the media
  actually analysed, not by the item's content-type label.
- **FR-025**: Beat-function and attribute distributions MUST be reportable per model path, each
  with its observation count.
- **FR-026**: A systematic difference between the two paths MUST be surfaced as a finding with its
  sample size. It MUST NOT be normalised away, averaged into a combined figure, or silently
  reconciled.
- **FR-027**: Model agreement MUST be established by controlled comparison: a fixed sample of items
  extracted by **both** models from identical input, under the same prompt and schema version, with
  an agreement rate reported per attribute dimension and per beat-function position, each carrying
  its observation count.
- **FR-027a**: The controlled comparison MUST be limited to media that both models accept.
  Where a media class can only be served by one model, the report MUST state that the measured
  agreement does not extend to that class, and MUST NOT assert an agreement figure for it.
- **FR-027b**: The calibration sample MUST be fixed and re-runnable, so that a change of model,
  prompt version, or schema version can be re-measured against the same items. Every recorded
  agreement figure MUST carry the model identities and versions it was measured under, and a figure
  measured under superseded versions MUST NOT be presented as current.
- **FR-027c**: Calibration extractions MUST be marked as such and excluded from the analytical
  corpus, so that no item is double-counted in any distribution, count, or aggregate.
- **FR-027d**: Where both paths are configured to the same model, the comparison MUST report that
  no cross-model comparison applies, rather than reporting the resulting trivial agreement as a
  finding.
- **FR-027e**: The controlled comparison MUST be a costed batch operation on the same terms as
  backfill — dry-run projection before execution (FR-032, FR-033), measured cost recorded
  (FR-038), threshold confirmation (FR-037) — and its sample size MUST meet a configured minimum
  before any agreement figure is reported.
- **FR-027f**: Observational per-path distributions (FR-025, FR-026) remain available and remain a
  finding in their own right, but MUST NOT be described as evidence of a model difference except
  where the controlled comparison supports it.

### Functional Requirements — versioning

- **FR-028**: Every extraction MUST record the prompt version, schema version, vocabulary
  version(s), and model used.
- **FR-029**: Introducing a new prompt, schema, or vocabulary version MUST NOT alter or invalidate
  previously stored extractions. Prior extractions remain valid under the versions they recorded.
- **FR-030**: An extraction's stored values MUST be interpretable against the vocabulary version it
  recorded, not only against the current one.

### Functional Requirements — backfill

- **FR-031**: Re-extraction of already-collected items MUST be supported as a batch operation over
  a selectable set, including items whose original extraction failed.
- **FR-032**: Backfill MUST support a dry-run that reports the item count in scope and projected
  model spend without making a single model call.
- **FR-033**: The projection MUST be derived from measured per-item usage where a measured baseline
  exists, and MUST state that basis and its sample. Where no measured baseline exists for a class of
  item, the projection MUST say so rather than presenting an estimate in the same form as a
  measurement.
- **FR-034**: Backfill MUST source media from already-retained copies and MUST NOT contact any
  social platform.
- **FR-035**: An item whose retained media is no longer retrievable MUST be recorded with a
  classified reason and counted, never silently skipped.
- **FR-036**: Backfill MUST be idempotent at a given version: re-running MUST NOT re-extract or
  re-charge for items already extracted at that version.
- **FR-037**: A run whose projection exceeds a configured spend threshold MUST require explicit
  confirmation before proceeding.
- **FR-037a**: A configured **monthly ceiling on extraction spend** MUST be enforced, not merely
  reported: before any costed run begins, the month-to-date total of measured extraction cost plus
  the run's projection MUST be compared against the ceiling, and the run MUST halt when it would be
  exceeded. Enforcement is a hard stop, not a warning. This satisfies Constitution XI for the spend
  this feature introduces.
- **FR-037b**: The ceiling in FR-037a MUST be labelled as covering **extraction spend only**
  wherever it is surfaced. It MUST NOT be presented as a system-wide budget while generation spend
  (songbird) sits outside its count — a cap that silently omits most of the system's model spend
  would report "within budget" on incomplete evidence, which Constitution VI forbids.

### Functional Requirements — cost and continuity

- **FR-038**: Per-extraction cost MUST be recorded from the provider's reported usage, not
  estimated from a price list.
- **FR-039**: Unchanged input MUST NOT be re-extracted at the same version; extraction MUST be
  cached by content identity.
- **FR-040**: The marginal cost per extraction at expected steady-state volume MUST be stated
  before this feature ships.
- **FR-041**: Reviewer-facing deliverables that today receive flow as readable text MUST continue
  to receive a readable rendering, so the existing review workflow is not degraded by the move to
  structured storage.
- **FR-042**: Model selection for each path MUST remain configuration per call site, so a path can
  be re-pointed without a code change.
- **FR-043**: This feature MUST ship ahead of the external attribute vocabulary. Flow structure,
  validation, quarantine, and versioning MUST be complete and usable while the vocabulary publishes
  no dimensions, with attribute behaviour built and inert **as specified in FR-015**.
- **FR-043a**: This feature MUST establish the vocabulary storage and versioning mechanism — closed
  membership, versioned, held as data, assignments recording the version they were made under — in
  a form the external attribute vocabulary can populate without introducing a second mechanism.
  The beat-function vocabulary (FR-004) is its first occupant.
- **FR-043b**: A second backfill pass, run when the attribute vocabulary lands, is an accepted
  consequence of FR-043 and MUST be supported by the same backfill machinery on the same terms
  (FR-031 – FR-037). Its projected cost MUST be statable at the time this feature ships, so the
  decision to defer attributes is made against a known second bill rather than an unknown one.

### Key Entities

- **Extraction** — one model's structured reading of one content item at one point in time. Holds
  the verbatim subtitle, the summary, the ordered beats, the attribute assignments, the outcome
  classification, the recorded cost, and full version and model provenance.
- **Flow beat** — an ordered element of an extraction: position, function, description. Belongs to
  exactly one extraction.
- **Beat-function vocabulary** — the versioned closed list of structural roles a beat may carry,
  including an explicit residual member. Held as data.
- **Attribute assignment** — one dimension, one value, one confidence, one vocabulary version.
  At most one per dimension per extraction.
- **Quarantine record** — model output that failed validation twice: raw text, both sets of
  validation errors, version and model provenance, failure classification. Countable; never an
  extraction.
- **Backfill batch** — a selected set of items to re-extract, its dry-run projection, its
  confirmation state, and its per-item outcomes including classified skips. **Persisted**, not
  merely reported at run time: the projection must survive to be compared against actual spend
  (SC-008), and the per-item outcomes must survive to be counted (SC-010). A controlled comparison
  is the same entity under a different selection, since it is a costed batch on identical terms
  (FR-027e).
- **Parity report** — beat-function and attribute distributions per model path with observation
  counts, and the stated confound. Observational; does not on its own attribute divergence to the
  model.
- **Calibration sample** — a fixed, re-runnable set of items that both models accept, held stable
  so that agreement can be re-measured across model and version changes.
- **Agreement measurement** — the result of one controlled comparison: per-dimension and
  per-beat-position agreement rates, the enumerated disagreements, the sample size, the media
  classes covered and excluded, and the model identities and versions it was measured under.
  Its extractions are marked as calibration and never counted in the analytical corpus.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A question of the form "how many items of this type open with a hook, and how did
  they perform" is answerable by a single structured query returning counts, with zero manual
  reading and no text pattern matching in the query path.
- **SC-002**: 100% of stored extractions have every beat function present in the vocabulary version
  that extraction recorded, verified across the entire store.
- **SC-003**: 100% of stored extractions have contiguous, unique, ordered beat positions and at
  least one beat.
- **SC-004**: An automated comparison of the field set produced by the video path and the image
  path finds no structural difference other than subtitle content.
- **SC-005**: Re-validating the entire extraction store against each row's recorded schema version
  produces zero failures.
- **SC-006**: Zero extractions in the store originate from a response that was cut short or
  pattern-salvaged, verified by the absence of any code path that can produce one.
- **SC-007**: Quarantine counts are retrievable at any time, broken down by model, prompt version,
  schema version, and failure kind, and every quarantine record can be inspected in full.
- **SC-008**: A backfill dry-run over the full corpus reports item count and projected spend having
  made zero model calls; a subsequent real run's actual spend falls within ±25% of the projection,
  or the divergence is reported with its cause.
- **SC-009**: 100% of subtitles stored before this feature are byte-identical after it ships and
  after any backfill run.
- **SC-010**: Every backfill item resolves to exactly one outcome — extracted, cached, quarantined,
  or classified-skip. An unclassified outcome is a defect, and the count of unclassified outcomes
  is zero.
- **SC-011**: The share of beats carrying the residual function is reportable at any time, so
  vocabulary gaps are observable as a trend.
- **SC-012**: An analyst can apply a minimum attribute confidence and see both the surviving
  assignments and how many remain.
- **SC-013**: Beat-function and attribute distributions per model path are available with sample
  sizes, and any reported divergence carries its observation count and its stated confound.
- **SC-014**: Every stored extraction names the model that served it and the versions it was
  produced under; the count with missing provenance is zero.
- **SC-015**: Extraction failure never reduces collection: **no code path exists in which an
  extraction outcome — failed, quarantined, or deferred — short-circuits media delivery or metric
  recording**, verified by extracting an item whose extraction quarantines and confirming its media
  and metrics still land. Stated structurally rather than as a rate comparison against a pre-feature
  baseline, for the same reason FR-027 rejects observational comparison for model agreement:
  delivery rate moves for collection reasons that have nothing to do with extraction — a profile
  going private, a platform markup change, a short listing — so a changed rate could never be
  attributed to this feature. A confounded comparison would be weaker evidence than the invariant.
- **SC-016**: A controlled agreement rate is available per attribute dimension and per beat-function
  position, measured on a fixed sample both models extracted from identical input, carrying its
  sample size and the model identities and versions it was measured under.
- **SC-017**: The media classes the agreement measurement does not cover are named in the same
  report, and the count of agreement figures asserted for uncovered classes is zero.
- **SC-018**: Calibration extractions are excluded from every distribution, count, and aggregate
  over the analytical corpus — verifiable by confirming that no item in the calibration sample
  contributes more than one extraction to any such figure.
- **SC-019**: No costed run proceeds once the month-to-date extraction spend plus its projection
  would exceed the configured ceiling; the count of runs that exceeded it is zero.

## Assumptions

- **Media for backfill comes from the retained Drive copies**, referenced by
  `harvested_items.drive_file_id`. Local files are deleted after upload
  ([social_harvest.py:928](service/prefect/flows/common/social_harvest.py#L928)), and re-collecting
  from the platform would be both collection expansion and a politeness violation. Items whose
  Drive copy is gone are classified skips, not re-scrapes.
- **This feature is additive.** Existing prose fields and the reviewer-facing sheets keep their
  current shape and content; structured output is stored alongside rather than replacing them.
  Nothing in this feature overwrites `subtitle`, `content_flow`, or `summary` in place.
- **Confidence is a small ordered scale, not a continuous score.** Self-reported model confidence
  at fine numeric granularity implies precision the model does not have; an ordered scale is
  filterable by minimum level without asserting false precision. Revisit if a calibration study
  later justifies a continuous score.
- **The vocabularies live in the relational store**, not in `hashmap.py` — they are versioned,
  queryable, migratable state rather than static mapping data.
- **Extraction remains stateless on the Roach side**: it returns validated structured output;
  persistence, versioning, and quarantine storage sit on the orchestration side, matching the
  existing division of responsibility.
- **Corpus size for sizing** — ⚠️ *corrected by Phase 0 (research.md R1)*. The 656 signal rows /
  691 items cited above came from `docs/AUDIT.md`'s 2026-07-31 snapshot. Measured live 2026-08-03:
  **819 signal rows and 855 harvested items** (video 364, carousel 323, image 130, story 2; 18
  items with a failed original analysis). The corpus grows monthly, so backfill scope is defined as
  "everything present when the run starts", never as a fixed count. Steady-state new volume follows
  the existing monthly harvest cadence.
- **Both model paths already receive strict provider-side schema enforcement**; this feature adds
  the client-side validation that neither path currently performs, rather than introducing schema
  enforcement for the first time.
- **The residual beat function exists from vocabulary version 1**, so that "no discernible
  structure" is expressible without a vocabulary change.
- **The two paths are only actually two models when configured to be.** `IMAGE_MODEL` falls back to
  `MODEL` ([analyze.py:26](service/roach/analyze.py#L26)); the deployed configuration sets them
  apart (`xiaomi/mimo-v2.5` for video, `google/gemini-2.5-flash-lite` for images). The controlled
  comparison is therefore conditional on configuration, not unconditional.
- **The calibration overlap is expected to be image and carousel media**, since the image model
  cannot accept video. This makes the measurement one-directional: it can say whether the two
  models agree on images, and cannot say anything about video-only content. That limit is reported,
  not worked around — closing it would mean sending video to a model that cannot take it.
- **A second backfill is planned, not accidental.** Shipping ahead of the attribute vocabulary means
  the corpus is re-extracted twice. At the current corpus size that is cheap; the decision rests on
  that being true now and the corpus only growing, which is itself the argument for getting the
  forward path running sooner.

## Dependencies

- **Attribute vocabulary definition (S-05)** — the dimensions, their permitted values, and their
  semantics. This feature consumes that vocabulary as versioned data and defines the mechanism for
  applying and versioning assignments; it does not define the vocabulary's content. See FR-043.
- **Existing harvest pipeline** — item collection, media delivery to Drive, and the signal store
  this feature extends.
- **Provider usage accounting** — the resolved cost already returned on every call
  ([analyze.py:132](service/roach/analyze.py#L132)) is the basis for FR-033 and FR-038.
- **Relational datastore** as the system of record for extractions, vocabularies, and quarantine.

## Out of Scope

- **Defining the attribute vocabulary** — dimensions and values are S-05.
- **Collection changes** — deeper listings, new profiles, additional harvest schedules (S-02).
- **Comment text analysis** and any extraction target beyond the item's own media.
- **Semantic or vector indexing** of subtitle or summary. This feature makes structure countable;
  it does not make prose searchable by meaning.
- **Downstream consumption of the new structure** — Songbird retrieval changes, effect estimation,
  and cohort analysis consume this data but are separate features.
- **Changing the transcript itself** — no diarisation, no timestamp alignment, no speaker labels.
- **Re-collecting media** for items whose retained copy is gone.
