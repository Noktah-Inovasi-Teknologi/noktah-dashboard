# Tasks: Noktah Hub v1: Registry, Client Card and Intake

**Input**: Design documents from `specs/008-hub-registry-client-card/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/ (hub-api.md, intake-ai.md, ui-screens.md), quickstart.md

**Tests**: included where the spec or project rules require them:
- permissions (SC-009)
- quote check (SC-004)
- history and approval invariants against real Postgres (`.claude/rules/backend/schema.md`)
- the UI sweep for every page

**Organization**: phases follow user-story dependencies. US1 (roles) and US3 (Card) come before US2 (Intake), which writes to the Card. All three are P1.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: the user story from spec.md (US1–US6)

---

## Phase 1: Setup

- [X] T001 Add Hub env to `.env.example` and `docker-compose.yml`:
  - the `api` service gets `HUB_API_INTERNAL_TOKEN`, `OPENROUTER_API_KEY`, `HUB_INTAKE_MODEL`, `HUB_SUMMARY_MODEL`, `HUB_AI_MONTHLY_CAP_USD`, `GOOGLE_CLIENT_ID/SECRET/REFRESH_TOKEN`, `SLACK_MANAGERIAL_ESKALA`, `SLACK_MANAGERIAL_NOKTAH` and `SLACK_AUTOMATION_NOKTAH`
  - `prefect` and `prefect-worker` get `HUB_API_INTERNAL_TOKEN` and `HUB_API_URL=http://api:8000`
- [X] T002 Add API dependencies (httpx, google-api-python-client, google-auth, pypdf, pypdfium2) in `service/api/pyproject.toml`, and `uv lock`
- [X] T003 [P] Write the card definition v1 in `config/hub/card_v1.yaml` (10 Profil fields, 10 Guideline sections, choice lists, always-banned list; data-model.md), and mount `./config/hub` read-only into `api` in `docker-compose.yml`
- [X] T004 [P] Create the API package layout: `service/api/app/{registry,people,card,requests,intake,summary,ai}/__init__.py`

---

## Phase 2: Foundational (blocks every story)

- [X] T005 Write migration `config/postgres/migrations/010_hub_registry_card.sql`, following the schema.md contract:
  - all tables in data-model.md
  - the additive `clients` columns
  - partial unique indexes and CHECKs
  - seeds: `noktah_brands` Eskala and Venyu; People and emails `core@noktah.co` (Owner), `bagas@noktah.co` (Brand Manager, Venyu), `defila@noktah.co` (Brand Manager, Eskala), per G-15
- [X] T006 Write `config/postgres/migrations/010_hub_registry_card.down.sql` (drop the new tables and new `clients` columns in reverse order)
- [X] T007 Mirror migration 010 in `config/postgres/init.sql`, and extend `script/verify_schema_parity.sh` and `service/prefect/tests/test_schema_parity.py` to include `010`
- [X] T008 Apply migration 010 to `noktah_dashboard` after a rehearsal (`script/db_rehearsal.sh create`), and run `script/verify_schema_parity.sh`
- [X] T009 [P] Extend settings (internal token, OpenRouter, models, cap, Google, Slack env names, card definition path) in `service/api/app/settings.py`
- [X] T010 [P] Add error types, the error-envelope handler (codes from contracts/hub-api.md) and a `version`-conflict helper in `service/api/app/errors.py`
- [X] T011 Resolve the verified email to a Person and active roles (403 `no_access` when there is no Manager role) in `service/api/app/auth.py`
- [X] T012 [P] Write the pure permission function `can(assignments, action, brand)`, implementing the G-9/G-10 table (research R1), in `service/api/app/permissions.py`
- [X] T013 [P] Write exhaustive permission tests (every role × action × same/other Noktah Brand) in `service/api/tests/test_permissions.py`
- [X] T014 Add the internal router guard (token required; refuses `cf-ray`/`cf-connecting-ip`) in `service/api/app/internal.py`, with tests in `service/api/tests/test_internal_guard.py`
- [X] T015 [P] Add a registry change-log helper (`record_change(conn, entity, …)`) in `service/api/app/registry/changes.py`
- [X] T016 [P] Build the OpenRouter client (strict JSON schema, `finish_reason` check before parsing, one retry with the validator error, usage cost) in `service/api/app/ai/openrouter.py`
- [X] T017 [P] Build the AI ledger and monthly cap (check before each call; one 80% alert per month to `SLACK_AUTOMATION_NOKTAH`) in `service/api/app/ai/budget.py`, with tests in `service/api/tests/test_budget.py`
- [X] T018 [P] Add Slack notices (approval pending → brand webhook; best-effort, never blocking) in `service/api/app/notify.py`
- [X] T019 Add a card-definition loader and sync (YAML → `card_definitions`; refuses to change a frozen version) in `service/api/app/card/definition.py`
- [X] T020 [P] Set up real-Postgres test fixtures (fresh DB with migrations applied; skipped when unreachable) in `service/api/tests/conftest.py`
- [X] T021 Add the web catch-all proxy `service/web/server/api/hub/[...path].ts` (forwards method, query and body through `hubApi()`), and extend `server/utils/hubApi.ts` to pass through 402/409 and the `no_access` code
- [X] T022 [P] Build the dashboard layout (`UDashboardGroup`, collapsible sidebar, role-aware nav from `/v1/me`) in `service/web/app/layouts/default.vue`, with a `useMe()` composable in `service/web/app/composables/useMe.ts`
- [X] T023 [P] Extend the fixture framework for all API paths and states (`full`, `empty`, `down`, `pending`, `no_access`, `failed`, `cap`) in `service/web/server/fixtures/`

