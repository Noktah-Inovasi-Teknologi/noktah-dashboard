# Feature Specification: Longitudinal Metric Capture

**Feature Branch**: `006-longitudinal-metric-capture`

**Created**: 2026-08-03

**Status**: Draft

**Input**: User description: "Read AUDIT.md §5 (Re-scraping) and service/prefect/flows/common/social_harvest.py:414-433. Enable the same content item to be observed repeatedly over time, so engagement accumulation is measurable rather than frozen at first sighting. Separate metric refresh from full re-harvest; observation history table; velocity derivation; irregular spacing; missed captures recorded; pre-existing content marked as history-less. No increase in model spend. Out of scope: collection expansion (S-02), analysis."

## Problem

A content item's engagement numbers are captured once and never revisited. The first
successful harvest freezes `views` / `likes` / `comments` / `shares` at whatever the
platform reported that day, and every later run skips the item before it reads a
number. Measured: **656 signal rows, one observation each**, all first captured between
2026-07-15 and 2026-07-28.

Because a metric is only ever a single point, the system cannot express *how* engagement
arrived. A post that earned 5,000 likes in six hours and a post that earned 5,000 likes
over three weeks are indistinguishable in the store, though they are entirely different
creative outcomes. Velocity — the rate at which engagement accrues — is the closest
available substitute for the retention and watch-time signals this system cannot obtain,
and it is derivable only from repeated observation.

The skip is not wrong in itself. It correctly prevents re-downloading and re-analyzing
media that has not changed, which is where the expense lives. What is wrong is that the
skip also discards **numbers already in hand**: re-listing a profile returns current
counts for every item it lists, and those counts are thrown away for any item already
known.

## Correction to a premise in the feature request

The request states that "the monthly `harvest-3mo-*` deployments already re-list the same
90-day window every month." **This is no longer accurate**, and the difference changes what
a "free" observation cadence actually reaches.

- Those deployments are now named `harvest-monthly-*` and use a **31-day** window, not 90.
  Eighteen per-client deployments and five per-competitor deployments run monthly on this
  shape.
- However, the day-window is **not** what bounds the platform response. It is a
  client-side filter applied to an already-fetched listing. The listing returns up to
  `list_depth` (default 30) most recent items per profile, and the window filter then
  discards the ones outside 31 days — **after** their current counts have already been
  received.

The practical consequence, which the requirements below are written against:

> Free re-observation covers **every item present in a profile's listing response**, which
> is the account's most recent ~30 items — not a 31-day window, and not a 90-day window.
> An item is re-observable for exactly as long as it remains within its account's listing
> depth. On a high-frequency account that residency is short; on a low-frequency account it
> may last many months.

This is a real bound on the feature's reach, not a defect to route around. It means a
series ends for a knowable reason, and that reason must be recorded rather than left to
look like a capture failure (FR-018). Extending residency by listing deeper is collection
expansion and is **out of scope** here (S-02).

## Clarifications

### Session 2026-08-03

- Q: Where is derived velocity surfaced for a reader? → A: Datastore only — queryable in SQL, no spreadsheet changes
- Q: What triggers velocity derivation? → A: A separate scheduled process that derives over the store after harvests run
- Q: Is "lacks observation history" a stored flag or derived? → A: Derived from observation records; legacy rows seeded as one observation carrying legacy provenance
- Q: What is the minimum interval that yields a rate? → A: 24 hours; rates expressed per day, sub-daily pairs recorded as too short
- Q: What reference point defines plateau and acceleration? → A: The item's own intervals — plateau ≤10% of its highest interval rate, acceleration ≥3× the plateau rate

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Engagement stops being frozen at first sighting (Priority: P1)

An analyst looks at a post harvested two months ago. Today its stored numbers are the
numbers the platform reported today, and the numbers it reported at each earlier
observation are still available beside them. Nothing was re-downloaded and no model was
called to make that true.

**Why this priority**: Every other capability in this feature is arithmetic over a series
that does not yet exist. Until a second observation can be stored, velocity is impossible
by construction. This story alone also fixes a standing data-quality problem — stored
engagement currently drifts further from reality every day a post stays live.

