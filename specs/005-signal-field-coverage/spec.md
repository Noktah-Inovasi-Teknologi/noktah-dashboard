# Feature Specification: Signal Field Coverage

**Feature Directory**: `specs/005-signal-field-coverage`

**Feature Branch**: `feat/social-harvest-songbird` (current; no branch hook configured)

**Created**: 2026-08-02

**Status**: Draft

**Input**: User description: "Read AUDIT.md §3 (Views, shares, comment text) and service/roach/collect.py. Expand what Roach captures per post so that Instagram non-video content and account-level follower data are not blind spots."

## Context: measured state at specification time

The brief's premises were checked against the code and the audit before this spec was written. Two
of the four targets are **already implemented in the collection layer** and one of those is
**dropped before it reaches storage**. This changes the shape of the work and is recorded here so
the plan does not rebuild what exists.

| Brief's premise | Verified state | Consequence for scope |
|---|---|---|
| "No share counts are captured on either platform" | **Partly stale.** Roach already reads TikTok `shareCount` ([collect.py:545](../../service/roach/collect.py#L545)) and yt-dlp `repost_count` ([collect.py:450](../../service/roach/collect.py#L450)), and already emits an explicit `shares: None` for Instagram ([collect.py:949](../../service/roach/collect.py#L949)). | The gap is **downstream, not upstream**: `shares` has no column in `harvested_signals`, no column in `ACCOUNT_HEADER` ([social_harvest.py:99-104](../../service/prefect/flows/common/social_harvest.py#L99-L104)), and is not a parameter of `social.signal.record` ([social_tasks.py:240-256](../../service/prefect/tasks/social_tasks.py#L240-L256)). Collected, then discarded. |
| "Instagram profile public_metadata is always empty" and "collect.py:808-812 returns {} for Instagram" | **Stale.** `_instagram_profile_info` plus a GraphQL fallback landed 2026-08-01 ([collect.py:598](../../service/roach/collect.py#L598), [:671](../../service/roach/collect.py#L671)) and populate `public_metadata` ([collect.py:987-993](../../service/roach/collect.py#L987-L993)). Line numbers in the brief refer to the pre-2026-08-01 file. | Follower capture and its **time series already exist**: `account_follower_observations` (append-only, one row per observation) and `social.account.record-followers` ([social_tasks.py:442](../../service/prefect/tasks/social_tasks.py#L442)) shipped with feature 004. Remaining work is **verification and gap-closure**, not construction. |
| "of 656 harvested rows, 361 (Instagram carousel and image) have likes only" | **Consistent with the code.** Instagram non-video items take `comments` from the gallery-dl post payload ([collect.py:944](../../service/roach/collect.py#L944)), which does not carry it; the only enrichment path (`_instagram_clip_stats`) is Reels-keyed and cannot match a feed post or carousel. | Genuinely open. Investigation-gated (see User Story 2). |
| "No comment text is captured anywhere" | **Confirmed.** No path in `/list`, `/download`, or `/analyze` touches a comment thread. | Genuinely open. Feasibility assessment only (see User Story 5). |

The single cross-cutting defect behind all four targets: **absence and non-existence are stored
identically.** A `views` of `NULL` on an Instagram carousel means "Instagram does not publish this
number"; a `views` of `NULL` on an Instagram Reel means "the enrichment call failed this run". Both
are `NULL`. No consumer can tell them apart, so no consumer can tell a collection regression from a
platform limit.

## Clarifications

### Session 2026-08-02

- Q: Where do field availability determinations live? → A: A version-controlled declarative file in the repository is the source of truth, reconciled into a queryable store by a sync task (repo file + DB mirror).
- Q: How is per-observation capture outcome stored? → A: A dedicated append-only table, one row per observation per supplementary capture attempt, carrying status and classified reason.
- Q: What is the canonical availability status vocabulary? → A: Five values — `available`, `unavailable_platform_limit`, `not_collected_by_decision`, `inconclusive`, `undetermined`.
- Q: How does the share-count column reach existing reviewer sheet tabs? → A: Append at the end, backfill the header cell on existing tabs once, and read by header name rather than position — one canonical layout.
- Q: What probe budget governs the Instagram investigations? → A: Offline evidence first; only if unresolved, at most 10 spaced live probes against one designated non-client account, with the count recorded in the determination.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Tell a platform limit apart from a collection failure (Priority: P1)

An analyst looking at a harvested post sees an empty engagement field. They need to know, without
reading collector source code, whether that emptiness is permanent (the platform never publishes
it for this content type) or incidental (this run's supplementary call failed and a later run may
fill it). Today both look like `NULL`.

**Why this priority**: It is the enabling story. Every other story in this feature either produces
a new field that will sometimes be empty, or concludes that a field cannot be obtained — and both
outcomes are only meaningful if emptiness is legible. It also directly satisfies the brief's
success criterion "per-platform, per-content-type field availability is documented as data", and
it delivers value even if every other story is dropped: the existing 656 rows immediately become
interpretable.

**Independent Test**: Query the harvested corpus for every field that is empty, join to the
availability record, and confirm every empty value resolves to exactly one reason. Verified by
checking that Instagram carousel `views` reports "not exposed by platform" while an Instagram Reel
whose enrichment failed reports "capture attempted, failed".

**Acceptance Scenarios**:

1. **Given** an Instagram carousel row with no `views`, **When** a consumer asks why, **Then** the
   system reports that the platform does not expose plays for that platform/content-type pair, and
   the row is not counted as a collection failure.
2. **Given** an Instagram Reel row with no `views` because the supplementary stats call failed,
   **When** a consumer asks why, **Then** the system reports an attempted-but-unsuccessful capture
   with a classified reason, distinct from the platform-limit answer.
3. **Given** a consumer that needs to know coverage before running an analysis, **When** it queries
   the availability record for (platform, content type, field), **Then** it receives exactly one of
   the five canonical statuses defined in FR-002, with the reason and the date the determination
   was made.
4. **Given** a field determined unavailable, **When** a later platform change makes it available,
   **Then** the availability record can be updated without altering or reinterpreting any
   previously stored observation.

---

### User Story 2 - Comment counts on Instagram non-video content (Priority: P2)

Instagram carousels and single images are 361 of 656 harvested rows and carry likes only. Comment
count is the one remaining engagement dimension Instagram plausibly exposes for them. A researcher
needs to know whether it can be obtained from surfaces already in use — and if not, needs that
answer recorded once so it is not re-investigated every quarter.

**Why this priority**: Highest-value open gap by row count (55% of the corpus), and the brief's
stated first priority. Ranked below Story 1 only because its outcome may legitimately be "not
obtainable", and that outcome is only recordable once Story 1 exists.

**Independent Test**: Run the investigation against a real account and record the determination.
If a qualifying path is found, verify comment counts appear on carousel and image rows with no
increase in per-account request count beyond the stated budget. If not, verify the availability
record carries the negative determination with its reason.

**Acceptance Scenarios**:

1. **Given** the existing authenticated surfaces already in use, **When** the investigation asks
   whether any of them returns a comment count keyed to a feed post or carousel, **Then** the
   determination is recorded as data with the evidence behind it.
2. **Given** a qualifying path is found, **When** an Instagram carousel is harvested, **Then** its
   comment count is stored and the run's total request count per account stays within the declared
   budget.
3. **Given** no qualifying path exists within current surfaces and pacing, **When** the
   investigation concludes, **Then** the field is recorded as unavailable with its reason and **no
   collection code is added** — an approach that would raise request volume or add an
   authenticated surface is explicitly not adopted.
4. **Given** the investigation is inconclusive, **When** it concludes, **Then** that is recorded as
   inconclusive with what was tried, never as a platform limit.

---

### User Story 3 - Share counts reach the analyst (Priority: P3)

TikTok share counts are already collected on every TikTok post and thrown away at the storage
boundary. An analyst querying the corpus cannot see a single one.

**Why this priority**: Lowest-cost item in the feature — zero additional network requests, since
the number is already inside payloads that have been fetched, parsed, and discarded. Ranked third
only because it affects the smaller platform (TikTok is ~3% of the corpus by the audit's figures).

**Independent Test**: Harvest a TikTok account and confirm share counts are present in the stored
signals and visible on the reviewer-facing sheet, with no change to the number of requests the run
issues. Confirm Instagram rows carry an explicit platform-limit determination for the same field
rather than an ambiguous blank.

**Acceptance Scenarios**:

1. **Given** a TikTok video whose payload carries a share count, **When** it is harvested, **Then**
   the share count is stored and visible to a consumer querying the corpus.
2. **Given** a TikTok photo post or story, **When** it is harvested, **Then** its share count is
   stored from the same already-fetched payload.
3. **Given** an Instagram post of any content type, **When** a consumer asks for its share count,
   **Then** it receives an explicit platform-limit unavailable, not an empty value.
4. **Given** the change is deployed, **When** the per-account request count for a harvest run is
   compared before and after, **Then** it is unchanged.

---

### User Story 4 - Instagram follower count as a trustworthy series (Priority: P4)

Engagement rate needs a follower denominator. The capture mechanism and the append-only series
exist; what is not established is that they are actually producing rows for Instagram accounts, or
that a missed capture is recorded as missed rather than silently absent.

**Why this priority**: The build is done; this is verification plus gap-closure. It ranks below
the items that add missing data because it may turn out to require no code change at all — but it
cannot be assumed working, because the primary Instagram endpoint has been intermittently failing
since 2026-07-31 and the fallback is recent.

**Independent Test**: Harvest a set of Instagram accounts, then query the follower series and
confirm one observation row per account per run, with any account that produced none carrying a
recorded, classified missed-capture reason.

**Acceptance Scenarios**:

1. **Given** an Instagram account is harvested and its follower count resolves, **When** the run
   completes, **Then** a new timestamped observation exists, appended rather than overwriting any
   prior one.
2. **Given** the same account is harvested again later, **When** the series is queried, **Then**
   both observations are present with their own timestamps, so change over time is derivable.
3. **Given** an account whose follower count could not be resolved by any available route, **When**
   the run completes, **Then** the miss is recorded with a classified reason and the account is
   distinguishable from one never attempted — and no zero or interpolated value is stored.
4. **Given** a consumer computing engagement rate, **When** the relevant follower observation is
   absent, **Then** the rate is reported unavailable rather than computed against a substituted
   denominator.

---

### User Story 5 - A recorded decision on comment text (Priority: P5)

Comment text is the richest untapped source of audience language. Before anyone builds it, the
organisation needs a written, evidence-backed determination of what it would cost in collection
aggressiveness — and, if that cost is disqualifying, a recommendation against it that stops the
question being reopened.

**Why this priority**: Produces a decision, not a capability. Sequenced last because the
investigation's cost estimate is most credible once the per-account request budget established by
the earlier stories is measured rather than assumed.

**Independent Test**: Review the recorded determination and confirm it states the marginal request
volume per account per run, compares it to the current baseline, and issues an explicit
recommendation with reasoning.

**Acceptance Scenarios**:

1. **Given** the assessment is complete, **When** it is read, **Then** it states the expected
   additional requests per post and per account per run, expressed as a multiple of the current
   per-account baseline.
2. **Given** the assessment finds that comment text requires materially more aggressive
   collection, **When** it concludes, **Then** it recommends against implementation and **no
   comment-text collection is built** in this feature.
3. **Given** the assessment concludes, **When** the availability record is queried for comment
   text, **Then** it reports the field as not collected by deliberate decision, with a pointer to
   the determination — distinct from both a platform limit and an untried field.

---

### Edge Cases

- **A field is available for one content type and not another on the same platform.** Instagram
  publishes plays for Reels but not carousels. The availability record is keyed by (platform,
  content type, field); a platform-level answer is not sufficient and must not be recorded.
- **A previously available field stops being returned.** If Instagram's supplementary stats route
  breaks the way the profile endpoint did on 2026-07-31, rows begin arriving with attempted-but-
  failed status. This must surface as a run-level anomaly, not be silently reclassified as a
  platform limit.
- **A field is present but zero.** A post with genuinely zero comments must be stored as zero and
  must never be conflated with an absent value.
- **Historical rows predate the availability record.** The 656 existing rows were collected before
  any capture-outcome was recorded. They must be marked as lacking capture provenance rather than
  assumed successful, so a later coverage statistic is not inflated by rows whose status is
  unknown.
- **A share count exists on TikTok but the reviewer sheet for that account predates the column.**
  Existing per-account tabs carry the old header and are brought forward by a one-time header
  backfill (FR-016a). A tab created between the code deploy and the backfill, or a backfill that
  partially fails, must still be written correctly — which is why reads resolve by header name
  (FR-016b) rather than assuming the migration completed.
- **Re-harvest of an already-captured post.** Capture is effectively once-only for successful items
  (audit §5), so new fields will populate on newly seen posts but not retroactively on the 656
  existing rows. Backfill of existing rows is not assumed and must be stated as a known coverage
  boundary, not silently left as a gap.
- **An account is harvested twice into different destinations.** The corpus contains such pairs;
  follower observations must not double-count an account within a single run.

## Requirements *(mandatory)*

### Functional Requirements

#### Availability as data (Story 1)

- **FR-001**: The system MUST maintain a queryable record of field availability keyed by
  (platform, content type, field), where each entry carries a status, a human-readable reason, and
  the date the determination was made.
- **FR-001a**: The authoritative source of availability determinations MUST be a version-controlled
  declarative file, so that each determination and its supporting evidence is reviewable as a
  change rather than as opaque stored state.
- **FR-001b**: That source MUST be reconciled into the queryable store by a repeatable sync, such
  that a determination can be joined against stored observations without consulting the file.
  Reconciliation MUST be idempotent and MUST NOT modify any observation.
- **FR-002**: The availability status vocabulary MUST be exactly these five values:
  | Status | Meaning | Consumer action |
  |---|---|---|
  | `available` | Published by the platform for this content type and collected by the system | Use the value; an empty one is a capture outcome question (FR-003) |
  | `unavailable_platform_limit` | The platform does not publish this field for this content type | Never expect it; exclude from coverage denominators |
  | `not_collected_by_decision` | Obtainable, but deliberately not collected | Revisit only by reversing the recorded decision |
  | `inconclusive` | Investigated without reaching a determination | Treat as unknown; re-investigation may resolve it |
  | `undetermined` | Not yet investigated | Investigate |
- **FR-002a**: The vocabulary MUST be closed — a determination carrying a value outside it MUST be
  rejected rather than stored. Adding a value is a deliberate, versioned vocabulary change.
- **FR-003**: Each stored observation MUST record, per supplementary capture attempted for it,
  whether that capture succeeded, failed, or was not attempted, so that an empty value on a row is
  resolvable to exactly one cause.
- **FR-003a**: Capture outcomes MUST be held as append-only records — one per observation per
  supplementary capture attempt — and MUST NOT be updated in place. A re-harvest that re-attempts a
  capture adds a record; it does not overwrite the earlier attempt.
- **FR-003b**: A capture outcome MUST be recorded for an attempt that succeeded overall but
  returned nothing for a particular observation, distinctly from an attempt that failed outright.
- **FR-004**: A failed capture MUST carry a classified reason drawn from the collection-failure
  classification already required of the system (not found, private, deleted, blocked, parse
  failure, timeout, unexpected structure).
- **FR-005**: The system MUST NOT store a substituted, defaulted, or interpolated value in place of
  an unobtained one. An unavailable metric is reported as unavailable.
- **FR-006**: Observations collected before capture-outcome recording existed MUST be marked as
  lacking capture provenance and MUST NOT be counted as successful captures in any coverage figure.
- **FR-007**: Updating an availability determination MUST NOT alter, reinterpret, or delete any
  previously stored observation.

#### Instagram non-video comment counts (Story 2)

- **FR-008**: The system MUST determine, and record as data per FR-001, whether a comment count for
  Instagram feed posts and carousels is obtainable from the authenticated surfaces already in use.
- **FR-009**: If a qualifying path exists, comment counts for Instagram carousel and image content
  MUST be captured and stored.
- **FR-010**: A qualifying path MUST NOT introduce an authenticated surface not already in use, and
  MUST NOT issue requests that scale per-post; per-account bounded requests in the manner of the
  existing supplementary stats path are the only acceptable shape.
- **FR-011**: If no qualifying path exists, the determination MUST be recorded with its reason and
  **no collection code MUST be added**. An approach that increases collection aggressiveness MUST
  NOT be adopted to close this gap.
- **FR-012**: An inconclusive investigation MUST be recorded with the `inconclusive` status, listing
  what was attempted; it MUST NOT be recorded as `unavailable_platform_limit`.
- **FR-012a**: Any investigation in this feature MUST exhaust offline evidence — payloads already
  captured, the source of the collection libraries already in use, and published documentation —
  before issuing a live request.
- **FR-012b**: If offline evidence is insufficient, live probing MUST be capped at 10 requests per
  investigation, spaced rather than issued in a burst, and directed at a single designated
  non-client account. The probe count and the account category MUST be recorded in the resulting
  determination.
- **FR-012c**: Reaching the probe cap without a determination MUST yield an `inconclusive` record.
  The cap MUST NOT be raised to force a conclusion, and an investigation MUST stop immediately on
  any throttling or challenge response rather than continuing to its remaining budget.

#### Share counts (Story 3)

- **FR-013**: TikTok share counts already parsed during collection MUST be persisted to the signal
  store and MUST be visible on the reviewer-facing delivery surface.
- **FR-014**: Capturing share counts MUST NOT change the number of network requests a harvest run
  issues.
- **FR-015**: Instagram share counts MUST carry the `unavailable_platform_limit` status for every
  Instagram content type.
- **FR-016**: A new reviewer-facing column MUST be added at the end of the existing column set, so
  that every pre-existing column keeps its position.
- **FR-016a**: Existing delivery destinations MUST be brought to the new layout by a one-time,
  idempotent header backfill, leaving exactly one column layout in existence. The backfill MUST
  alter only the header row and MUST NOT touch reviewer-entered values.
- **FR-016b**: Reads of the reviewer-facing surface — including the flag that is synced back — MUST
  resolve columns by header name rather than by fixed position, so a layout change cannot silently
  shift which column is interpreted as which field.

#### Instagram follower series (Story 4)

- **FR-017**: Every harvest run of an Instagram account MUST attempt a follower-count observation
  and MUST append it, timestamped, to the account's observation history — never overwrite a prior
  value.
- **FR-018**: A run that fails to obtain a follower count MUST record the miss against the account
  with a classified reason, distinguishable from an account for which no attempt was made.
- **FR-019**: The follower history MUST be queryable per account over time such that change between
  observations is derivable, without assuming observations are evenly spaced.
- **FR-020**: A follower count MUST NOT be stored as zero, carried forward, or interpolated when
  unobserved.

#### Comment text (Story 5)

- **FR-021**: The system MUST produce a recorded feasibility determination for comment text stating
  the expected marginal requests per post and per account per harvest run, expressed relative to
  the measured current per-account baseline.
- **FR-022**: If the determination finds that comment text requires materially more aggressive
  collection, it MUST recommend against implementation, and comment-text collection MUST NOT be
  built in this feature.
- **FR-023**: The comment-text determination MUST be recorded in the availability record with the
  `not_collected_by_decision` status, distinct from `unavailable_platform_limit`.

#### Cross-cutting (all stories)

- **FR-024**: Before any collection-affecting change is implemented, its expected marginal
  collection volume MUST be stated — as additional requests per account per run and as a percentage
  of the measured current baseline — and MUST be reported to the requester for acceptance.
- **FR-025**: No change in this feature may increase request rate, add an authenticated surface not
  already in use, or introduce any identity-rotation or throttle-evasion behaviour. This applies to
  investigation activity (FR-012a–FR-012c) as well as to production collection.
- **FR-026**: All new fields MUST be additive to existing storage. No existing column may be
  dropped, retyped, or have its meaning changed.
- **FR-027**: Every new field MUST be distinguishable by consumers as an observed public count, not
  merged into a field carrying values of a different provenance.

### Key Entities

- **Field Availability Determination**: For one (platform, content type, field) triple — the
  status, the reason, the evidence it rests on, and the date determined. The answer to "can this
  ever be known?" Authored in a version-controlled declarative source and mirrored into the
  queryable store (FR-001a/FR-001b).
- **Capture Outcome**: For one observation and one supplementary capture attempt — whether it was
  attempted, what happened, and when. The answer to "was it known this time?" Append-only; repeated
  attempts accumulate rather than replace (FR-003a).
- **Engagement Observation**: An existing per-content-item record of public counts, extended with
  share count and with its capture outcomes.
- **Follower Observation**: An existing append-only, timestamped account-level follower count.
  Extended in this feature only by the recording of misses.
- **Feasibility Determination**: A recorded assessment for a field that was investigated but not
  built — cost in marginal collection volume, the recommendation, and the reasoning.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: For every combination of platform, content type, and engagement field the system
  handles, an availability determination exists and is queryable. Zero combinations report
  `undetermined` **except those for which no content has ever been observed** — those record
  `undetermined` with their observation count (zero) as the reason. Claiming availability for a
  content type the system has never collected would be an assertion from unobserved data, which
  Constitution VIII forbids; the honest `undetermined` is the correct outcome, not a gap.
- **SC-002**: 100% of empty engagement values on newly harvested content resolve to exactly one
  cause — an `unavailable_platform_limit` determination, a `not_collected_by_decision`
  determination, or a recorded capture outcome carrying a classified failure reason. No empty value
  is ambiguous.
- **SC-003**: Every newly harvested TikTok post carries a share count whenever the platform
  published one, verified across **every TikTok content type the system has actually observed**.
  As of 2026-08-02 that is video only — the photo-post and story paths exist in code but have never
  produced a row, so their share availability stays `undetermined` and is verified the first time
  such a row is collected.
- **SC-004**: The number of network requests a harvest run issues per account is unchanged from the
  pre-feature baseline, except where a documented increase was stated in advance and accepted.
- **SC-005**: Every Instagram account harvested after this feature has at least one timestamped
  follower observation per run, or a recorded classified miss — with no account silently absent
  from both.
- **SC-006**: Follower change between two observations of the same account is derivable by query
  alone, with no interpolation and no assumption of even spacing.
- **SC-007**: Each field the brief named that is not implemented has a recorded determination
  stating why, readable without consulting source code.
- **SC-008**: The existing Instagram carousel and image rows — **377 as measured 2026-08-02**, not
  the 361 the brief cited from a three-week-old audit — are correctly classified: their empty view
  counts identified as a platform limit rather than counted as collection failures. The count is
  re-derived at verification time rather than asserted, since the corpus grows between runs.
- **SC-009**: A stated marginal-collection-volume figure exists for every collection-affecting
  change, produced before implementation.
- **SC-010**: No investigation conducted in this feature issues more than 10 live requests, and
  every investigation records the number it actually issued. Zero investigations continue after a
  throttling or challenge response.

## Assumptions

- **Story ordering deviates from the brief.** The brief ordered comment counts first; this spec
  places the availability record first. The availability record is a hard prerequisite for
  recording the outcome of the comment-count investigation, which may legitimately be negative,
  and it is the brief's own first success criterion. The brief's relative ordering is otherwise
  preserved.
- **Two targets are verification, not construction.** TikTok share collection and Instagram
  follower capture already exist in the collection layer as of 2026-08-01. Scope for these is
  closing the storage gap and verifying production behaviour respectively. If verification finds
  the follower path working and misses already recorded, Story 4 closes with no code change.
- **Availability is recorded at two granularities** (confirmed in Clarifications, FR-001/FR-003),
  because the brief's two required distinctions live at different levels: a per-(platform, content
  type, field) determination answers "can this ever be known", and a per-observation capture
  outcome answers "was it known this time". Recording only the first would leave a failed
  enrichment indistinguishable from a platform limit; only the second would require inferring
  platform limits from failure patterns.
- **Follower-series resolution follows harvest cadence.** Per-account harvests are monthly, so the
  series is monthly-resolution. A dedicated higher-frequency follower poll is a separate feature
  and is not assumed here.
- **Existing rows are not backfilled.** Capture is effectively once-only for successful items, so
  new fields populate on newly harvested content only. Historical rows are marked as lacking the
  new provenance rather than being re-collected. Re-observation of existing posts is a separate
  concern.
- **One reviewer layout, reached by backfill.** Share count is visible to reviewers as a new
  trailing column, and existing per-account tabs are brought to that layout by a one-time header
  backfill (FR-016a) rather than two layouts coexisting permanently. Name-based reads (FR-016b) are
  the safeguard for the window before the backfill completes, not a substitute for it.
- **The comment-text assessment is expected to recommend against.** Comment threads are addressed
  per post and paged, making cost scale with post count rather than account count — structurally
  the shape FR-010 disqualifies. The assessment is still performed on evidence rather than assumed,
  but no implementation capacity is reserved for it.
- **Availability determinations are point-in-time.** Platforms change what they publish; a
  determination records when it was made so a stale one is identifiable.

## Dependencies

- The existing collection service and its already-authenticated Instagram and TikTok sessions. No
  new credential, account, or surface is introduced.
- The existing append-only follower observation store and account roster from feature
  004-relational-spine, which supply the account identity every follower observation is keyed to.
- The existing signal store and reviewer-facing delivery surface, extended additively.
- A designated non-client account on each platform, available for the capped investigation probes
  permitted by FR-012b, so that exploratory requests are never aimed at a client or competitor
  account under active harvest.

## Out of Scope

- Analysis, ranking, or scoring that consumes the new fields — including engagement-rate
  computation, velocity, and any change to how content is selected as exemplary.
- Backfilling or re-observing the 656 existing harvested rows.
- Longitudinal re-observation of engagement metrics on already-captured posts.
- Comment-text collection, storage, or analysis.
- Any increase in collection rate, breadth, or authenticated surface area.