**Checkpoint**: migration applied and parity verified, and the API resolves roles. The web app has its layout and proxy.

---

## Phase 3: User Story 1: Sign in and see only what your role allows (P1) 🎯

**Goal**: roles decide what each Person sees and does; a Person without a role sees nothing.
**Independent Test**: sign in as each seeded role and as a no-role email; check visibility and the 403s (quickstart §2, SC-009).

- [X] T024 [US1] Add `GET /v1/me` (Person, roles, visible Noktah Brands, `can` flags) in `service/api/app/main.py` and `service/api/app/people/routes.py`
- [X] T025 [US1] Scope Client reads to the caller's Noktah Brands (404 for others) in `service/api/app/registry/routes.py` (`GET /v1/clients`, `GET /v1/clients/{id}`)
- [X] T026 [P] [US1] Build the "Anda belum punya akses" page, and redirect on 403 `no_access`, in `service/web/app/pages/no-access.vue` and `service/web/app/middleware/access.global.ts`
- [X] T027 [P] [US1] Rebuild the client list page (scoped list, completeness column placeholder, status filter) in `service/web/app/pages/index.vue`
- [X] T028 [US1] Add fixtures and UI sweep routes for `/` and `/no-access` in `service/web/server/fixtures/` and `service/web/e2e/ui-sweep.e2e.ts`
- [X] T029 [US1] Real-Postgres tests: a no-role Person gets 403 on every `/v1` route; another Noktah Brand's Client gets 404. In `service/api/tests/test_access.py`

**Checkpoint**: US1 is independently demonstrable (after the Cloudflare policy change in T079).

---

## Phase 4: User Story 3: Read and maintain a Client Card (P1)

**Goal**: the four-part card, with history, approvals, completeness and Summary.
**Independent Test**: quickstart §5 plus acceptance scenarios US3-1…5.

- [X] T030 [US3] Write the card value service in `service/api/app/card/values.py`: current/pending/superseded/corrected transitions (research R2), `card_version` concurrency, and completeness
- [X] T031 [US3] Add Card routes in `service/api/app/card/routes.py`:
  - `GET /v1/card-definition`
  - `GET /v1/clients/{id}/card`
  - `PUT …/card/{part}/{field}`
  - `POST …/correct`
  - `GET …/history`
  - `POST …/guideline/{field}/copy-from`
- [X] T032 [US3] Add Approval routes (`GET /v1/approvals`, `POST /v1/approvals/{id}/approve|reject`; Owner fallback per G-12) and the Slack notice on pending, in `service/api/app/card/approvals.py`
- [X] T033 [P] [US3] Add Request routes (list, create, status change with event row; `ditolak` needs a reason) in `service/api/app/requests/routes.py`
- [X] T034 [US3] Write Summary generation (confirmed parts only; fingerprint; refresh only when stale and older than 24 h; respects the cap) in `service/api/app/summary/service.py`, and `POST /internal/summaries/refresh` in `service/api/app/internal.py`
- [X] T035 [P] [US3] Add a Prefect flow `hub-summary-refresh` (hourly, `alert_hooks("noktah")`) in `service/prefect/flows/hub_summary_refresh.py`, plus a deployment in `service/prefect/prefect.yaml`
- [X] T036 [US3] Real-Postgres tests in `service/api/tests/test_card_history.py`:
  - one current value per field
  - approval swap
  - correction keeps history
  - 409 on a stale `card_version`
  - PM Guideline edit → pending
  - BM edit → current
  - no deletes
