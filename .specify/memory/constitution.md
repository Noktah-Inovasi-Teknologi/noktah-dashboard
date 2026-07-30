<!--
SYNC IMPACT REPORT
==================
Version change: 1.0.0 → 1.1.0 (MINOR — additive)

Rationale for MINOR rather than MAJOR: no existing principle is removed or
redefined. Principles I–V are preserved verbatim in intent. Principle II gains
two additive subsections (Collection Targets, Model Providers) and one scoped
carve-out to its retry mandate. Everything else is new.

New principles added:
  - VI.   Data Honesty & Metric Provenance
  - VII.  Append-Only Observation Records
  - VIII. Evidence Discipline
  - IX.   Identity Chain Integrity
  - X.    Polite Collection
  - XI.   Model Call Governance
  - XII.  Versioned Vocabularies & Additive Schema Change

Sections added:
  - Content Intelligence Domain Rules
  - Flow & Task Naming for Roach and Songbird

Sections amended:
  - Core Principles § II — added "Collection Targets" and "Model Providers"
    subsections; added scoped retry carve-out for collection tasks
  - Data Management & Integration Patterns — added datastore-of-record rule,
    taxonomy placement rule, and provenance requirements for JSON output
  - Development Workflow — added dry-run requirement for costed batch
    operations

Templates reviewed:
  - .specify/templates/plan-template.md ✅ — Constitution Check gate present;
    no change needed
  - .specify/templates/spec-template.md ✅ — no constitution-specific mandates
    required
  - .specify/templates/tasks-template.md ⚠️ — task categories align, but
    consider adding a "cost estimate" task category for specs that introduce
    model calls

Deferred TODOs / to verify against repo:
  - Constitution v1.0.0 names Google Sheets, Jira, and JSON files but no
    relational datastore. Principle VII and the Data Management section now
    assume a Postgres system of record. VERIFY this exists before running
    Wave 1 specs; if it does not, the Wave 0 audit must establish it and this
    section may need a PATCH amendment.
  - Confirm whether Roach and Songbird are already Prefect flows in this repo
    or separate services. The naming section assumes the former.
-->

# Noktah Dashboard Constitution

## Core Principles

### I. Workflow Orchestration via Prefect

All automation logic MUST be expressed as Prefect flows and tasks.

- **Flows** are async, return `Dict[str, Any]` with `start_time`, `end_time`, `data`, `summary`,
  and optional `error` fields. They MUST NOT raise exceptions to callers.
- **Tasks** are atomic, single-responsibility operations. They MUST raise exceptions so Prefect's
  retry mechanism can handle failures.
- Flow names use kebab-case (e.g., `"content-plan-to-jira"`).
- Task names follow the `"api-group.resource.action"` pattern
  (e.g., `"google.sheets.read-data"`).
- All flows MUST be independently executable via `docker exec prefect python flows/<name>.py`.

### II. External API Integration Standards

All external API calls MUST follow the project's established integration patterns.

- **Google APIs**: authenticate via OAuth2 with three-method fallback
  (token.json → env refresh token → interactive flow).
  Required env vars: `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REFRESH_TOKEN`.
- **Jira API**: authenticate via Basic Auth (email + API token).
  Required env vars: `JIRA_URL`, `JIRA_USERNAME`, `JIRA_API_TOKEN`.
- API calls MUST include random delays between requests (e.g., 5–10 s) to respect rate limits.
  Delay MUST be omitted after the last item in a sequence.
- Bulk Jira creation MUST use the REST bulk endpoint with a maximum of 45 issues per batch.
- Tasks that call external APIs MUST specify `retries=2` and `retry_delay_seconds=30`.

**Collection targets (Roach).** Social platforms are not integration partners and MUST NOT be
treated as such.

- No social platform API (Instagram Graph API, TikTok API, or equivalent) is available to this
  project. Flows MUST NOT be designed around one, and MUST NOT assume a publish-time callback,
  a returned platform post ID, or access to private analytics.
- **Retry carve-out**: the `retries=2` / `retry_delay_seconds=30` mandate above does NOT apply
  to collection tasks. Repeated rapid retry against a blocking or throttling target is the
  wrong response and is prohibited. Collection tasks MUST use exponential backoff with jitter,
  MUST trip a circuit breaker after a configured consecutive-failure count, and MUST surface
  the failure rather than persisting.
- Pacing, backoff, and concurrency ceilings MUST be governed centrally as configuration, not
  implemented per collection task.

**Model providers (Roach extraction, Songbird generation).**

- Model selection MUST be configuration per call site, never hardcoded in flow or task logic,
  so tiers can change without redeployment.