**Independent Test**: Run an existing monthly harvest deployment twice against a profile
whose posts are already fully harvested. Confirm the second run performs no media
download and no content analysis, yet records a new observation for each already-known
item that appeared in the listing, with values reflecting the second run's counts.

**Acceptance Scenarios**:

1. **Given** a content item whose prior harvest completed successfully, **When** a later
   run lists the profile and that item appears in the listing, **Then** a new timestamped
   observation is recorded from the listing counts, **And** no media download occurs,
   **And** no content analysis occurs.
2. **Given** the same item, **When** the later run completes, **Then** the item's
   latest-value record reflects the newest observation, **And** the earlier observation
   remains retrievable unchanged.
3. **Given** a content item never harvested before, **When** a run encounters it, **Then**
   it is harvested in full exactly as today, **And** its first observation is recorded.
4. **Given** an item appearing in the listing response but excluded by the run's day-window
   filter, **When** the run processes the profile, **Then** its metrics are still observed,
   **And** it is still not downloaded or analyzed.
5. **Given** a run that observes an item, **When** the observation is written, **Then** the
   run that produced it is identifiable from the observation record.

---

### User Story 2 - Velocity is derived from unevenly spaced observations (Priority: P2)

An analyst asks which posts accumulated engagement fastest, and which kept accumulating
after their first week rather than dying. The answer is read from stored values, not
recomputed by whoever asks, and it is not distorted by the fact that observations arrive
at ragged intervals.

**Why this priority**: This is the analytical payload the feature exists to deliver, and
it is what makes a second observation worth storing. It is second only because it is
inert until P1 supplies a series.

**Independent Test**: Seed a content item with three observations at deliberately uneven
spacing (e.g. 2 days, then 26 days). Confirm the derived per-day rates reflect actual
elapsed time rather than observation order, and that the values are persisted rather than
computed at read time.

**Acceptance Scenarios**:

1. **Given** two consecutive observations of an item, **When** derivation runs, **Then**
   the change in each metric between them is stored, **And** a rate normalized by the
   actual elapsed time between them is stored.
2. **Given** three observations spaced 2 days and 26 days apart, **When** derivation runs,
   **Then** the two intervals' rates are each divided by their own elapsed time, **And**
   neither interval is weighted as though spacing were uniform.
3. **Given** two observations of an item less than 24 hours apart, **When** derivation runs,
   **Then** no rate is produced for that interval, **And** the interval is recorded as too
   short to yield a rate rather than producing an extreme value, **And** the change in each
   metric across it is still stored.
4. **Given** an item with three observations where the first interval's per-day rate is at
   or below 10% of its highest interval rate and the following interval's rate is at least
   3× that plateau rate, **When** derivation runs, **Then** the late rise is flagged as
   acceleration after plateau, **And** the interval in which it was detected is identified.
5. **Given** two items with identical rate trajectories on accounts whose typical engagement
   differs by an order of magnitude, **When** derivation runs, **Then** both are flagged
   identically, proving the thresholds are scale-free.
6. **Given** an item with exactly one observation, **When** derivation runs, **Then** no
   velocity is produced, **And** the item is not recorded as having zero velocity.
7. **Given** a later observation whose count is lower than the earlier one, **When**
   derivation runs, **Then** the negative change is stored as observed, **And** it is not
   clamped to zero.
8. **Given** any stored velocity value, **When** it is surfaced, **Then** the number of
   observations it rests on is available alongside it.
9. **Given** derivation is re-run over observations it has already covered, **When** it
   completes, **Then** the derived records match the prior run rather than duplicating.

---

### User Story 3 - Absence is legible, never inferred (Priority: P3)

An analyst sees an item with no velocity. They can tell, without reading code, which of
these is true: it predates longitudinal capture; a refresh was attempted and failed; the
item has aged out of its account's listing; derivation has not yet run over the
observations it does have; or it simply has not been observed twice yet.