- [X] T037 [US3] Build the Client page shell with tabs, and the Ringkasan tab, in `service/web/app/pages/clients/[id]/index.vue` and `service/web/app/components/client/SummaryTab.vue`
- [X] T038 [P] [US3] Build the Profil tab in `service/web/app/components/client/ProfilTab.vue` and `service/web/app/components/card/FieldCard.vue`: field cards, edit/correct forms, history slideover, expired badge, required highlight
- [X] T039 [P] [US3] Build the Guideline tab in `service/web/app/components/client/GuidelineTab.vue` and `service/web/app/components/card/GuidelineSection.vue`:
  - the 10 sections with helper text
  - archetype select
  - 1–5 sliders
  - pending state
  - copy-from slideover
  - approve/reject for the Brand Manager and Owner
- [X] T040 [P] [US3] Build the Permintaan tab (list, filter, add, status change) in `service/web/app/components/client/RequestsTab.vue`
- [X] T041 [P] [US3] Build the approvals page in `service/web/app/pages/approvals.vue`
- [X] T042 [US3] Add fixtures and UI sweep routes for `/clients/[id]` (full, empty, down, pending) and `/approvals`

---

## Phase 5: User Story 2: Update a Client from what they said or sent (P1)

**Goal**: pasted text, screenshots, Google Docs and PDFs become Proposals that a Manager accepts.
**Independent Test**: quickstart §3 (SC-001, SC-004).

- [X] T043 [P] [US2] Write the source readers (text normalisation; image validation up to 5 MB; Google Doc fetch; PDF text via pypdf; scanned pages via pypdfium2, max 10) in `service/api/app/intake/sources.py`
- [X] T044 [P] [US2] Write the deterministic checks in `service/api/app/intake/checks.py`: quote-in-text with WhatsApp-prefix stripping, PIC token match, price unit/conditions, other-client names, contradiction, not-Bahasa
- [X] T045 [P] [US2] Write check tests, including a fabricated-quote set (SC-004), in `service/api/tests/test_intake_checks.py`
- [X] T046 [US2] Write the Intake prompt and schema (`intake_v1`, contracts/intake-ai.md) in `service/api/app/intake/prompt.py`
- [X] T047 [US2] Write the Intake pipeline (hash cache, cap check, AI call, validation, drop patient data, flags, store Proposals, classified failures) in `service/api/app/intake/pipeline.py`
- [X] T048 [US2] Add Intake routes in `service/api/app/intake/routes.py`:
  - `POST /v1/clients/{id}/intakes`
  - `GET /v1/intakes/{id}`
  - `GET /v1/clients/{id}/intakes`
  - `POST /v1/proposals/{id}/decide`, which enforces ticks, requires `edit` for `unverified`, creates the Request, and applies the Guideline approval path
  - `GET /v1/ai/usage`
- [X] T049 [US2] Add raw-material purge (older than 12 months; excerpts kept) as `POST /internal/intakes/purge-raw` in `service/api/app/internal.py`, and a daily Prefect flow `hub-intake-purge` in `service/prefect/flows/hub_intake_purge.py` plus `prefect.yaml`
- [X] T050 [US2] Make one live verification call: an image Intake on the configured model. Record the result and cost in research.md R4, and split `HUB_INTAKE_VISION_MODEL` only if images fail
- [X] T051 [US2] Real-Postgres tests in `service/api/tests/test_intake_decide.py`: ticks enforced, unverified needs edit, Request plus fact from one paste, cache returns the earlier Intake, cap returns 402
- [X] T052 [US2] Build the Intake page (input tabs, loading, AI usage meter, Proposal cards with diff, flags, ticks and buttons) in `service/web/app/pages/clients/[id]/intake.vue` and `service/web/app/components/intake/ProposalCard.vue`
- [X] T053 [US2] Add fixtures and UI sweep routes for `/clients/[id]/intake` (full, empty, down, failed, cap)

---

## Phase 6: User Story 4: Manage the Registry (P2)