- Every model call MUST be attributable to a call site, a feature, and where applicable a client.
- Model responses MUST be validated against a schema before use. Invalid output is retried once
  with the validation error fed back, then quarantined. Unvalidated model output MUST NOT reach
  storage.

### III. Docker-First Deployment

The canonical runtime environment is Docker. All services MUST be containerizable and runnable
via `docker-compose`.

- Base image: `prefecthq/prefect:3.6.9-python3.14`.
- Package manager: UV (`uv sync --frozen --no-dev` for production builds).
- The Dockerfile MUST copy `pyproject.toml` and `uv.lock` before application code to preserve
  layer caching.
- Dependencies MUST be pinned in `uv.lock` for reproducible builds.
- New services MUST be added to `docker-compose.yml` with a health check.
- Volume mount `service/prefect → /app` enables live code reload during development.

### IV. Security & Credential Management

Credentials MUST never appear in source code or version control.

- All sensitive values (API keys, tokens, passwords) MUST be sourced from environment variables
  or Prefect Blocks.
- Prefect Blocks MUST use `SecretStr` for sensitive fields and expose a `load_or_env(name)`
  classmethod that tries `cls.load(name)` first, then falls back to env vars.
- The `.env` file is gitignored and MUST NOT be committed.
- Log output MUST NOT contain API keys, tokens, or passwords.
  Log resource IDs and block names instead.
- Client social credentials MUST NOT be stored, shared between flows, or used to access content
  that is not publicly visible. See Principle X.

### V. Observability & Error Handling

Flows MUST be observable and gracefully handle partial failures.

- Log at appropriate levels:
  - `INFO` — normal progress, state transitions, performance metrics
  - `WARNING` — recoverable issues, skipped items, fallback behaviour
  - `ERROR` — failures affecting results, API errors, auth failures
  - `DEBUG` — request/response payloads, detailed diagnostics
- Flows MUST continue processing remaining items when a single-item failure occurs.
- Flows MUST set `end_time` in a `finally` block and include a `summary` with success/failure
  counts.
- JSON output files MUST include a `metadata` section (workflow name, execution timestamp,
  source, record count) alongside the `data` array, using UTF-8 encoding and 2-space indent.
- A skipped or failed item MUST be recorded with a classified reason, never silently dropped.
  Silent partial success is the most dangerous failure mode in this system: analysis continues
  and produces confident conclusions from an incomplete corpus.

### VI. Data Honesty & Metric Provenance

The system MUST NOT store, compute, or display a metric it cannot actually observe.

- Public collection yields no reach, impressions, watch time, completion rate, or saves. Columns
  representing these MUST NOT exist unless explicitly typed as estimates.
- **Observations and estimates MUST NOT share a field.** An estimated value MUST carry its
  derivation method, source calibration, and confidence, and MUST be visually and structurally
  distinct from an observed value wherever it is surfaced.
- **Scraped metrics and first-party metrics MUST NOT be merged into the same field.** Any
  consumer MUST be able to determine which source a number came from.
- Where a consumer requests an unavailable metric, the system MUST return an explicit
  unavailable. It MUST NOT silently substitute a proxy.
- Interpolated values (e.g., follower count between collection points) MUST be marked as
  interpolated.

### VII. Append-Only Observation Records

Metric captures are immutable, timestamped rows.

- An observation MUST NOT be updated in place. Re-observing a content item creates a new row.
- Velocity — the rate at which engagement accrues — is the system's closest available substitute
  for the retention and watch-time signals it cannot obtain. It is derivable only from an
  append-only history. This principle is load-bearing, not stylistic.
- Missed observations MUST be recorded as missed attempts with a classified reason, never
  omitted. Derivations MUST NOT assume evenly spaced observations.
- Content items collected before longitudinal capture existed MUST be marked as lacking history
  and excluded from velocity-dependent analysis, never treated as having zero velocity.

### VIII. Evidence Discipline

Analytical output MUST be conservative, sample-aware, and honest about uncertainty.

- **Sample size is always visible.** Any effect estimate, benchmark, baseline, or recommendation
  surfaced to a user MUST display the observation count it rests on.
- **Minimum sample is enforced structurally.** Below a configured threshold the system reports
  insufficient data. This threshold MUST NOT be overridable from a user interface.
- **Estimates are shrunk** toward the relevant baseline in proportion to sample size. Raw means
  from small samples MUST NOT be surfaced as findings. Store raw and shrunk; surface shrunk.
- **Matched negatives are required.** Any effect estimate MUST be computed over both content
  that carried an attribute and performed well and content that carried it and performed poorly.
  A code path that computes an effect from high performers only MUST NOT exist.
- **Language is associational, never causal.** No output may claim a creative decision caused a
  performance outcome.
- Cross-client aggregates MUST satisfy both a minimum observation count and a minimum distinct
  account count, and MUST contain no client-identifying information.