**Why this priority**: This is what keeps the other two stories honest. A velocity of
"nothing" that silently spans five distinct causes is precisely the confident-but-false
conclusion this system's constitution treats as worse than a visible failure. It is P3
only because it has no value until there are series to have gaps in.

**Independent Test**: Construct one item of each cause, query the store, and confirm each
resolves to exactly one distinct, named reason with no overlap and no unclassified
residue.

**Acceptance Scenarios**:

1. **Given** every content item captured before this feature, **When** the feature is
   deployed, **Then** each carries exactly one observation marked as inherited rather than
   captured, **And** each resolves as lacking observation history, **And** each is excluded
   from velocity-dependent analysis, **And** none is treated as zero-velocity.
2. **Given** a history-less item that is later observed a second time, **When** that
   observation is recorded, **Then** the item resolves as having history without any flag
   being updated, **And** velocity becomes derivable from the observations that genuinely
   exist, **And** the derived velocity remains identifiable as resting on an inherited
   earlier endpoint.
3. **Given** a refresh attempt that fails, **When** the run completes, **Then** the failed
   attempt is recorded with a classified reason, **And** it is distinguishable from an
   attempt that was never made.
4. **Given** an item that no longer appears in its account's listing, **When** a run
   completes, **Then** the absence is recorded with a reason distinguishing "aged out of
   listing depth" from "capture failed".
5. **Given** any content item with no velocity value, **When** the cause is queried,
   **Then** exactly one reason applies, **And** no item resolves to an unclassified state.

---

### Edge Cases

- **A count goes down.** Comments are deleted and view counters are revised. Negative
  deltas are real observations and are stored as such (FR-012). Clamping them to zero would
  fabricate an observation that was never made.
- **An item is observed twice within one run**, or two runs overlap. The observation record
  must tolerate this without collapsing the rows, and rate derivation must withhold a rate
  for the sub-24-hour interval rather than divide by it (FR-014).
- **The same post is harvested into two account folders.** Six such pairs exist today. The
  observation series is keyed to the content item, so both paths contribute to one series
  rather than two half-series.
- **A profile is rate-limited or private mid-run.** No observation is invented for its
  items; the missed attempt is recorded with a classified reason per FR-018.
- **An item ages out of listing depth, then returns** (e.g. the account deletes newer
  posts). The series resumes with a long gap. Derivation must handle the gap by elapsed
  time, not treat it as a fresh series.
- **An item is deleted from the platform.** Its existing observations remain valid history
  and are not purged; the series simply ends.
- **The failure-retry purge path** deletes an item's ledger record and re-harvests it. Its
  accumulated observation history must survive that purge — the media was bad, the past
  measurements were not.
- **Listing returns an item with no counts at all** (e.g. an Instagram story). An
  observation with no values is not silently written as zeros; it resolves through the
  existing field-availability determinations.
- **Derivation runs before a harvest, or fails while collection succeeds.** Because the two
  are separate processes (FR-016b), observations may exist that no derivation has yet
  covered. Such an item reports "not yet derived", which is distinct from "too few
  observations to derive" (FR-024).
- **Derivation is re-run over observations it has already covered**, e.g. after a threshold
  change. It must converge on the same records rather than accumulate duplicates (FR-016c).

## Requirements *(mandatory)*

### Functional Requirements

**Separating refresh from re-harvest**

- **FR-001**: The system MUST provide a metric-refresh path that records current counts for
  an already-harvested content item **without** downloading its media and **without**
  performing content analysis.
- **FR-002**: The system MUST continue to skip media download and content analysis for any
  item whose prior harvest succeeded. The existing de-duplication behaviour is preserved;
  only the discarding of counts changes.
- **FR-003**: The metric-refresh path MUST consume counts already present in a profile
  listing response and MUST NOT issue an additional platform request per item.
- **FR-004**: The system MUST refresh metrics for every item present in a listing response,
  including items excluded from that run by its day-window or recent-N depth filter.
- **FR-005**: The existing failure-retry purge path MUST remain unchanged in behaviour, and
  MUST NOT delete an item's accumulated observation history.

