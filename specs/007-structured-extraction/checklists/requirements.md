# Specification Quality Checklist: Structured Extraction Output

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

## Notes

### Clarifications resolved (2 of 2) — 2026-08-03

Both were scope decisions rather than detail gaps. Both are answered; see the spec's Clarifications
section for the record.

1. **FR-027 — parity method → controlled comparison.** A fixed sample is run through both models on
   identical input and the agreement rate reported. Chosen over observational reporting, which
   cannot separate the model effect from the fact that video and image content genuinely differ.
   Three consequences are specified rather than assumed away: the comparison costs model calls and
   is therefore a costed batch operation on backfill's terms (FR-027e); it is bounded by an
   asymmetry, since the image model cannot accept video, making the result one-directional
   (FR-027a); and calibration extractions must be excluded from the analytical corpus or every
   distribution over the sample is double-weighted (FR-027c).
2. **FR-043 — sequencing against S-05 → ship ahead.** Flow, validation, quarantine, and versioning
   land first; attribute behaviour is built and inert. Two consequences added: this feature now owns
   the vocabulary storage and versioning mechanism S-05 will populate rather than S-05 inventing one
   (FR-043a), and the second backfill is an accepted, costed consequence whose projected bill must
   be statable when this ships (FR-043b).

### Requirement count

51 functional requirements (FR-001 – FR-043b) across 8 groups; 18 success criteria; 5 user stories
(2×P1, 2×P2, 1×P3); 16 edge cases.

### Content-quality note

The "Correction to the input premise" section and a small number of source line references cite
implementation. This is deliberate and follows the precedent of `docs/AUDIT.md` §0: the brief's
stated premise about `analyze.py` was factually wrong in a way that changes the scope of the work,
and correcting it in prose alone would leave the claim unfalsifiable. The requirements themselves
(FR-001 – FR-043) name no language, framework, library, or API.

### Constitution alignment spot-checks

- **VI (data honesty)** — FR-010, FR-021, FR-033, FR-035 keep absence and estimate distinguishable
  from observation.
- **VIII (evidence discipline)** — FR-014, FR-025, FR-026, SC-013 keep sample size visible and
  forbid averaging a divergence away. FR-027e enforces a minimum calibration sample structurally
  before any agreement figure is reported; FR-027b keeps every figure tied to the versions it was
  measured under.
- **XI (model call governance)** — FR-032, FR-033, FR-037, FR-038, FR-039, FR-040 cover dry-run,
  measured cost, threshold confirmation, and caching. The controlled comparison (FR-027e) and the
  second backfill (FR-043b) are both model spend and are both put under the same discipline rather
  than treated as incidental.
- **XII (versioned vocabularies, additive change)** — FR-004, FR-015, FR-028 – FR-030 and the
  additive assumption cover versioning and non-invalidation of prior extractions.

### Verification status

- [x] Constitution loaded and cross-checked (`.specify/memory/constitution.md`, v1.1.0)
- [x] `docs/AUDIT.md` §7, §8, §9 and SHARED §1 read
- [x] `service/roach/analyze.py` read in full
- [x] `service/prefect/tasks/openrouter_tasks.py` validation path read (premise correction)
- [x] Media retention path confirmed (`social_harvest.py` — local files unlinked after Drive upload)
