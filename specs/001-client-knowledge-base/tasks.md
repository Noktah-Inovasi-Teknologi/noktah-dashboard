---
description: "Task list for Client Knowledge Base implementation"
---

# Tasks: Client Knowledge Base

**Input**: Design documents from `specs/001-client-knowledge-base/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/mcp-tools.md, quickstart.md

**Tests**: Included — plan.md and quickstart.md define a pytest suite (repository, tools, ingestion).

**Organization**: Tasks are grouped by user story to enable independent implementation and testing.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1, US2, US3, US4)
- All paths are repository-relative.

## Path Conventions

New service lives at `service/knowledge-base/`; database schema in `config/postgres/`.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project scaffold, container, and database schema

- [X] T001 Create service directory structure `service/knowledge-base/` (with `tests/`) per plan.md
- [X] T002 Create `service/knowledge-base/pyproject.toml` with dependencies (mcp/FastMCP, asyncpg, google-api-python-client, google-auth, pydantic, pytest) and pytest config
- [X] T003 [P] Create `service/knowledge-base/Dockerfile` (UV multi-stage) mirroring `service/prefect/Dockerfile` — runs the MCP server over **SSE HTTP transport** listening on `0.0.0.0:8080` (path `/sse`) per research.md R1
- [X] T004 [P] Add `knowledge-base` service to `docker-compose.yml` (attach `dashboard-networks`, `depends_on: postgres`, env vars for Postgres + Google OAuth + `KB_MCP_API_KEY`, healthcheck, resource limits). Reachable in-cluster as `http://knowledge-base:8080`; publishing a host port is optional (in-cluster access is via service-name DNS, not `localhost`)
- [X] T005 Extend `config/postgres/init.sql` to create `pg_trgm` + `pgcrypto` extensions and the `knowledge_records` table with all columns per data-model.md
- [X] T006 [P] Add indexes to `config/postgres/init.sql`: partial unique `uq_current_client_subject`, `ix_client_current`, `ix_client_timestamp`, and gin_trgm indexes on `client_key`/`subject`/`information` per data-model.md
- [X] T007 [P] Create idempotent migration `config/postgres/migrations/001_knowledge_records.sql` for existing databases (guarded `CREATE EXTENSION/TABLE/INDEX IF NOT EXISTS`)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core infrastructure that ALL user stories depend on

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [X] T008 Implement `service/knowledge-base/db.py`: async PostgreSQL connection pool + query helpers, DSN built from env vars (no secrets in code, per constitution IV)
- [X] T009 [P] Implement `service/knowledge-base/models.py`: Pydantic models for `KnowledgeRecord` and all tool argument/return shapes per contracts/mcp-tools.md
- [X] T010 Implement normalization helpers in `service/knowledge-base/repository.py` (`client_key = lower(trim, collapse-whitespace)`, `subject_key = lower(trim)`) per data-model.md
- [X] T011 Implement `service/knowledge-base/server.py` (FastMCP server, tool registration skeleton, standard error-contract wrapper `{ok,error,code}`) and `service/knowledge-base/main.py` (**SSE HTTP transport** entrypoint on `0.0.0.0:8080`, validating the `X-API-KEY` header against `KB_MCP_API_KEY`) per contracts/mcp-tools.md and research.md R1. Verified live: built the Docker image, ran it against a real Postgres, and confirmed `/health` (200), unauthenticated `/sse` (401), and authenticated `/sse` (200, `text/event-stream`) all behave correctly. Also had to disable FastMCP's DNS-rebinding host-check (only supports exact/`base:*` host patterns, not Docker service names) — documented in main.py and the rules file.
- [X] T012 [P] Configure structured logging (INFO/WARNING/ERROR) and non-sensitive error handling in `service/knowledge-base/server.py` per constitution V
- [X] T013 Register the `knowledge-base` MCP server in `service/anythingllm/storage/plugins/anythingllm_mcp_servers.json` as a remote SSE entry (`"type":"sse"`, `"url":"http://knowledge-base:8080/sse"`, `"headers":{"X-API-KEY":"<shared-key>"}`). Config written and documented (README: paste the real key in, since the file doesn't support `${VAR}` interpolation). **Manual step remaining**: reload in the live AnythingLLM UI and confirm it shows connected — not possible to verify from this session (no running AnythingLLM instance with real Google OAuth attached here). A direct MCP client (not AnythingLLM) was used instead to confirm the server itself lists all 5 tools and executes them correctly over SSE.

**Checkpoint**: MCP server boots, connects to Postgres, appears connected in AnythingLLM with empty tool set

---

## Phase 3: User Story 1 - Feed Knowledge Base via Chatbot (Priority: P1) 🎯 MVP

**Goal**: Team members submit plain-text or Google Docs/Sheets content; the chatbot creates
structured records and confirms the save.

**Independent Test**: Submit a plain-text client note via chat and verify a structured record is
created with the correct fields (quickstart V1); share a Google link and verify records created (V2).

### Tests for User Story 1 ⚠️

- [X] T014 [P] [US1] Repository create + normalization tests in `service/knowledge-base/tests/test_repository.py`
- [X] T015 [P] [US1] Tool contract tests for `kb_find_client` and `kb_upsert_record` (create path) in `service/knowledge-base/tests/test_tools.py`
- [X] T016 [P] [US1] Google source fetch/normalization tests (mocked Google API) in `service/knowledge-base/tests/test_ingestion.py`

### Implementation for User Story 1

- [X] T017 [US1] Implement `repository.upsert_record` in `service/knowledge-base/repository.py` — create + dedupe + supersession, atomically in one transaction: insert the new current row and, when a current row already exists for (client_key, subject_key) with different information, set that prior row's `superseded_by` to the new row's id; identical information is a dedupe no-op (R6). No two-current window (FR-010 folded in). **Correctness fix found during testing**: the new row's id must be pre-generated and the old row updated *before* the new row is inserted (the partial unique index is checked immediately, not deferred), which required making the `superseded_by` FK `DEFERRABLE INITIALLY DEFERRED`; a retry-on-`UniqueViolationError` loop handles the remaining race when no current row exists yet for concurrent first-inserts. See data-model.md's "Implementation note" and `test_single_current_record_invariant_holds_under_concurrent_upserts`.
- [X] T018 [US1] Implement `repository.find_client` in `service/knowledge-base/repository.py`: exact `client_key` match + `pg_trgm` close-match suggestions with record counts (FR-003b)
- [X] T019 [US1] Implement `service/knowledge-base/ingestion.py`: fetch Google Docs/Sheets (all tabs) via existing OAuth env vars, reusing patterns from `service/prefect/tasks/google_tasks.py`; return normalized text
- [X] T020 [US1] Implement `kb_find_client` MCP tool in `service/knowledge-base/server.py` per contract
- [X] T021 [US1] Implement `kb_upsert_record` MCP tool in `service/knowledge-base/server.py`: validation, `source_reference` required for Google sources, returns `created`/`superseded`/`unchanged` with `superseded_record_id` (FR-003, FR-003a, FR-004, FR-010)
- [X] T021a [US1] Author the AnythingLLM workspace system prompt / agent instructions in `service/knowledge-base/agent/workspace-prompt.md` (version-controlled) that orchestrate the confirm-before-save flow: call `kb_find_client` and require user confirmation of the client on a close match (FR-003b); propose a moderate-granularity `subject` and let the user confirm/adjust before calling `kb_upsert_record` (FR-003a); relay the confirmation summary (FR-004)
- [X] T022 [US1] Shape ingestion confirmation summary payload returned by `kb_upsert_record` for the LLM to relay (FR-004)

**Checkpoint**: US1 independently testable — records can be created from text and Google sources with confirmation

---

## Phase 4: User Story 2 - Query the Knowledge Base via Chatbot (Priority: P1) 🎯 MVP

**Goal**: Team members ask questions and receive accurate, current-only answers with minimal tokens.

**Independent Test**: Insert a known record, ask a question answered by it, verify correct answer;
verify superseded records never surface and unknown clients return a "no information" response
(quickstart V3, V6).

### Tests for User Story 2 ⚠️

- [X] T023 [P] [US2] Repository `query_current` tests (excludes superseded, ranking, empty result) in `service/knowledge-base/tests/test_repository.py`
- [X] T024 [P] [US2] Tool contract test for `kb_query_current` in `service/knowledge-base/tests/test_tools.py`

### Implementation for User Story 2

- [X] T025 [US2] Implement `repository.query_current` in `service/knowledge-base/repository.py`: filter `superseded_by IS NULL`, optional subject filter, `pg_trgm` ranking of question/subject against `subject`+`information`, `limit` (FR-005, FR-006, FR-007, SC-003)
- [X] T026 [US2] Implement `kb_query_current` MCP tool in `service/knowledge-base/server.py`: returns minimal structured records, `found:false` when none exist so the LLM states no info is available (FR-011)

**Checkpoint**: MVP complete (US1 + US2) — ingest and retrieve current knowledge end-to-end

---

## Phase 5: User Story 3 - Query Client Historical Data (Priority: P2)

**Goal**: Team members retrieve past/superseded knowledge with time period and supersession context.

**Independent Test**: Insert a superseded record with a past timestamp, query for the period, verify
it returns labelled `superseded` with a pointer to the superseding record (quickstart V5).

### Tests for User Story 3 ⚠️

- [X] T027 [P] [US3] Repository `query_history` tests (status labels, time range, superseded pointer + timestamp) in `service/knowledge-base/tests/test_repository.py`
- [X] T028 [P] [US3] Tool contract test for `kb_query_history` in `service/knowledge-base/tests/test_tools.py`

### Implementation for User Story 3

- [X] T029 [US3] Implement `repository.query_history` in `service/knowledge-base/repository.py`: optional subject + `since`/`until` range, include superseded rows with `status` and `superseded_by`(+timestamp), ordered by timestamp desc (FR-008, FR-009)
- [X] T030 [US3] Implement `kb_query_history` MCP tool in `service/knowledge-base/server.py` per contract

**Checkpoint**: US3 independently testable — historical retrieval with time/supersession context

---

## Phase 6: User Story 4 - Mark a Record as Superseded (Priority: P2)

**Goal**: Verify and harden the supersession behavior (implemented atomically in T017/T021):
submitting updated info for an existing client+subject supersedes the prior record while preserving
full history.

**Independent Test**: Create a record, submit an update for the same client+subject, verify the old
record is marked superseded and the new one is current (quickstart V4, V8).

> **Note**: The supersession + dedupe logic itself lands in T017 (repository) and T021 (tool) during
> US1, since it is a single atomic upsert function. This phase adds the dedicated tests and edge-case
> hardening that prove the invariant, keeping US4 independently verifiable.

### Tests for User Story 4 ⚠️

- [X] T031 [P] [US4] Supersession atomicity + single-current-per-(client,subject) invariant tests in `service/knowledge-base/tests/test_repository.py`
- [X] T032 [P] [US4] Tests for `kb_upsert_record` returning `superseded` (+`superseded_record_id`) and `unchanged` dedupe in `service/knowledge-base/tests/test_tools.py`

### Implementation for User Story 4

- [X] T033 [US4] Harden supersession in `service/knowledge-base/repository.py` (logic implemented in T017): verify single-transaction atomicity under concurrent upserts, confirm the partial unique index holds, and cover edge cases (self-reference guard, whitespace-variant subjects) (FR-010). Concurrent-upsert test initially caught a real `UniqueViolationError` race (no row to `FOR UPDATE`-lock on first insert); fixed with a bounded retry loop — see T017 note.
- [X] T034 [US4] Verify `kb_upsert_record` return contract (`created`/`superseded`/`unchanged` + `superseded_record_id`) matches contracts/mcp-tools.md; add any missing status handling surfaced by T032

**Checkpoint**: All user stories independently functional

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Documentation, security remediation, and end-to-end validation

- [X] T035 [P] Create `service/knowledge-base/README.md` (setup, MCP registration, env vars) and update root `README.md` services table
- [X] T036 [P] Add a `.claude/rules/backend/` note (or extend CLAUDE.md) documenting the knowledge-base MCP service conventions
- [ ] T037 Security remediation (research R7): move hardcoded Google OAuth secrets in `service/anythingllm/storage/plugins/anythingllm_mcp_servers.json` to env references, rotate exposed tokens, and confirm the file is gitignored. **Status**: confirmed the file is gitignored and untracked by git (`git check-ignore` + `git ls-files` both verified) — no version-control exposure, so this is no longer a release blocker. Rotating the live Google OAuth tokens requires Google Cloud Console access this session does not have; documented as a recommended follow-up in README/rules/research.md R7 instead of actioned.
- [ ] T038 Run all quickstart.md validation scenarios V1–V8 in AnythingLLM and record results. **Status**: protocol-level validation done from this session — built the real Docker image, ran it against a real Postgres, and used a real MCP client (not AnythingLLM) to list all 5 tools and execute `kb_upsert_record` → `kb_query_current` end-to-end successfully. This substantiates the tool layer behind V1/V3/V4/V6/V8. **Not verified**: the actual AnythingLLM chat UI experience, the LLM-driven confirm-before-save conversation (workspace-prompt.md behavior), and live Google Docs/Sheets fetch (only mocked in tests) — these need manual validation against the running AnythingLLM instance with real Google credentials.
- [ ] T039 [P] Verify performance targets: retrieval latency < 15 s (SC-002) and token usage ≥ 30% below naive full-document RAG (SC-006) via spot-check. **Status**: not measured — requires a live LLM conversation to count tokens and wall-clock a real chat round-trip. Structurally, `kb_query_current` returns only a handful of matched rows (filtered + `pg_trgm`-ranked) rather than whole documents, which is designed to satisfy SC-006, but this needs a real measurement pass against AnythingLLM.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately
- **Foundational (Phase 2)**: Depends on Setup — BLOCKS all user stories
- **User Stories (Phase 3–6)**: All depend on Foundational
  - US1 (P1) and US2 (P1) form the MVP; US2 retrieval is best validated with US1 ingestion in place
  - US3 (P2) depends only on Foundational (can be built independently once records exist)
  - US4 (P2) verifies/hardens the supersession logic already implemented in US1 (T017/T021); its tests (T031/T032) depend on that logic
- **Polish (Phase 7)**: Depends on all targeted user stories

### User Story Dependencies

- **US1**: Foundational only. Transport is resolved (SSE HTTP, service-name URL — research.md R1); T013 only reload-verifies the pinned image accepts the remote entry
- **US2**: Foundational only (independently testable by seeding a record directly)
- **US3**: Foundational only (independently testable by seeding a superseded record directly)
- **US4**: Verifies US1's `kb_upsert_record`/`repository.upsert_record` (T017/T021); independently testable via supersession + history linkage

### Within Each User Story

- Tests written first and expected to FAIL before implementation
- Repository (data-access) before MCP tool
- Core implementation before confirmation/summary shaping

### Parallel Opportunities

- Setup: T003, T004, T006, T007 in parallel (distinct files) after T001/T002
- Foundational: T009 and T012 parallel with T008/T010/T011 where files differ
- All test tasks within a story ([P]) run in parallel before that story's implementation
- Once Foundational completes, US1/US2/US3 can be staffed in parallel; US4 follows US1

---

## Parallel Example: User Story 1

```bash
# Tests for US1 together (different files):
Task: "Repository create + normalization tests in service/knowledge-base/tests/test_repository.py"
Task: "Tool contract tests for kb_find_client and kb_upsert_record in service/knowledge-base/tests/test_tools.py"
Task: "Google source fetch tests (mocked) in service/knowledge-base/tests/test_ingestion.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 + User Story 2)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational (CRITICAL — blocks all stories)
3. Complete Phase 3: US1 (ingestion) → validate V1/V2
4. Complete Phase 4: US2 (current retrieval) → validate V3/V6
5. **STOP and VALIDATE**: ingest + retrieve works end-to-end — deliverable MVP

### Incremental Delivery

1. Setup + Foundational → foundation ready
2. US1 + US2 → MVP (ingest + retrieve current)
3. US3 → historical retrieval
4. US4 → automatic supersession + dedupe
5. Polish → docs, security remediation, performance validation

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps each task to its user story for traceability
- Verify tests fail before implementing
- Commit after each task or logical group
- All secrets via env vars only (constitution IV); no secrets in source
- Supersession is append + link — old rows are never deleted (constitution: audit trail; FR-008)