**Observation history**

- **FR-006**: The system MUST persist metric observations as timestamped records permitting
  many observations per content item.
- **FR-007**: Observation records MUST be append-only. An existing observation MUST NOT be
  updated or deleted to reflect a newer capture.
- **FR-008**: Every metric capture, including the first at initial harvest, MUST write an
  observation record.
- **FR-009**: Each observation MUST record which run produced it and when it was taken.
- **FR-010**: The existing single-row latest-value record per content item MAY be retained
  as a convenience and MUST reflect the most recent observation, but MUST NOT be the only
  place a captured value is stored.
- **FR-010a**: A metric refresh MUST NOT erase values it did not observe. Refreshing an
  item's counts MUST leave its analysis-derived fields (subtitle, content flow, summary)
  and its reviewer-assigned fields intact. A refresh carries no analysis output, so writing
  its empty analysis fields over stored ones would destroy work this feature never
  re-creates.

**Velocity derivation**

- **FR-011**: For each consecutive pair of observations of an item, the system MUST derive
  and persist the change in each metric, the actual elapsed time between the two
  observations, and a rate normalized to a per-day basis using that elapsed time.
- **FR-012**: Derivations MUST record a decrease as a negative change. Values MUST NOT be
  clamped, floored at zero, or discarded.
- **FR-013**: Derivations MUST NOT assume evenly spaced observations. No derivation may use
  observation sequence position as a proxy for elapsed time.
- **FR-014**: The system MUST decline to produce a rate for an interval shorter than a
  configured minimum, defaulting to **24 hours**, and MUST record that the interval was too
  short rather than emitting a value derived from near-zero elapsed time. The change in each
  metric across such an interval is still stored; only the rate is withheld.
- **FR-015**: The system MUST detect and persist late acceleration and MUST identify the
  interval in which it occurred. Thresholds MUST be scale-free, defined against the item's
  own interval rates rather than absolute counts or any external baseline: an interval is a
  **plateau** when its per-day rate is at or below a configured fraction (default **10%**)
  of that item's highest interval rate, and **acceleration** is a following interval whose
  per-day rate is at least a configured multiple (default **3×**) of the plateau interval's
  rate. Detection requires at least three observations, since it compares two intervals.
- **FR-015a**: Intervals withheld for being too short (FR-014) MUST NOT participate in
  plateau or acceleration detection, and MUST NOT be treated as a plateau by virtue of
  having no rate.
- **FR-016**: Derived velocity MUST be persisted in the datastore, not recomputed on each
  query.
- **FR-016a**: Derived velocity is exposed through the datastore only. This feature MUST
  NOT add velocity, change, or acceleration columns to any delivered spreadsheet, and MUST
  NOT alter the layout of the per-account harvest sheets or the per-run detail sheets.
- **FR-016b**: Velocity derivation MUST run as a scheduled process separate from
  collection, reading observations already stored rather than being computed during a
  harvest. A derivation failure MUST NOT affect collection, and a collection failure MUST
  NOT prevent derivation over observations already recorded.
- **FR-016c**: Derivation MUST be repeatable over the same observations without producing
  duplicate or conflicting derived records, so it can be re-run after a correction or a
  backfill.
- **FR-017**: Any surfaced velocity value MUST carry the count of observations it was
  derived from, retrievable in the same query that returns the value.

**Recording what did not happen**

- **FR-018**: A metric capture that was attempted and did not succeed MUST be recorded with
  a classified reason, and MUST NOT be silently skipped.
- **FR-019**: The reason vocabulary MUST distinguish at minimum: item provably beyond the
  listing's reach (published before the oldest item the listing returned); item that should
  have appeared in the listing but did not; item confirmed removed by the platform; capture
  attempt failed due to a platform block or throttle; capture returned no usable counts.
  These MUST NOT be collapsed into a single "failed". Confirmed removal is only observable
  on the download path, so an item merely absent from a listing MUST NOT be recorded as
  removed — absence within reach is consistent with removal but is not proof of it.
