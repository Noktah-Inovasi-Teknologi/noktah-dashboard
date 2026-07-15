# Implementation Plan: Social Profile Content Harvest & Analysis

**Branch**: `002-social-content-harvest` | **Date**: 2026-07-12 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/002-social-content-harvest/spec.md`

## Summary

Turn the scaffolded `roach` scraper into an automated, Prefect-orchestrated capability that
harvests and analyzes content from up to five public Instagram/TikTok profiles per run, then
delivers the downloaded media plus an analysis Google Sheet to Google Drive. Orchestration
(per-profile loop, sequential pacing, randomized delays, rate-limit/challenge back-off, the
100-items/hour cap, cross-run de-duplication, Drive delivery, structured logging, and the
completion notification) lives entirely in **Prefect flows/tasks** per constitution I. The
`roach` service is promoted from an idle scaffold to a small **internal HTTP API** exposing the
platform-specific primitives (`list` / `download` / `analyze`) where the specialized anti-bot and
media dependencies (`yt-dlp[curl-cffi]`, `gallery-dl`, `ffmpeg`) already live. Two flow variants
share the same internals and differ only in depth selection: **most-recent-N** (default N=10) and
**time-window** (default X=7 days). De-duplication state is persisted in a PostgreSQL ledger.

## Technical Context

**Language/Version**: Python 3.14 (project standard; matches Prefect and roach services)

**Primary Dependencies**:
- Orchestration: **Prefect 3.6.9** (flows, tasks, run logging, notification blocks, deployments)
- Video collection path (FR-005): **yt-dlp[default,curl-cffi]** (+ `ffmpeg`) — Reels/TikTok videos
- Non-video collection path (FR-005): **gallery-dl** — image posts, carousels, stories, profile metadata
- Analysis: **OpenRouter** (`xiaomi/mimo-v2.5`, audio+video+image-capable) via `requests`
- Delivery: **google-api-python-client** + **google-auth** — Drive **write** + Sheets (reuse
  `service/prefect/blocks/google_credentials.py` OAuth refresh-token pattern, widened scope)
- roach API: **FastAPI** + **uvicorn** (internal-only, API-key header auth over `dashboard-networks`)
- Prefect↔roach transport: **httpx**

**Storage**:
- Google Drive — delivery target. All output lands under a **configured stable parent folder**
  (`HARVEST_DRIVE_PARENT_ID`); per-profile folders (named after the account) are ensured *inside*
  that parent so their ids are stable across runs (the dedupe anchor, see below). One analysis
  Sheet is created per run under the same parent.
- PostgreSQL 15 (existing `postgres` service) — new `harvested_items` de-dup ledger (FR-021),
  keyed by the **stable per-profile folder id** as `drive_target` (not the per-run Sheet)
- Shared Docker volume `social_data` — ephemeral download staging shared by roach + prefect;
  files are deleted after successful Drive upload (retention sweep on run end)

**Testing**: pytest. Prefect task/flow unit tests with `yt-dlp`/`gallery-dl`/OpenRouter/Drive
mocked (pacing, back-off, hourly-cap, dedupe, per-item continue-on-failure logic); roach API
contract tests against the endpoint schemas in `contracts/roach-api.md`.

**Target Platform**: Linux containers on Docker Desktop, attached to `dashboard-networks`.

**Project Type**: Prefect workflow (orchestration) + companion scraper microservice (`roach`).

**Performance Goals**: ≤ 100 content items collected per rolling 1-hour window (FR-011); sequential
collection with human-like randomized delays (FR-013); ≤ 5 profiles per run (FR-002).

**Constraints**: Public profiles only, no owner-only analytics (FR-003); anonymous-first
collection, burner-account cookie session only where anonymous is blocked (FR-012); a single
blocked/failed profile or item never fails the run or discards collected content (FR-015, FR-020);
no credentials in source or logs (FR-019).

**Scale/Scope**: ≤ 5 profiles/run; default depth N=10 items (recent flow) or X=7 days (window
flow); 2 flows, ~1 new roach API, 1 new Postgres table, Drive write client extension.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Evaluated against `.specify/memory/constitution.md` v1.0.0:

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Workflow Orchestration via Prefect | ✅ Pass | All automation logic (loop, pacing, back-off, hourly cap, dedupe, delivery, notification) is Prefect flows/tasks. Two async flows returning `Dict[str, Any]` with `start_time/end_time/data/summary/error`, never raising to callers; tasks raise for retry. Kebab-case flow names, `api-group.resource.action` task names. Independently runnable via `docker exec prefect python flows/<name>.py`. |
| II. External API Integration Standards | ✅ Pass | Google via OAuth env vars (`GOOGLE_CLIENT_ID/SECRET/REFRESH_TOKEN`); API tasks set `retries=2, retry_delay_seconds=30`; random delays between requests (5–10 s), omitted after last item. New Drive **write** scope documented in research R2. OpenRouter + roach API keys via env. |
| III. Docker-First Deployment | ✅ Pass | `roach` promoted to a served API with a health check + shared volume; both services build via UV from `pyproject.toml`+`uv.lock` (roach base image is `python:3.14-slim`, retained — see Complexity Tracking). Prefect image unchanged. |
| IV. Security & Credential Management | ✅ Pass | All secrets (`GOOGLE_*`, `OPENROUTER_API_KEY`, `ROACH_API_KEY`) from env; `SecretStr` in blocks; `secrets/cookies.txt` gitignored; no secrets logged (log profile/item IDs and counts only). |
| V. Observability & Error Handling | ✅ Pass | Structured INFO/WARNING/ERROR logging via `get_run_logger` (profile, item, counts, delays, back-offs, failures); flow continues on single-item/profile failure; `end_time` set in `finally`; `summary` with success/failure counts; completion notification via Prefect notification block/automation. |

**Gate result**: PASS. One structural choice (companion HTTP microservice instead of doing raw
scraping in-process inside the Prefect container) is documented in Complexity Tracking; it does not
violate any principle (orchestration remains fully Prefect).

## Project Structure

### Documentation (this feature)

```text
specs/002-social-content-harvest/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/
│   ├── roach-api.md     # Internal roach HTTP API contract (list/download/analyze)
│   └── sheet-schema.md  # Analysis Google Sheet column contract
├── checklists/
│   └── requirements.md  # Spec quality checklist (from /speckit-specify)
└── tasks.md             # Phase 2 output (/speckit-tasks — NOT created here)
```

### Source Code (repository root)

```text
service/roach/                     # Promoted from scaffold → internal scraper API
├── Dockerfile                     # + expose port, uvicorn CMD, add gallery-dl
├── pyproject.toml / uv.lock       # + gallery-dl, fastapi, uvicorn
├── api.py                         # FastAPI app: /health, /list, /download, /analyze (API-key header)
├── collect.py                     # video (yt-dlp) + non-video (gallery-dl) collection primitives
├── analyze.py                     # extend: analyze video AND image items (leave N/A fields empty)
├── main.py                        # keep CLI for manual/debug use
└── secrets/cookies.txt            # burner-account IG session (gitignored)

