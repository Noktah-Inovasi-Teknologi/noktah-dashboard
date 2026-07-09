<!--
SYNC IMPACT REPORT
==================
Version change: [TEMPLATE] → 1.0.0
New principles added:
  - I. Workflow Orchestration via Prefect
  - II. External API Integration Standards
  - III. Docker-First Deployment
  - IV. Security & Credential Management
  - V. Observability & Error Handling
New sections added:
  - Data Management & Integration Patterns
  - Development Workflow
Templates reviewed:
  - .specify/templates/plan-template.md ✅ — Constitution Check gate already present
  - .specify/templates/spec-template.md ✅ — no constitution-specific mandates required
  - .specify/templates/tasks-template.md ✅ — task categories align with principles
Deferred TODOs: none
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

## Data Management & Integration Patterns

Static data mappings (workers, components, role assignments) live in `hashmap.py` and MUST be
accessed via `.get()` with explicit fallback values. They MUST NOT be embedded in flow or task
logic directly.

Data transformations MUST follow the ETL pattern: Extract raw data, Transform each record
individually with validation, Load to the destination. Validate-only mode (`--validate-only`)
MUST be supported by all flows that write to external systems.

Date/time values in API payloads MUST use ISO 8601 (`YYYY-MM-DDTHH:MM:SSZ`). Jira date fields
use `YYYY-MM-DD` only. Indonesian month names are permitted for user-facing CLI arguments.

Numeric fields read from spreadsheets MUST be coerced to `float`, with `None` returned for
empty or non-numeric values.

## Development Workflow

Local development uses `uv run` inside `service/prefect/`. Docker is the target for all
integration testing and production runs.

Flows MUST include an `if __name__ == "__main__":` block that writes timestamped JSON output
to `service/prefect/data/` for immediate feedback during development.

Tasks MUST be tested independently with sample data before being composed into flows.

Container changes require `docker-compose up -d --build prefect`. Dependency changes require
`uv sync` locally and a container rebuild.

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

Refer to `.claude/rules/backend/prefect.md` for runtime development guidance.

**Version**: 1.0.0 | **Ratified**: 2026-07-05 | **Last Amended**: 2026-07-05