- **FR-020**: A content item that has never had a refresh attempted MUST be distinguishable
  from one whose refresh attempt failed.

**Pre-existing content**

- **FR-021**: Every content item captured before this feature — all pre-feature rows present
  when the seeding runs, whatever the count is at that moment — MUST have its existing stored
  counts seeded as a single observation carrying explicit legacy provenance, recording that
  the value was inherited from the pre-feature record rather than captured by this feature.
  Seeding MUST NOT invent a capture time it does not know; the observation is timestamped
  with the item's recorded harvest time.
- **FR-021a**: An item's history status MUST be derived from its observation records, not
  stored as a separately maintained flag. An item lacks observation history when its only
  observation carries legacy provenance.
- **FR-022**: Items lacking observation history MUST be excluded from velocity-dependent
  analysis and MUST NOT be treated as having zero velocity.
- **FR-023**: History status MUST update as a consequence of observations accumulating, with
  no manual intervention, no maintained flag to update, and no re-run of the seeding step.
- **FR-023a**: A legacy-provenance observation MUST be usable as the earlier endpoint of a
  velocity interval once a genuine later observation exists, and any velocity so derived
  MUST remain identifiable as resting on a legacy endpoint.
- **FR-024**: For any content item lacking a velocity value, exactly one classified reason
  MUST apply, drawn from a closed vocabulary distinguishing at minimum: only an inherited
  legacy observation exists; observed only once so far; observations exist but derivation
  has not yet run over them; every interval was too short to yield a rate; the item is no
  longer observable. An item resolving to no reason, or to more than one, is a defect.

**Cost**

- **FR-025**: This feature MUST NOT introduce any model call. Metric refresh is arithmetic
  and storage, not extraction.
- **FR-026**: This feature MUST NOT increase the number of platform requests issued by any
  existing scheduled run.
- **FR-027**: Velocity derivation MUST support a dry-run mode reporting what it would
  derive and over how many items, without writing.

### Key Entities *(include if data involved)*

- **Metric Observation**: One timestamped capture of a content item's public counts (views,
  likes, comments, shares). Append-only; many per content item. Carries the capturing run's
  identity, the moment of capture, and a provenance marking whether it was captured by this
  feature or inherited from the pre-feature record. This is the record of what was seen; it
  is never amended.
- **Velocity Derivation**: A derived record spanning one consecutive pair of observations.
  Holds the change in each metric, the elapsed time between them, and the resulting
  normalized per-unit-time rate. Marked when the interval was too short to yield a rate.
- **Acceleration Flag**: A marker on an item indicating its per-day rate rose after a
  plateau, identifying the interval where the rise was detected. Thresholds are relative to
  the item's own interval rates, so the flag is comparable across accounts of different
  sizes. Absent unless at least three observations exist.
- **Missed Capture Record**: A record that a capture was attempted or expected and did not
  produce an observation, with a reason drawn from a closed vocabulary. Distinct from the
  absence of any record at all.
- **History Status**: A per-content-item state derived from that item's observation
  records — not a stored, separately maintained flag. An item lacks history while its only
  observation carries legacy provenance, and gains history as a consequence of a genuine
  observation being recorded.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A content item's stored engagement numbers reflect what the platform reported
  at the most recent run that listed it, rather than at first sighting — verified by
  comparing stored values against a live listing for a sample of items last harvested more
  than 30 days prior.
- **SC-002**: Refreshing an already-harvested item's metrics consumes zero media downloads
  and zero model calls, verified by measuring both across a full monthly harvest cycle
  before and after.
- **SC-003**: Total platform requests per scheduled run are unchanged from the pre-feature
  baseline, verified against the per-listing request counts already reported by each run.
- **SC-004**: Monthly model spend attributable to harvest runs is unchanged from the
  pre-feature baseline.
- **SC-005**: After two consecutive monthly cycles, content items that remained within their
  account's listing depth carry at least two ordered observations each, and their count is
  reported.
- **SC-006**: Velocity for any item with sufficient observations is retrievable as a stored
  value without recomputation at query time.
