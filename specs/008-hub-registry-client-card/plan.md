# Implementation Plan: Noktah Hub v1: Registry, Client Card and Intake

**Branch**: `master` (via PRs) | **Date**: 2026-09-25 | **Spec**: [spec.md](spec.md) | **Grill ledger**: [grill.md](grill.md)

**Input**: Feature specification from `specs/008-hub-registry-client-card/spec.md`

## Summary

The Hub becomes the managers' home for the **Registry**, **People and roles**, and each Client's **Client Card**, which has four parts: Profil, Guideline, Riwayat Permintaan, and Ringkasan. The Card is filled through AI **Intake**, which turns pastes, screenshots, Google Docs and PDFs into Proposals a Manager accepts.

The work is split across the three services:

- **`hub-api`** (FastAPI, `service/api`) holds all Hub logic.
  - It is the only writer of Hub data.
  - It enforces roles on every request.
  - It runs the AI: Intake, Summaries and old-note processing.
  - It writes the read-only sheet copies.
- **`service/web`** (Nuxt on Cloudflare Workers) renders pages. It proxies to the API through one catch-all server route.
- **Prefect** runs the recurring and one-time work on schedules. It calls the API's internal endpoints for:
  - the sheet copy, every 5 min
  - Summary refresh, hourly
  - raw-material purge, daily
  - the one-time import and old-note processing

One new migration (`010_hub_registry_card.sql`) adds the people, role, Card, Request, Summary, Intake and change-log tables. Its only change to an existing table is additive columns on `clients`.

## Technical Context

**Language/Version**:
- Python 3.14 for `hub-api` and the Prefect flows
- TypeScript on Bun 1.4, with Nuxt 4 and Nuxt UI 4, for `service/web`

**Primary Dependencies**:
- API: FastAPI, asyncpg, pydantic, PyJWT
  - httpx, for OpenRouter and Slack webhooks
  - google-api-python-client and google-auth, for Docs and Sheets
  - pypdf (PDF text) and pypdfium2 (scanned-PDF pages to images; Apache-2.0, chosen over AGPL PyMuPDF)
- Web: Nuxt UI dashboard components (`UDashboardGroup`, `UDashboardSidebar`, `UTable`, `UForm`), `@nuxt/ui` locale `id`

**Storage**: Postgres 15 (`noktah_dashboard`)
- New tables through migration 010, mirrored in `init.sql` and verified by `script/verify_schema_parity.sh`.
- Card definition: a versioned YAML file, `config/hub/card_v1.yaml`, synced into a table. Once a Card value references a version, that version is frozen, like the extraction vocabulary.

**Testing**:
- **API:** pytest.
  - Pure logic runs without a database: quote check, PIC match, permissions, cost cap, sheet-copy diff.
  - Real-Postgres tests (marker `schema`, same pattern as `service/prefect/tests`) cover history invariants, approvals and concurrency.
- **Web:** bun test for UI conventions, plus Playwright UI sweep (fixture mode) for every new page.
- **Prefect flows:** existing pytest suite.

**Target Platform**:
- `hub-api` and Prefect run in Docker on the office PC (Windows, Docker Desktop).
- The web app runs on Cloudflare Workers.

**Project Type**: Web application. The web front end and API service are separate deployables.

**Performance Goals**:
- Intake Proposals are shown within ~30 s of submit for text, and ~60 s for images and PDFs.
- Page data loads in under 1 s from the PC.

**Constraints**:
- The web app never touches the database or OpenRouter.
- Every data request is role-checked.
- AI spend is capped at USD 5 a month.
- Raw Intake material is kept for 12 months.
- Nothing else is deleted.
- The UI is in Bahasa.
- The API gets only the secrets it uses.

**Scale/Scope**:
- 2 Noktah Brands, about 23 Clients, 10–15 People, and fewer than 10 Managers.
- About 40 Intakes a month.
- 95 old notes processed once.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design (below).*

