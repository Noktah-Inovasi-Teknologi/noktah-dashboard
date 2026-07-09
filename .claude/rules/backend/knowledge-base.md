# Knowledge Base Service Development Rules

## Overview

The Knowledge Base service (`service/knowledge-base/`) is an MCP (Model Context Protocol) tool
server that gives the AnythingLLM chatbot structured read/write access to the Client Knowledge
Base. Unlike the Prefect service, it is **not** a workflow orchestrator — it serves synchronous,
low-latency tool calls over SSE HTTP transport. See
`specs/001-client-knowledge-base/` for the full spec, plan, data model, and tool contracts.

## Architecture Principles

### Why not Prefect

This service intentionally does not use Prefect. Chatbot ingestion and retrieval are
request/response operations with a tight latency budget (<15s for retrieval); Prefect's async
flow orchestration is designed for batch automation and would add latency with no benefit here.
Prefect remains the right tool for future *batch* work (e.g., scheduled Google Sheet re-sync) —
see `specs/001-client-knowledge-base/plan.md` → Complexity Tracking for the full justification
against the project constitution's Prefect-first principle.

### Service Structure

- **db.py** — PostgreSQL connection pool, DSN built from env vars only
- **models.py** — Pydantic models matching the MCP tool contracts exactly (`contracts/mcp-tools.md`)
- **repository.py** — data-access layer: normalization, atomic upsert-with-supersession, fuzzy
  client matching (`pg_trgm`), current/historical queries
- **ingestion.py** — Google Docs/Sheets fetch (reuses the OAuth refresh-token pattern from
  `service/prefect/blocks/google_credentials.py`, scoped read-only)
- **server.py** — FastMCP tool registration + the `{ok, error, code}` error contract
- **main.py** — SSE HTTP transport entrypoint (see Transport below)
- **agent/workspace-prompt.md** — the AnythingLLM workspace system prompt that drives the
  confirm-before-save conversational flow; paste this into the target workspace's settings

## Data Model

Single table `knowledge_records` (see `specs/001-client-knowledge-base/data-model.md`).
Supersession is **append + link**: updating a client+subject never deletes the old row, it sets
`superseded_by` on it and inserts a new current row. Key correctness details:

- The partial unique index `uq_current_client_subject` (`WHERE superseded_by IS NULL`) enforces at
  most one current record per (client_key, subject_key), and is checked **immediately** (not
  deferred) — the new row's id must be generated client-side and the old row updated *before* the
  new row is inserted, or the insert will transiently violate the index.
- The `superseded_by` foreign key **must** be `DEFERRABLE INITIALLY DEFERRED` to allow that
  ordering (it references a row that doesn't exist yet until COMMIT).
- `SELECT ... FOR UPDATE` cannot lock a not-yet-existing row, so concurrent first-inserts for the
  same (client_key, subject_key) can still race past each other. `repository.upsert_record`
  retries on `UniqueViolationError` rather than surfacing it to the caller.

Do not "simplify" the upsert transaction ordering without re-running
`service/knowledge-base/tests/test_repository.py::test_single_current_record_invariant_holds_under_concurrent_upserts`
— it is a regression test for exactly this race.

## Transport (MCP / AnythingLLM integration)

- Runs **SSE HTTP transport** (not stdio, not streamable-HTTP) on `0.0.0.0:8080`, path `/sse`.
  See `specs/001-client-knowledge-base/research.md` R1 for why SSE was chosen over streamable-HTTP.
- Registered in AnythingLLM via `service/anythingllm/storage/plugins/anythingllm_mcp_servers.json`
  as a **remote** entry using the Docker **service name** (`http://knowledge-base:8080/sse`), never
  `localhost` — inside the AnythingLLM container, `localhost` resolves to AnythingLLM itself.
- FastMCP's default DNS-rebinding protection only allows localhost Host headers; `main.py` widens
  `mcp.settings.transport_security.allowed_hosts/allowed_origins` since this is an internal-only
  Docker network. The `X-API-KEY` header (checked by a raw ASGI middleware, not
  `BaseHTTPMiddleware`, which would buffer/break SSE streaming) is the real auth boundary.
- The plugins JSON does not support `${VAR}` env interpolation — the `X-API-KEY` header value must
  be pasted in literally, matching `KB_MCP_API_KEY`. The file is gitignored, so this is safe.

## Testing

Tests run against a **real disposable PostgreSQL container**, not mocks — supersession atomicity
and the partial-unique-index race are exactly the kind of bug that only reproduces against a real
database. See `service/knowledge-base/README.md` for the local test-DB setup. Google API calls are
mocked (`test_ingestion.py`); the database is not.

## Security

- All credentials via env vars only (`KB_DATABASE_URL`, `KB_MCP_API_KEY`,
  `GOOGLE_CLIENT_ID`/`SECRET`/`REFRESH_TOKEN`) — no secrets in source, per constitution IV.
- Known pre-existing finding: `service/anythingllm/storage/plugins/anythingllm_mcp_servers.json`
  contains a plaintext Google OAuth client secret/refresh token for the separate `google-drive` MCP
  entry. The file is gitignored and untracked (not a version-control exposure), but rotating those
  tokens and moving them to env-var injection remains a recommended follow-up
  (`specs/001-client-knowledge-base/research.md` R7).
