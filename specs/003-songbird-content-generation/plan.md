# Implementation Plan: Songbird — Targeted Content Generation

**Branch**: `003-songbird-content-generation` | **Date**: 2026-07-15 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/003-songbird-content-generation/spec.md`

## Summary

Add **songbird**, a Prefect-orchestrated content-generation capability that turns accumulated client
knowledge and social-performance signal into an actionable content plan. Two flows share one engine and
differ only in triggering + delivery: **songbird-monthly-plan** (scheduled; reads the per-client monthly
quantity from the Clients worksheet, distributes publish dates across the target month, delivers a
rationale-annotated draft by default or appends to the live content-plan worksheet) and
**songbird-generate** (manual; standalone draft-only ideas, no dates, no live handoff). Generation is
prompt-assembly + an OpenRouter chat call — no heavy dependencies — so it lives entirely in the existing
`service/prefect` image (constitution I). The "hit" bias draws on four signals: current client knowledge
(`knowledge_records`), and the client's own + competitors' top-performing harvested posts ranked by
engagement over a rolling 180-day window from a new `harvested_signals` store populated by the existing
social-harvest engine. Output is Indonesian with natural English code-mixing; a "hit not guaranteed"
caveat rides in every run outcome. No Jira calls — handoff is via the content-plan worksheet only.

## Technical Context

**Language/Version**: Python 3.14 (project standard; the Prefect service image).

**Primary Dependencies**:
- Orchestration: **Prefect 3.6.9** (async flows, tasks, `get_run_logger`, deployments, cron schedule).
- Generation: **OpenRouter** chat-completions (house model `xiaomi/mimo-v2.5`, provider-routing +
  structured JSON), called over **httpx** (already a Prefect-service dep via `social_tasks.py`). Ported
  from the roach analyze pattern into a reusable text-only task; **no new package** required.
- Client grounding: **asyncpg** against `knowledge_records` (feature 001).
- Performance signal: **asyncpg** against the new `harvested_signals` table (this feature).
- Delivery + config read: **google-api-python-client** / **google-auth** via the existing
  `GoogleCredentials` block + `google_tasks.py` (Sheets read/append/ensure, Drive folder ensure).

**Storage**:
- PostgreSQL 15 (existing `postgres` service) — new `harvested_signals` table (rankable engagement +
  analysis mirror), plus reads of existing `knowledge_records`.
- Google Sheets/Drive — the Clients config worksheet (`1-aV46TIn4m_zs3vtCNeS_Bvl3Tt-tgg09uuG_NqgNNY`,
  read-only for quantity), draft deliverables under `SONGBIRD_DRIVE_PARENT_ID`, and the live content-plan
  worksheet (append). No new local storage.

**Testing**: pytest with OpenRouter, Google, and Postgres access mocked for the engine/task unit tests
(date distribution, column alignment, graceful degradation, per-idea continue-on-failure, run-outcome
shape). A live smoke path is documented in `quickstart.md` (real container + real Clients sheet).

**Target Platform**: Linux container on Docker Desktop, `dashboard-networks`, the existing `prefect` /
`prefect-worker` services.

**Project Type**: Prefect workflow (orchestration) — no new service.

**Performance Goals**: On-demand small batch (≤10 ideas) from trigger to draft in under 5 minutes
(SC-002); monthly plan of the per-client size in a single run (SC-001).

**Constraints**: Indonesian output with natural English code-mixing (FR-003a); "hit not guaranteed"
caveat in every outcome (FR-006); publish dates assigned by songbird, never the model (FR-004); no direct
issue-tracker calls (FR-018); flows never raise, continue on single-idea failure (FR-019/FR-020); no
secrets in source/logs (constitution IV).

**Scale/Scope**: Per client, ~8–30 ideas/month (client-configured); 2 flows, 1 shared engine, 2 new task
modules (`songbird_tasks.py`, `openrouter_tasks.py`), 1 new Postgres table, 1 hashmap addition
(`CLIENT_SOCIAL`), 2 deployments, small docker-compose env additions, 1 signal-capture insertion into the
existing social-harvest engine.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Evaluated against `.specify/memory/constitution.md` v1.0.0:

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Workflow Orchestration via Prefect | ✅ Pass | All logic is Prefect flows/tasks. Two async flows returning `Dict[str, Any]` with `start_time/end_time/data/summary/error`, never raising; tasks raise for retry. Kebab flow names (`songbird-monthly-plan`, `songbird-generate`); `songbird.*` / `openrouter.chat.complete` task names. Independently runnable via `docker exec prefect python flows/<name>.py`. |
| II. External API Integration Standards | ✅ Pass | Google via the existing OAuth env-var block; API tasks set `retries=2, retry_delay_seconds=30`; randomized delays between OpenRouter calls (reuse `randomized_item_delay`), omitted after the last idea. OpenRouter key from env. No new provider relationship. |
| III. Docker-First Deployment | ✅ Pass | No new service/image — songbird runs in the existing `service/prefect` image (UV, `pyproject.toml`+`uv.lock`, no new dependency). New Postgres table via `config/postgres/init.sql`. New env vars added to the `prefect`/`prefect-worker` compose blocks. |
| IV. Security & Credential Management | ✅ Pass | `OPENROUTER_API_KEY`, `GOOGLE_*`, DB DSN via env only; no secrets logged (log client/counts/target/ids); `SecretStr` where blocks are used. |
| V. Observability & Error Handling | ✅ Pass | Structured INFO/WARNING/ERROR via `get_run_logger`; continue on single-idea failure; `end_time` in `finally`; `summary` with produced/failed counts, signal-availability, target; `__main__` writes timestamped JSON to `data/`. |

**Gate result**: PASS. No new abstraction layers or services; the feature is additive within the existing
Prefect service. Complexity Tracking is empty (no violations to justify).

## Project Structure

### Documentation (this feature)

```text
specs/003-songbird-content-generation/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/
│   ├── content-plan-row.md   # Generated-idea → content-plan column contract (draft + live)
│   └── generation-io.md      # OpenRouter generation input/output JSON contract
├── checklists/
│   └── requirements.md  # Spec quality checklist (from /speckit-specify)
└── tasks.md             # Phase 2 output (/speckit-tasks — NOT created here)
```

### Source Code (repository root)

```text
service/prefect/
├── flows/
│   ├── songbird_monthly_plan.py     # "songbird-monthly-plan" flow (scheduled, dated, draft|live)
│   ├── songbird_generate.py         # "songbird-generate" flow (manual, standalone draft-only)
│   └── common/
│       └── songbird.py              # shared engine: signals → prompt → generate → rows → deliver
├── tasks/
│   ├── songbird_tasks.py            # songbird.client.context, songbird.signal.top-performers,
│   │                                #   songbird.config.quantity (read Clients sheet)
│   ├── openrouter_tasks.py          # openrouter.chat.complete (+ object_schema/array_schema helpers)
│   ├── social_tasks.py              # + social.signal.record (populate harvested_signals)
│   └── google_tasks.py              # reused as-is (sheets read/append/ensure, drive folder ensure)
├── flows/common/social_harvest.py   # + best-effort social_signal_record call at delivery point
├── hashmap.py                       # + CLIENT_SOCIAL (client → {own[], competitors[]})
└── tests/
    ├── test_songbird.py             # engine: date distribution, column align, degrade, continue-on-fail
    └── test_openrouter_tasks.py     # payload build, JSON extraction, 429 back-off (mocked httpx)

config/postgres/
└── init.sql                         # + harvested_signals table (idempotent)

docker-compose.yml                   # prefect + prefect-worker: OPENROUTER_API_KEY, OPENROUTER_MODEL,
                                     #   SONGBIRD_DRIVE_PARENT_ID
```

**Structure Decision**: Songbird is additive inside `service/prefect/` and mirrors the social-harvest
layout (thin flow entrypoints + a `flows/common/` engine + single-responsibility tasks). Generation needs
only prompt text + an HTTP call, so — unlike roach — no companion service or new image is justified
(constitution I is satisfied and no Complexity Tracking entry is required). The signal store reuses the
existing `postgres` service and is filled by the already-present social-harvest engine, keeping songbird a
pure *consumer* of harvested data.

## Complexity Tracking

> No constitution violations. No new services, images, or abstraction layers are introduced; the feature
> is additive within the existing Prefect service. Table intentionally empty.

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| — | — | — |