| Principle | Status | How |
|---|---|---|
| **I. Prefect orchestration** | ✅ with justification | Recurring and batch automation are Prefect flows (`hub-sheet-sync`, `hub-summary-refresh`, `hub-intake-purge`, `hub-registry-import`, `hub-notes-process`); each returns the standard dict and carries `alert_hooks()`. The API is request/response, like the knowledge-base service: see Complexity Tracking. |
| **II. External API standards** | ✅ | Google APIs use the existing refresh-token pattern. Sheets writes are batched (one `batchUpdate` per tab per run). OpenRouter model is configuration per call site (`HUB_INTAKE_MODEL`, `HUB_SUMMARY_MODEL`). Every call is attributed (`call_site`, `client`), the schema is validated, invalid output is retried once with the error, then marked failed. Nothing unvalidated is stored. |
| **III. Docker-first** | ✅ | `hub-api` already in compose with a health check; dependencies locked with uv. |
| **IV. Security** | ✅ | Secrets from env only. Roles are enforced in the API on every `/v1` route. `/internal` routes need `HUB_API_INTERNAL_TOKEN` and **refuse any request that came through Cloudflare** (it carries `cf-ray`/`cf-connecting-ip`), so they're reachable only on the Docker network. No secrets in logs. |
| **V. Observability** | ✅ | Every flow is alert-hooked to `#noktah-otomasi`. Intake failures are recorded with a classified reason (`validation_failed`, `truncated`, `timeout`, `provider_error`, `cap_reached`, `unreadable`). The sheet copy never fails silently (G-7). |
| **VI. Data honesty** | ✅ | The Summary is marked "dibuat otomatis" and is never a source (G-27). Screenshot Proposals are marked "cek manual". Unverified quotes are flagged. Monthly performance numbers from old notes are *not* forced into the Card ("tidak masuk kartu"); no reach metric is stored. |
| **VII. Append-only observations** | ✅ (analogous) | Card values and Request statuses are append-only history rows; corrections add rows (G-32). |
| **VIII. Evidence discipline** | n/a | No effect estimates in this feature. |
| **IX. Identity chain** | n/a | No attribution in this feature. |
| **X. Polite collection** | n/a | No collection. |
| **XI. Model call governance** | ✅ | Rules come first, AI second: quote check, PIC match, price-unit check and client-name check are **deterministic**, and the model only drafts. Content hash cache: an identical Intake for the same Client returns the earlier result. The monthly ceiling is **enforced** (G-6). Marginal cost: research §R4. A human approves everything. |
| **XII. Versioned vocabularies, additive schema** | ✅ | Card definition and choice lists are versioned and frozen once used (G-29). Migration 010 is additive only; `init.sql` is mirrored; parity is verified. |
| **Data Management (hashmap.py)** | ⚠️ noted, not violated | `hashmap.py` stays the automations' source, because the sheet copy keeps it correct. Retiring it is deferred (A-1) and will need an amendment then. |
| **Deferred register** | ✅ | The spec's Inherited & Deferred section accounts for every in-scope row (none). The plan defers **S-1** (songbird reading the Client Card), added to the register with this spec as origin. |

**Gate: PASS.**

## Project Structure

### Documentation (this feature)

```text
specs/008-hub-registry-client-card/
├── spec.md · grill.md · plan.md (this file)
├── research.md          # Phase 0
├── data-model.md        # Phase 1
├── quickstart.md        # Phase 1
├── contracts/
│   ├── hub-api.md       # HTTP contract of hub-api (/v1 and /internal)
│   ├── intake-ai.md     # AI prompt/response contract for Intake and Summary
│   └── ui-screens.md    # Hub pages, states, role visibility
└── tasks.md             # Phase 2 (/speckit-tasks)
```

### Source Code (repository root)

```text
config/
├── hub/card_v1.yaml                         # Card definition v1 (Profil, Guideline, choice lists)
└── postgres/
    ├── init.sql                             # mirrors migration 010
    └── migrations/010_hub_registry_card(.down).sql

service/api/                                 # hub-api (FastAPI) — ALL Hub logic
├── app/
│   ├── main.py            # app, routers, lifespan
│   ├── settings.py        # env (DB, AUDs, OpenRouter, Google, Slack, internal token, caps)
│   ├── auth.py            # Access JWTs (existing) + person/role resolution
│   ├── permissions.py     # pure: what a role may do on which Noktah Brand
│   ├── db.py
│   ├── registry/          # clients, team, social accounts, change log, import, sheet copy
│   ├── people/            # people, emails, roles
│   ├── card/              # definition loader, values + history, approvals, copy-from, completeness
│   ├── requests/          # client requests + status history
│   ├── intake/            # submit, sources (text/image/gdoc/pdf), AI call, checks, proposals, decide
│   ├── summary/           # summary generation
│   ├── ai/                # OpenRouter client, cost ledger, monthly cap
│   ├── notify.py          # Slack webhooks (approvals, cap alert)
│   └── internal.py        # /internal routes (token + not-through-Cloudflare guard)
└── tests/                 # pure tests + @pytest.mark.schema real-Postgres tests

service/web/                                 # Nuxt on Cloudflare Workers — pages only
├── app/
│   ├── layouts/default.vue                  # UDashboardGroup + sidebar (role-aware nav)
│   ├── pages/index.vue                      # client list
│   ├── pages/clients/[id]/index.vue         # Client: Profil · Guideline · Permintaan · Ringkasan · Registry · Riwayat
│   ├── pages/clients/[id]/intake.vue        # Intake + Proposal review
│   ├── pages/approvals.vue                  # menunggu persetujuan
│   ├── pages/people/index.vue · [id].vue    # people, emails, roles
│   ├── pages/notes.vue                      # "belum ada klien" + old-note progress
│   └── pages/no-access.vue
├── server/api/hub/[...path].ts              # catch-all proxy → hubApi() (service token + user JWT)
├── server/fixtures/                         # fixture data for every page/state (UI sweep)
└── e2e/ui-sweep.e2e.ts                      # ROUTES extended with every page

service/prefect/flows/
├── hub_sheet_sync.py        # every 5 min → POST /internal/sheet-sync
├── hub_summary_refresh.py   # hourly      → POST /internal/summaries/refresh
├── hub_intake_purge.py      # daily       → POST /internal/intakes/purge-raw
├── hub_registry_import.py   # one-time    → POST /internal/registry/import (validate-only first)
└── hub_notes_process.py     # one-time    → POST /internal/notes/process
```

