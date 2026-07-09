# Implementation Plan: Client Knowledge Base

**Branch**: `001-client-knowledge-base` | **Date**: 2026-07-07 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/001-client-knowledge-base/spec.md`

## Summary

Provide a chatbot-driven Client Knowledge Base where team members feed and retrieve structured
client information through the existing AnythingLLM instance. Records are stored in a **structured
PostgreSQL table** (not raw vector documents) so that supersession, current-vs-historical
filtering, and normalized client identity are handled deterministically. AnythingLLM interacts with
the store through a **dedicated Model Context Protocol (MCP) tool server** that exposes a small set
of typed operations (upsert, query-current, query-history, find-client). The LLM performs
natural-language parsing and answer composition; the MCP server guarantees data correctness and
returns only the minimal matched rows, satisfying the token-efficiency requirement.

## Technical Context

**Language/Version**: Python 3.14 (matches existing Prefect service and project standard)

**Primary Dependencies**: MCP Python SDK (`mcp` / FastMCP) for the tool server; `asyncpg` or
`psycopg` for PostgreSQL; `google-api-python-client` + `google-auth` for Google Docs/Sheets
fetching (reuse patterns from `service/prefect/tasks/google_tasks.py`); Pydantic for record
validation.

**Storage**: PostgreSQL 15 (existing `postgres` service). New table `knowledge_records` in the
main application database. Full-text/fuzzy matching via built-in `pg_trgm` (no vector infra
required at the target scale). Embeddings are a documented future option, not in v1.

**Testing**: pytest with a disposable Postgres schema (or testcontainers-style ephemeral DB);
contract tests against the MCP tool schemas.

**Target Platform**: Linux container on Docker Desktop, attached to `dashboard-networks`. MCP
transport resolved to **remote SSE HTTP** (research.md R1): AnythingLLM registers the server by URL
using its compose service name (`http://knowledge-base:8080/sse`) — not `localhost` — with an
`X-API-KEY` header for auth. T013 reload-verifies the pinned image accepts the remote entry.

**Project Type**: Backend service (MCP tool server) + database schema. No standalone UI (chatbot
is the sole interface per FR-012).

**Performance Goals**: Ingestion confirmation < 30 s (SC-001); retrieval answer < 15 s (SC-002);
retrieval token usage ≥ 30% below naive full-document RAG (SC-006).

**Constraints**: Medium scale — dozens of clients, hundreds of current records, years of
superseded history. Retrieval MUST return only current records as primary answers (SC-003) and
must never surface superseded records as current.

**Scale/Scope**: ~4 MCP tools, 1 database table (+ indexes), 1 new Docker service, reuse of
existing Google credentials and AnythingLLM MCP integration.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Evaluated against `.specify/memory/constitution.md` v1.0.0:

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Workflow Orchestration via Prefect | ⚠ Deviation (justified) | Interactive, low-latency chatbot tool-calling is a request/response service, not a batch workflow. Forcing it into Prefect flows would add latency and complexity. See Complexity Tracking. Batch Google-source re-sync (future) SHOULD use Prefect. |
| II. External API Integration Standards | ✅ Pass | Google Docs/Sheets fetched via existing OAuth env vars (`GOOGLE_CLIENT_ID/SECRET/REFRESH_TOKEN`); rate-limit/retry patterns reused from `google_tasks.py`. |
| III. Docker-First Deployment | ✅ Pass | New `knowledge-base` service added to `docker-compose.yml` with UV, health check, and network attachment. |
| IV. Security & Credential Management | ✅ Pass (with remediation) | New service reads all secrets from env vars; no secrets in code. **Remediation noted**: existing `anythingllm_mcp_servers.json` contains hardcoded Google secrets — plan includes moving them to env references. |
| V. Observability & Error Handling | ✅ Pass | Structured logging with INFO/WARNING/ERROR levels; MCP tools return typed errors; ingestion returns success/failure summaries. |

**Gate result**: PASS with one justified deviation (Principle I) documented in Complexity Tracking.

## Project Structure

### Documentation (this feature)

```text
specs/001-client-knowledge-base/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/
│   └── mcp-tools.md     # MCP tool contracts (Phase 1 output)
├── checklists/
│   └── requirements.md  # Spec quality checklist (from /speckit-specify)
└── tasks.md             # Phase 2 output (/speckit-tasks — NOT created here)
```

### Source Code (repository root)

```text
service/knowledge-base/
├── Dockerfile              # UV-based container, runs the MCP server (SSE/HTTP transport)
├── pyproject.toml          # UV package manifest
├── uv.lock                 # Locked dependencies
├── main.py                 # MCP server entry point (transport + tool registration)
├── server.py               # FastMCP server + tool definitions
├── db.py                   # PostgreSQL connection pool + query helpers
├── models.py               # Pydantic models (KnowledgeRecord, query args/results)
├── repository.py           # Data-access layer: upsert w/ supersession, query, history, client match
├── ingestion.py            # Google Docs/Sheets fetch + text normalization helpers
└── tests/
    ├── test_repository.py  # Supersession, normalization, history retrieval
    ├── test_tools.py       # MCP tool contract tests
    └── test_ingestion.py   # Google source parsing helpers

config/postgres/
└── init.sql                # Extended with knowledge_records table + pg_trgm + indexes
```

**Structure Decision**: A new backend service `service/knowledge-base/` mirrors the existing
`service/prefect/` layout (UV, Dockerfile, modular files) per constitution III. It is a standalone
MCP tool server rather than a Prefect flow because it serves synchronous chatbot tool calls. The
PostgreSQL table lives in the existing `postgres` service; schema is created via the existing
`config/postgres/init.sql` bootstrap plus an idempotent migration for existing databases.

## Complexity Tracking

> Fill ONLY if Constitution Check has violations that must be justified

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| New service is an MCP tool server, not a Prefect flow (Principle I) | Chatbot ingestion/retrieval is synchronous request/response with a <15 s latency budget; AnythingLLM invokes tools directly via MCP. Prefect flows are async/batch-oriented and add orchestration latency and a UI-run indirection that does not fit interactive tool calls. | Wrapping each tool call in a Prefect flow was rejected: it adds per-call scheduling overhead, breaks the MCP stdio/SSE contract, and provides no retry/observability benefit that the MCP server cannot provide itself. Prefect remains the chosen tool for future *batch* Google-source re-sync. |
| Structured Postgres table instead of AnythingLLM native vector/document RAG | Supersession (client+subject), current-vs-historical filtering, normalized client identity, and audit trail are relational constraints that vector similarity cannot enforce; dumping documents into the vector store would surface superseded content as current, violating SC-003. | Pure vector RAG rejected: cannot deterministically exclude superseded records or filter by time period, and returns whole chunks (higher token cost) rather than minimal structured rows. |
