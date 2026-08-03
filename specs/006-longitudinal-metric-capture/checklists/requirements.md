# Specification Quality Checklist: Longitudinal Metric Capture

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-08-03
**Feature**: [spec.md](../spec.md)

## Content Quality

- [x] No implementation details (languages, frameworks, APIs)
- [x] Focused on user value and business needs
- [x] Written for non-technical stakeholders
- [x] All mandatory sections completed

## Requirement Completeness

- [x] No [NEEDS CLARIFICATION] markers remain
- [x] Requirements are testable and unambiguous
- [x] Success criteria are measurable
- [x] Success criteria are technology-agnostic (no implementation details)
- [x] All acceptance scenarios are defined
- [x] Edge cases are identified
- [x] Scope is clearly bounded
- [x] Dependencies and assumptions identified

## Feature Readiness

- [x] All functional requirements have clear acceptance criteria
- [x] User scenarios cover primary flows
- [x] Feature meets measurable outcomes defined in Success Criteria
- [x] No implementation details leak into specification

## Constitution Alignment

This feature implements Principle VII (Append-Only Observation Records) almost line for
line. Checked explicitly because the principle is a correctness requirement, not style
guidance.

- [x] **VII** — observations immutable and timestamped (FR-006, FR-007); re-observation
      creates a new row, never an update (FR-007); missed observations recorded with a
      classified reason (FR-018, FR-019); derivations do not assume even spacing (FR-013);
      pre-existing content carried forward as legacy-provenance observations, resolved as
      history-less from the records themselves, and excluded from velocity-dependent
      analysis rather than treated as zero-velocity (FR-021, FR-021a, FR-022, FR-023)
- [x] **VI** — decreases stored as observed rather than clamped (FR-012); a refresh never
      erases values it did not observe (FR-010a); no metric is stored that cannot be
      observed; no proxy is substituted for a missing capture
- [x] **VIII** — sample size travels with every surfaced velocity value (FR-017)
- [x] **X** — no increase in request volume against any collection target (FR-026); an item
      that becomes uncollectable is a reported fact with its own reason (FR-019)
- [x] **XI** — zero model calls introduced (FR-025); dry-run mode for the costed batch
      derivation (FR-027)
- [x] **XII** — the missed-capture reason vocabulary is closed and enumerated (FR-019);
      history is accumulated, not invalidated

## Validation Notes

**Iteration 1 findings (resolved in the spec as written):**

1. **Stale premise in the feature request.** The request asserts that `harvest-3mo-*`
   deployments re-list a 90-day window monthly, making a monthly cadence free. Verified
   against [prefect.yaml](../../../service/prefect/prefect.yaml): those deployments are now
   `harvest-monthly-*` with `days: 31`. Left uncorrected, this would have produced
   requirements written against a reach the system does not have. Addressed by the
   "Correction to a premise" section and carried into FR-004 and the assumptions.

2. **The free-observation surface is bounded by listing depth, not by a day window.**
   [social_harvest.py:510-511](../../../service/prefect/flows/common/social_harvest.py#L510-L511)
   shows the day-window is a client-side filter applied *after* the listing returns. Counts
   for items outside the window are already in hand and currently discarded. This makes
   FR-004 (refresh everything listed, not just everything selected) the requirement that
   determines whether the feature yields a usable series at all.

3. **"Late acceleration after an initial plateau" is only partly reachable.** At a monthly
   cadence bounded by listing residency, an item must survive three monthly listings to
   carry an acceleration flag. High-frequency accounts will age items out first. Not a
   defect to solve here — solving it means listing deeper, which is S-02. Handled by
   requiring the reason be recorded (FR-019) so a missing flag is never read as evidence of
   no acceleration.

**No unresolved issues.** All checklist items pass; zero clarification markers were needed,
as every open question had a defensible default recorded in Assumptions.

**Iteration 2 — clarification session 2026-08-03 (5 questions, all answered):**

Five decisions were promoted from Assumptions into binding requirements: velocity is
datastore-only with no spreadsheet surfacing (FR-016a); derivation is a separate scheduled
process, not inline in collection (FR-016b, FR-016c); history status is derived from
observation records rather than a maintained flag, with legacy rows seeded as
legacy-provenance observations (FR-021, FR-021a, FR-023a); the minimum interval yielding a
rate is 24 hours, rates expressed per day (FR-011, FR-014); plateau and acceleration
thresholds are scale-free against the item's own intervals, defaulting to 10%-of-peak and
3× (FR-015, FR-015a).

One defect was found by reading the code rather than the spec and fixed without spending a
question: [social_tasks.py:292-294](../../../service/prefect/tasks/social_tasks.py#L292-L294)
overwrites `subtitle`, `content_flow`, and `summary` unconditionally on upsert, unlike
`shares` which uses `COALESCE`. A metric refresh reusing that path would have wiped analysis
output across every refreshed row. Now forbidden by FR-010a.

Checkbox states unchanged at 22/22 passing; the spec became more specific in every area the
clarifications touched, and no previously passing item regressed.