### IX. Identity Chain Integrity

Every content item MUST be traceable: plan → brief → published post → observations → learned prior.

- Because no publishing API exists, the brief-to-post link is inferred, not known. Every
  attribution MUST carry a confidence value.
- **Confidence propagates.** Any analysis consuming attributed data MUST be filterable by minimum
  attribution confidence, and any conclusion MUST be able to report the confidence of the
  attributions beneath it.
- Ambiguous attributions MUST be queued for human confirmation, never resolved by guessing.
- A confirmed manual link MUST always override an inferred one.
- Content published without a brief MUST remain analyzable. It is valid performance evidence.

### X. Polite Collection

Roach collects publicly accessible content from public accounts, conservatively, and identifies
itself honestly.

- On blocking or throttling the system MUST slow down, back off, and eventually pause the
  affected target.
- The system MUST NOT respond to blocking by increasing request rate, rotating identity to evade
  detection, solving CAPTCHAs, or any equivalent circumvention. **No such code path may exist.**
- Authentication MUST NOT be used to access content that is not publicly visible. Accounts that
  become private are marked inactive and no longer attempted.
- If a target becomes uncollectable, that is a reported fact, not a problem to route around.
- Collection failures MUST be classified (not found, private, deleted, blocked, parse failure,
  timeout, unexpected structure) and counted per target and platform, so a platform markup change
  is distinguishable from a target going private.

### XI. Model Call Governance

The system operates on a small fixed monthly LLM budget. Cost is a hard constraint, not a metric.

- **Deterministic first.** Any output verifiable by a rule MUST be produced by deterministic code,
  not a model call. Classification against a fixed list, format validation, similarity
  computation, and arithmetic are not model problems.
- **Batch over interactive.** Extraction, tagging, re-extraction, and background analysis are not
  latency-sensitive and MUST be batched where the provider supports it.
- **Cache by content hash.** Unchanged input MUST NOT be re-processed.
- A configured monthly ceiling MUST be enforced, not merely reported. Exceeding it triggers a
  configured policy: halt non-essential calls, downgrade tier, or defer.
- Any feature adding model calls MUST state its marginal cost per unit at expected volume before
  it ships.
- **No model is a final arbiter of creative quality.** Models may generate, extract, classify,
  and rank. A human approves what ships.
- **Negative evidence is a correctness requirement.** Any retrieval informing generation MUST
  include underperforming examples, enforced at the retrieval layer rather than left to prompt
  construction. Retrieving only successes causes generated output to converge on existing work.

### XII. Versioned Vocabularies & Additive Schema Change

This system's value comes from accumulated history. History MUST NOT be casually invalidated.

- The controlled vocabulary describing content attributes is a **versioned artifact stored as
  data**, not hardcoded. Every tag application records the vocabulary version under which it was
  assigned.
- Changing a vocabulary dimension requires a documented migration and backfill plan. Tags MUST
  NOT be silently reinterpreted.
- Extraction records the prompt version, schema version, and model used. Prior extractions remain
  valid under their own version.
- Schema changes are additive by default. Destructive migrations require explicit justification
  in the plan's Complexity Tracking table.

## Content Intelligence Domain Rules

**Metric availability.** The following table is normative. Flows, schemas, and reports MUST
conform to it.

| Concept | Status | Rule |
|---|---|---|
| likes, comment count, comment text | Observable | Store as observation |
| video view / play count | Observable if present | Store as observation; record absence explicitly |
| share count | Observable if present | Store as observation; record absence explicitly |
| follower count at post time | Derived | Interpolate between collection points; mark interpolated |
| engagement rate | Derived | (likes + comments) ÷ followers-at-post-time |
| velocity | Derived | Δ between consecutive observations; requires ≥2 observations |
| cohort-relative rank | Derived | Percentile or standardized score within cohort, format, and size band |
| reach, impressions | **Unavailable** | MUST NOT exist as an observed field |
| watch time, completion rate, retention | **Unavailable** | MUST NOT exist as an observed field |
| saves | **Unavailable** | MUST NOT be modelled or proxied |
| sends per reach | **Not computable** | No denominator exists |

**Baselines.** Three baselines are maintained separately and MUST NOT be collapsed: the account's
own trailing performance, its cohort's performance, and the corpus for that format and size band.
Baselines use rolling windows, never lifetime history, and record their window length.

**Seasonality.** Content published in flagged seasonal periods (notably Ramadan and Eid) MUST be
flagged so it is separable from non-seasonal baselines.

**Tranches.** Every planned content item is assigned a tranche — proven, iteration, or
exploration — before briefing. **The tranche travels with the item into measurement and governs
its evaluation criteria.** Exploration items are evaluated on learning value, not on beating
baseline, and MUST NOT be retroactively judged by proven-tranche standards.