**Goal**: the Hub is the only place to edit Clients, teams and accounts; the sheet copies follow within 5 minutes.
**Independent Test**: quickstart §1 and §4 (SC-005, SC-008).

- [X] T054 [US4] Add Registry routes in `service/api/app/registry/routes.py`:
  - `POST /v1/clients`
  - `PATCH /v1/clients/{id}`
  - `PUT …/team/{role}`
  - `POST/PATCH …/accounts`
  - `GET …/history`

  All are change-logged, `version`-checked, and keep `is_active` in sync with `status`.
- [X] T055 [US4] Write the import in `service/api/app/registry/importer.py`: Clients tab, Hashmaps and the database; matching reused from roster rules; sheet wins; Eskala internal; People from WORKERS; team from CE/FA; difference report; validate-only
- [X] T056 [US4] Add `POST /internal/registry/import` in `service/api/app/internal.py`, which pauses the `roster-sync` deployment through the Prefect API on a real run
- [X] T057 [P] [US4] Add a Prefect flow `hub-registry-import` (manual, `--validate-only`) in `service/prefect/flows/hub_registry_import.py`, plus `prefect.yaml`
- [X] T058 [US4] Write the sheet copy in `service/api/app/registry/sheet_copy.py`: desired Clients/Hashmaps cells from the Registry, diff against live, write only changed managed cells, header note, internal Clients excluded, check mode with an overwritten list, and a **dry run** returning the cells it would write without writing (constitution: validate-only for external writes)
- [X] T059 [US4] Add `POST /internal/sheet-sync?check=&dry_run=` in `service/api/app/internal.py`, and a Prefect flow `hub-sheet-sync` (every 5 min; Monday 06:00 check; `--validate-only` → dry run) in `service/prefect/flows/hub_sheet_sync.py`, plus `prefect.yaml`
- [X] T060 [P] [US4] Write pure tests for the sheet diff (managed columns only; rename via `sheet_row_name`; internal excluded; unmanaged columns untouched) in `service/api/tests/test_sheet_copy.py`
- [X] T061 [US4] Run the import in validate-only mode against live data; review the difference report with the user before the real run
- [X] T062 [US4] Run the real import, then confirm 22 Clients plus Eskala, the seeded roles, and `roster-sync` paused (SC-005)
- [X] T062a [US4] Write migration `config/postgres/migrations/011_client_brand_required.sql` (+ `.down.sql`, init.sql mirror, parity): `CHECK (noktah_brand_id IS NOT NULL) NOT VALID` then `VALIDATE`, applied after T062 (schema.md pattern; FR-005)
- [X] T063 [P] [US4] Build the Registry tab (status/quotas/folders form, 5 team selects from People, social accounts) and the Riwayat tab in `service/web/app/components/client/RegistryTab.vue` and `service/web/app/components/client/HistoryTab.vue`
- [X] T064 [US4] Add fixtures and UI sweep coverage for the Registry and Riwayat tabs

---

## Phase 7: User Story 5: Manage people and roles (P2)

**Goal**: roles are granted in the Hub; People have several emails.
**Independent Test**: US5 acceptance scenarios.

- [X] T065 [US5] Add People routes in `service/api/app/people/routes.py`:
  - list, create, update, mark left (ends roles and team assignments)
  - add/remove emails (unique; 422 names the owner)
  - grant/end roles (BM only in their own Noktah Brand, never brand_manager or owner; Owner appoints BMs)
- [X] T066 [P] [US5] Real-Postgres tests for the role-granting rules and email uniqueness in `service/api/tests/test_people.py`
- [X] T067 [P] [US5] Build the People pages (list, detail with emails, roles, IDs, mark left, history) in `service/web/app/pages/people/index.vue` and `service/web/app/pages/people/[id].vue`
- [X] T068 [US5] Add fixtures and UI sweep routes for `/people` and `/people/[id]`

---

## Phase 8: User Story 6: Bring the old notes in (P3)

**Goal**: all 95 old notes become Proposals; unmatched ones wait in "belum ada klien".
**Independent Test**: quickstart §6.

