# Specification Quality Checklist: Relational Spine — Clients, Accounts, Runs, and Briefs

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-07-31
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

## Validation Notes

**Iteration 1 (2026-07-31)**

Passing:

- Content quality — the spec names entities and outcomes, not tables, types, or DDL. Table and
  column names appear only in the Context section as a description of *current* state, which is
  factual background rather than a design instruction.
- Success criteria are measured against counts read from the live database (62 / 656 / 691 rows;
  14 / 18 / 21 / 22 distinct names and handles), so each is checkable without knowing how it was
  built.
- Edge cases are drawn from live data rather than imagined: the opaque TikTok identifier stored as
  a handle, the unattributed `rsmatadryap`, the ambiguous `Nirwana Coffee Space` alias, and the
  three clients with no social account are all present in the system today.
- Scope is bounded by an explicit Out of Scope section naming the deferred features (S-06, S-07,
  S-08).

Outstanding:

- **1 [NEEDS CLARIFICATION] marker on FR-008** — account-to-client cardinality. This is a genuine
  fork rather than a detail: if an account may relate to only one client, a competitor named by
  two clients cannot be represented without duplicating the account, which the handle's
  uniqueness rule forbids and which would break the link from performance records. Presented to
  the user as Q1; this checklist item is re-evaluated once answered.

**Iteration 2 (2026-07-31) — all items pass**

Q1 answered: **role edges**. An account is one record per handle; the role lives on a
client-account relationship, so a national chain can be a competitor of several clients while
remaining a single account with a single stream of harvested content.

Spec updated accordingly:

- FR-007 moves the role from the account to the relationship; FR-008 permits any number of
  clients per account, including none.
- Two new integrity rules added, both drawn from the shape the answer implies rather than stated
  in it: FR-009 — at most one `owned` relationship per account, since a handle has one owner
  however many clients watch it; FR-010 — at most one relationship per client-account pair, so a
  role is never ambiguous. Subsequent requirements renumbered to FR-029.
- New Key Entity **Client Account Role**; the **Account** entity no longer carries a client or a
  role.
- US1 gains an acceptance scenario for the multi-client account; SC-001/SC-002/SC-003 restated so
  they do not assume exactly one client per account, while recording that today's data does have
  exactly one.
- The corresponding edge case is resolved rather than deferred, and a second added for the
  rejected case (two clients claiming the same handle as owned).

No [NEEDS CLARIFICATION] markers remain. Spec is ready for `/speckit-plan`.

**Iteration 3 (2026-07-31) — clarification session, 5 questions, 14/16 passing**

Answers integrated:

1. Unknown handle during collection → skip with a classified reason, continue the profile.
   Accounts are created only by reconciliation or the backfill (FR-016, FR-016a/b/c).
2. Account identity → a stable internal identifier, with the handle demoted to an observed
   attribute and a handle history, so a rename does not split an account (FR-011, FR-011a/b/c;
   new `Account Handle` entity; SC-003a).
3. Reconciliation cadence → daily schedule plus manual trigger, with nothing gating collection on
   it (FR-022a). Because no pre-flight check guards it, FR-023a now requires the run summary to
   distinguish "collected nothing, nothing new" from "collected nothing, handle unregistered" —
   otherwise a sheet edit that never reconciled is indistinguishable from a quiet month.
4. Follower counts → captured on every collection run, best-effort, absence recorded as absence
   (FR-013a/b).
5. Inactive → blocks storage rather than being descriptive only (FR-024a/b, FR-016a). Because
   collection targets live outside the datastore, this spends rate-limited requests on content
   that is then discarded, so FR-024b requires each newly-inactive account to be reported. Noted
   as a deliberate cost with the proper fix (datastore-driven targets) explicitly out of scope.

Two items regressed, both the same issue:

- **No implementation details / no implementation leak** — the Assumptions section now names
  Instaloader, a GraphQL document id, and a specific endpoint, added at the user's request while
  answering Q4. This is a genuine leak of an API-level detail into a spec that is otherwise
  technology-agnostic. It is contained: it sits in Assumptions, is labelled as a lead for planning
  to evaluate, and constrains no requirement — FR-013a/b state the outcome without naming a
  mechanism, and the spec states outright that nothing depends on it succeeding.
- **This does not block `/speckit-plan`.** The natural resolution is for planning to move that
  paragraph into `research.md`, where an evaluated technical option belongs, and delete it from
  the spec. Re-check both items once that happens.

**Iteration 4 (2026-07-31) — planning complete, 16/16 passing**

Both regressed items are re-checked. `/speckit-plan` probed the Instaloader route against the live
platform (`research.md` R3) and it **does not work** — two calls, both `HTTP 200` with a GraphQL
`execution error` and `data: null`, which rules out throttling, TLS fingerprinting, and the app id.
The evaluated option and its negative result now live in `research.md`, where an evaluated
technical option belongs, and the spec's Assumptions state the outcome without naming a library, an
endpoint, or a document id. The spec is technology-agnostic again.

Planning did not weaken any other item. It strengthened one: `research.md` R1 found the roster has
**two** disagreeing spreadsheet sources rather than the one the spec assumed (`Eskala` exists only
in `COMPONENTS`; Sumenep is spelled "Shop" in one sheet and "Space" in the other). That widens the
name-reconciliation problem the spec set out to solve without invalidating any requirement — the
alias table already handles it, and `alias.source` distinguishes the two sheets.

## Notes

- Items marked incomplete require spec updates before `/speckit-clarify` or `/speckit-plan`