service/prefect/
├── Dockerfile                     # unchanged
├── pyproject.toml / uv.lock       # + httpx (Google write already available)
├── flows/
│   ├── social_harvest_recent.py   # "social-harvest-recent" flow (most-recent-N)
│   └── social_harvest_window.py   # "social-harvest-window" flow (time-window)
├── flows/common/
│   └── social_harvest.py          # shared harvest engine (loop, pacing, cap, backoff, dedupe, deliver)
├── tasks/
│   ├── social_tasks.py            # social.profile.list / social.item.download / social.item.analyze (call roach)
│   ├── google_tasks.py            # + drive.folder.ensure, drive.file.upload, sheets.rows.append
│   └── utility_tasks.py           # + rolling-window limiter + randomized-delay helpers
├── blocks/
│   └── google_credentials.py      # widen scopes (drive.file + spreadsheets) + write client methods
└── tests/
    ├── test_social_harvest.py     # pacing, hourly cap, back-off, dedupe, continue-on-failure
    └── test_social_tasks.py       # roach API task contracts (mocked httpx)

config/postgres/
└── init.sql                       # + harvested_items table (+ idempotent migration)

docker-compose.yml                 # roach: ports/healthcheck; social_data shared volume;
                                   # prefect: ROACH_API_URL/ROACH_API_KEY env
```

**Structure Decision**: Orchestration lives in `service/prefect/` (constitution I); the
platform-specific fetch/analyze primitives stay in `service/roach/`, promoted from an idle
scaffold to a small internal HTTP API. This honors the spec's "extends the existing roach service"
while keeping the specialized anti-bot/media stack (`curl-cffi`, `ffmpeg`, `gallery-dl`) isolated
from the Prefect image. The de-dup ledger reuses the existing `postgres` service (as the
knowledge-base feature did), created via `config/postgres/init.sql` plus an idempotent migration.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| Companion `roach` HTTP microservice called by the flow, instead of running yt-dlp/gallery-dl directly inside the Prefect container | The collection stack needs `curl-cffi` (TLS-fingerprint anti-bot), `ffmpeg`, and `gallery-dl` — a heavy, scraper-specific toolchain. `roach` already exists as a service and README-defers exactly this interface decision. Isolating it keeps the Prefect image lean and lets the scraper toolchain evolve independently. | Installing the full scraper stack into `prefecthq/prefect:3.6.9-python3.14` was rejected: it bloats the orchestrator image, couples Prefect upgrades to scraper-tool upgrades, and discards the existing roach service. All *orchestration* logic still lives in Prefect, so principle I is satisfied either way. |
| roach uses base image `python:3.14-slim`, not constitution III's `prefecthq/prefect:3.6.9-python3.14` | roach is a **non-Prefect** companion (it runs FastAPI + yt-dlp/gallery-dl, no Prefect runtime), so the Prefect base image would pull an unused orchestration stack. Constitution III's base-image clause governs the Prefect runtime; roach mirrors the precedent set by the `knowledge-base` service, which also uses its own base. | Basing roach on the Prefect image was rejected: it ships a Prefect server/agent the service never runs, inflating image size for no benefit. roach still complies with III's UV build, `pyproject.toml`+`uv.lock` layer caching, and health-check requirements. |
| New `harvested_items` PostgreSQL table for cross-run de-duplication (FR-021) | FR-021 requires that re-runs never re-download or duplicate items already collected for a delivery target; this needs durable per-target content-ID state that survives container restarts. | A local JSON manifest or "list existing Drive files" check was rejected: local files are lost on volume reset and Drive listing is slow/fragile and can't record analysis-failure status. Postgres already exists and matches the knowledge-base precedent. |