- [X] T069 [US6] Write old-note processing in `service/api/app/intake/notes.py`: batches of 10, idempotent by record id, match by `client_id` then token-subset, unmatched → null Client, `not_bahasa` flag, `tidak_masuk_kartu`, stop at the cap, and a **dry run** reporting notes in scope, matched/unmatched counts and **projected model spend** with zero model calls (constitution: costed batches)
- [X] T070 [US6] Add `POST /internal/notes/process`, and `GET /v1/notes/unmatched`, `POST /v1/notes/{id}/assign|discard`, in `service/api/app/internal.py` and `service/api/app/intake/routes.py`
- [X] T071 [P] [US6] Add a Prefect flow `hub-notes-process` (manual, `--limit`, `--dry-run`) in `service/prefect/flows/hub_notes_process.py`, plus `prefect.yaml`
- [X] T072 [P] [US6] Build the Catatan lama page (progress, unmatched list, client picker) in `service/web/app/pages/notes.vue`
- [X] T073 [US6] Add fixtures and UI sweep routes for `/notes`
- [X] T074 [US6] Run note processing on the live notes (after T062): **dry run first** (projected spend), then the real run; report processed, matched and unmatched counts and actual vs projected cost

---

## Phase 9: Polish and cross-cutting

- [X] T075 Run the full `ui-sweep` (every page, every state), fix findings by rule, and look at the screenshots (skill Step 3)
- [X] T076 [P] Update `.claude/CLAUDE.md` (Hub section: flows, internal endpoints, env, import and sheet copy, roles), `service/api/README.md` and `service/web/README.md`
- [X] T077 [P] Update `.claude/rules/backend/schema.md` with the migration 010 notes (card history invariants, the pending index, why `clients.noktah_brand_id` is enforced later)
- [X] T078 Register the Prefect deployments (`prefect deploy -n "hub-*"`), and check that each hub flow runs once and carries `alert_hooks`
- [ ] T079 Guide the user through the ops steps:
  - add the two managerial Slack webhooks
  - change the Cloudflare Access policy for `hub.noktah.co` to *Include: Everyone*
  - verify from outside that a no-role sign-in sees only `/no-access`
- [ ] T080 Run the quickstart end to end (§1–§8) and record the results, including the paste-to-saved time for SC-001
- [X] T081 **With the user's explicit go-ahead at this step** (G-33), remove AnythingLLM entirely:
  - the `anythingllm` and `knowledge-base` services in `docker-compose.yml`, their containers and images
  - the `service/anythingllm/` and `service/knowledge-base/` directories
  - `.claude/rules/backend/knowledge-base.md`, and every CLAUDE.md mention
  - the `chat.noktah.co` tunnel hostname (a user step)

  Keep the `knowledge_records` table. Done 2026-09-25: containers, images, directories, compose
  services, rules file, docs and the tunnel hostname removed; the AnythingLLM database is backed
  up outside the repo.
- [X] T082 Open the PR(s) for spec 008 through the push gate (UI sweep clean), with only this feature's files staged

---

## Dependencies & Execution Order

- **Setup (T001–T004)** → **Foundational (T005–T023)**. Migration T005–T008 comes first, and T011 depends on T005.
- **US1 (T024–T029)** depends on Foundational.
- **US3 (T030–T042)** depends on Foundational and uses US1's scoping (T025).
- **US2 (T043–T053)** depends on US3 (T030 card values, T032 approvals, T033 requests).
- **US4 (T054–T064)** depends on Foundational. The import (T061–T062) is best run before US6 and before the pages are demonstrated with real data.
- **US5 (T065–T068)** depends on Foundational. It can run in parallel with US4.
- **US6 (T069–T074)** depends on US2 (the pipeline) and US4 (Clients imported).
- **Polish (T075–T082)**: T081 comes last, after US2 and US6 are live.

## Parallel Examples

- **Foundational:** T009, T010, T012–T013, T015–T018, T020, T022 and T023 touch different files.
- **US3:** T033 (requests), T038/T039/T040/T041 (web components).
- **US2:** T043, T044 and T045 in parallel; then T046 → T047 → T048.
- **US4 and US5:** can proceed side by side once the Foundational phase is done.

## Implementation Strategy

1. **MVP:** US1 plus US3 plus US2 (roles, the Card, Intake). This replaces AnythingLLM's actual use, and is demonstrable with the database's existing 21 Clients.
2. **Then:** US4 (the import and sheet copy make the Hub the only editor), US5, US6.
3. **Then:** Polish, ending with the AnythingLLM removal after the go-ahead.
4. Each phase ends at a checkpoint, with its tests and a UI sweep for its pages.