**Extraction output.** Content flow MUST be an ordered sequence of beats, each with position,
function drawn from a closed vocabulary, and description. Derived attributes MUST be single
values from fixed lists. Confidence is reported per attribute, not per extraction.

## Data Management & Integration Patterns

Static data mappings (workers, components, role assignments) live in `hashmap.py` and MUST be
accessed via `.get()` with explicit fallback values. They MUST NOT be embedded in flow or task
logic directly.

**The content attribute vocabulary is NOT static mapping data and MUST NOT live in `hashmap.py`.**
It is versioned, queryable, migratable state and belongs in the relational store. See Principle XII.

The relational datastore is the system of record for content items, extractions, observations,
briefs, plans, attributions, and effect estimates. JSON files under `service/prefect/data/` are
development output and MUST NOT be treated as a system of record for any of the above.

Data transformations MUST follow the ETL pattern: Extract raw data, Transform each record
individually with validation, Load to the destination. Validate-only mode (`--validate-only`)
MUST be supported by all flows that write to external systems.

Date/time values in API payloads MUST use ISO 8601 (`YYYY-MM-DDTHH:MM:SSZ`). Jira date fields
use `YYYY-MM-DD` only. Indonesian month names are permitted for user-facing CLI arguments.

Numeric fields read from spreadsheets MUST be coerced to `float`, with `None` returned for
empty or non-numeric values.

JSON output containing metric data MUST extend the `metadata` section with provenance fields:
collection timestamp, source (scraped / first-party / estimated), extraction schema version, and
vocabulary version where applicable.

## Flow & Task Naming for Roach and Songbird

Extends Principle I. Flow names remain kebab-case; task names remain `"api-group.resource.action"`.

**Roach** — collection and extraction:
- Flows: `roach-collect-<scope>`, `roach-extract-<scope>`, `roach-reobserve-scheduled`
- Tasks: `roach.<platform>.fetch-profile`, `roach.<platform>.fetch-post`,
  `roach.extract.flow-summary`, `roach.extract.attributes`, `roach.observe.capture`

**Songbird** — planning and generation:
- Flows: `songbird-monthly-plan`, `songbird-generate-briefs`, `songbird-resolve-hypotheses`
- Tasks: `songbird.retrieve.context`, `songbird.generate.angles`, `songbird.generate.brief`,
  `songbird.validate.voice`, `songbird.validate.specificity`

**Analysis** — learning layer:
- Flows: `analysis-recompute-baselines`, `analysis-recompute-priors`, `analysis-attribute-posts`
- Tasks: `analysis.baseline.compute`, `analysis.effect.estimate`, `analysis.attribution.match`

## Development Workflow

Local development uses `uv run` inside `service/prefect/`. Docker is the target for all
integration testing and production runs.

Flows MUST include an `if __name__ == "__main__":` block that writes timestamped JSON output
to `service/prefect/data/` for immediate feedback during development.

Tasks MUST be tested independently with sample data before being composed into flows.

Container changes require `docker-compose up -d --build prefect`. Dependency changes require
`uv sync` locally and a container rebuild.

**Costed batch operations** (re-extraction, backfill, collection schedule changes) MUST support
a dry-run mode that reports projected model spend and projected collection volume without
executing. This is the cost-side analogue of `--validate-only`. Operations projected above a
configured threshold require explicit confirmation.

Collection tasks MUST be developable against fixtures. Iterating on parsing or extraction logic
by repeatedly hitting live targets violates Principle X.

## Governance

This constitution supersedes all other practices for the Noktah Dashboard project. Amendments
require: (1) updating this file with a version bump per the policy below, (2) propagating
changes to dependent templates, and (3) a commit message referencing the new version.

**Versioning policy**:
- MAJOR: Removal or redefinition of an existing principle.
- MINOR: Addition of a new principle or section.
- PATCH: Clarifications, wording fixes, non-semantic refinements.

All PRs MUST pass a Constitution Check (see `.specify/templates/plan-template.md`) before
Phase 0 research and again after Phase 1 design.

Complexity violations (e.g., introducing a new abstraction layer not mandated by a principle)
MUST be justified in the plan's Complexity Tracking table.

**Principles VI–XII are correctness requirements, not style guidance.** A plan that satisfies
Principles I–V while violating VI–XII produces a system that runs correctly and concludes
falsely, which is worse than one that fails visibly. Constitution Check MUST treat them with
equal weight.

Refer to `.claude/rules/backend/prefect.md` for runtime development guidance.

**Version**: 1.1.0 | **Ratified**: 2026-07-05 | **Last Amended**: 2026-07-31