- **SC-007**: Velocity derived from observations spaced 2 days and 26 days apart yields
  per-day rates that differ from those a uniform-spacing assumption would produce, proving
  actual elapsed time is used.
- **SC-008**: Every pre-feature content item resolves as lacking observation history, and none
  appears in any velocity-dependent output — verified without reading a maintained flag, by
  querying their observation records directly. The count is whatever the store holds when
  seeding runs (819 as of 2026-08-03, and still growing), never a fixed literal.
- **SC-009**: Every content item without a velocity value resolves to exactly one classified
  reason; the count of items resolving to no reason or to multiple reasons is zero.
- **SC-010**: A capture that was attempted and failed is distinguishable in the store from
  one never attempted, and from an item that aged out of listing depth.

## Assumptions

- **No new collection is introduced.** Metric refresh rides entirely on listings that
  existing scheduled runs already perform. Deepening listings, adding profiles, or adding
  refresh-only schedules is collection expansion (S-02) and is out of scope. This is the
  single most consequential scope assumption in the spec: it means observation cadence is
  monthly at best, and observation *reach* is bounded by listing depth.
- **Observation cadence is therefore roughly monthly**, set by the existing
  `harvest-monthly-*` schedules. Late acceleration is detectable only at that resolution and
  only while an item remains within its account's listing depth. An item that ages out
  before accumulating three observations will never carry an acceleration flag; this is
  recorded as a reason (FR-019), not treated as evidence of no acceleration.
- **The existing latest-value record is retained** as a convenience for current consumers
  (ranking, exemplar selection, the delivered sheets), so this feature does not require
  those consumers to change. They may migrate to the observation history later; that
  migration is not part of this feature.
- **Observation history is retained indefinitely.** Accumulated history is the asset this
  feature exists to build; no retention or pruning policy is introduced.
- **Reviewer-edited fields are unaffected.** The manually assigned advertisement flag and
  the daily sheet-to-store reconciliation continue to behave exactly as today. A metric
  refresh does not overwrite reviewer input.
- **Plateau and acceleration thresholds are configuration**, not fixed constants, since the
  right values are unknown until a corpus of real series exists. The defaults — a 24-hour
  minimum interval, a 10%-of-peak plateau, a 3× acceleration multiple — are provisional
  starting points chosen to be scale-free, explicitly to be revised against observed data
  once enough series exist to calibrate them. No claim is made that they are correct.
- **Content analysis output is untouched.** Subtitles, content flow, and summaries were
  produced from the media at first harvest and are not re-derived. Analysis is out of scope.
- **The unit of a series is the content item**, not the account folder it was delivered to,
  so the six posts harvested into two folders each yield one series rather than two.

## Out of Scope

- **Collection expansion (S-02)** — deeper listings, additional profiles, refresh-only
  schedules, or any change increasing platform request volume.
- **Content analysis** — no re-extraction, re-summarization, or any new model call.
- **Follower-count series and derived engagement rate** — these depend on account-level
  metadata capture, which has its own known upstream breakage, and are not addressed here.
- **Consumer migration** — ranking, exemplar selection, and trend detection continue reading
  the latest-value record. Teaching them to use velocity is a later feature.
- **Spreadsheet surfacing** — velocity is read from the datastore. No delivered sheet gains
  a velocity column, and no existing sheet layout changes. Presenting velocity to reviewers
  is a later feature.
- **Backfilling history that was never captured.** The 656 pre-existing items cannot be
  given a past. Their single stored value is carried forward as one legacy-provenance
  observation so the observation record is complete; no earlier point is reconstructed,
  estimated, or interpolated.

## Dependencies

- The existing profile-listing capability, which already returns current public counts for
  every item it lists.
- The existing scheduled monthly harvest deployments, which supply the observation cadence
  at no additional cost.
- The existing capture-outcome and field-availability records, which already distinguish
  "the platform never publishes this" from "this capture failed" and which the missed-capture
  vocabulary must extend rather than duplicate.
- The existing run records, which supply the run identity each observation is attributed to.