**Structure Decision**: the two-deployable web application already in place (`service/web` and `service/api`), plus thin Prefect flows. The API holds the logic, so intake, summary and sheet-copy rules live in one codebase, tested once.

## Key design decisions (details in research.md)

1. **Roles in the API** (R1): every `/v1` request resolves email → Person → role assignments. `permissions.py` is a pure function: `(roles, action, noktah_brand) → allow/deny/needs_approval`. It is unit-tested exhaustively against the G-9 table.
2. **Card history as rows** (R2): one row per value.
   - A partial unique index allows at most one `current` value per (Client, field).
   - A Guideline change by a PM/AE is inserted as `pending`. Approval swaps it to `current` and marks the old row `superseded`, in one transaction.
   - Corrections add a row and mark the old one `corrected`.
   - Optimistic concurrency: each write carries the `version` it saw, and a mismatch returns 409 (FR-044).
3. **Intake pipeline** (R3):
   1. normalise the input
   2. hash it: if the same content was already submitted for this Client, return that result
   3. make one model call with a strict JSON schema, retried once on validation failure
   4. run the deterministic checks: quote in text, PIC match, price unit, other-client names, contradiction with current value, patient data
   5. store the Proposals

   Images and scanned PDF pages go to the same model. Their Proposals are marked `from_image`.
4. **Cost cap** (R4): a ledger table of every AI call's cost. Before each call, the month's total is checked. At the 80% crossing, one alert per month goes to `#noktah-otomasi`. At 100%, Intake and Summary refresh stop.
5. **Sheet copy** (R5): the API computes the desired Clients and Hashmaps cell values from the Registry, diffs them against the live sheet, and writes only changed cells in managed columns.
   - It runs when the change log advanced, or every run in "check" mode.
   - Internal Clients are excluded.
   - The Prefect flow runs it every 5 minutes; failures alert.
6. **Import** (R6): a validate-only first pass produces the difference report. The real run writes the Registry and pauses the `roster-sync` deployment, which would otherwise be a second writer of `clients`.
7. **Old notes** (R7): each `knowledge_records` row becomes an Intake of kind `old_note`. It runs through the same pipeline and is matched to a Client by the existing token-subset rule. No confident match means it goes to "belum ada klien" (G-17).
8. **Web proxy** (R8): one catch-all Nuxt server route forwards method, query and body to the API. It adds the service token and the user's Access JWT. Pages use `useFetch('/api/hub/...')`.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| `hub-api` is a request/response service, not Prefect flows (Principle I) | Managers need interactive, sub-second reads and ~30 s Intake round-trips. Prefect's flow orchestration adds latency and has no request model. This is the same justification as the knowledge-base service (spec 001). | Running Intake as a Prefect flow per paste would make the Hub wait on scheduling and the run lifecycle, and would split Card logic across two services. |
| `/internal` API routes with a shared token | Prefect must trigger API jobs (sheet copy, summaries, purge, import, notes) so that Hub logic stays in one codebase. | Putting the logic in Prefect as well would duplicate Card and Intake rules in two services that would drift. |
| Pausing the `roster-sync` deployment | After import, roster-sync would be a second writer of `clients`/`accounts` from the sheet copy, breaking "the Hub is the only place to edit" (G-7). | Leaving it running reintroduces two writers; changing its logic belongs to the switch-over spec (A-1). |
| `hub-api` image is `python:3.14-slim`, not the Prefect base image (Principle III) | The API isn't a Prefect service. It follows the knowledge-base precedent and keeps the image small. | The Prefect image would pull Prefect into a service that never runs a flow. |
| Deferred **S-1**: songbird still reads `knowledge_records`, not the Client Card | Switching songbird's grounding is an automation change (G-1). | Doing it here expands scope into generation quality, which needs its own measurement. |

## Post-design Constitution Re-check

The design artifacts (data-model.md, contracts/) were re-checked against the table above:
- Migration 010 is additive, and history tables are append-only.
- Every AI call site is attributed and capped.
- The web app has no DB or OpenRouter access; the contract routes everything through `/api/hub/**`.
- `/internal` is closed to Cloudflare-routed traffic.

**Gate: PASS.**
