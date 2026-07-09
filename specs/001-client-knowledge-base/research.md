# Phase 0 Research: Client Knowledge Base

This document resolves the open decisions the specification deferred to planning: retrieval
approach, whether Prefect is used, storage layout for historical data, and the AnythingLLM
integration mechanism.

## R1. Integration mechanism — how AnythingLLM reads/writes the knowledge base

**Decision**: Build a dedicated **MCP (Model Context Protocol) tool server** that AnythingLLM
connects to, exposing typed operations for ingestion and retrieval.

**Rationale**:
- AnythingLLM already supports MCP servers — `service/anythingllm/storage/plugins/anythingllm_mcp_servers.json`
  shows a working `google-drive` MCP integration, so the pattern is proven in this deployment.
- Tool calls let the LLM pass/receive **structured arguments** (client, subject, information), which
  is exactly the record shape the spec requires, and return **only matched rows** — directly
  satisfying the token-efficiency requirement (FR-007, SC-006).
- Keeps knowledge in a controlled relational store instead of AnythingLLM's opaque vector cache,
  so supersession and current/historical filtering are enforceable.

**Alternatives considered**:
- *Native AnythingLLM document upload + vector RAG*: rejected — cannot enforce supersession or
  exclude stale records; returns whole chunks (higher token cost); no structured fields.
- *Direct DB access from a custom AnythingLLM fork*: rejected — invasive, breaks upgrade path of
  the official image.

**Transport (T000 — RESOLVED 2026-07-07)**: Run the MCP server as its own container on
`dashboard-networks` and register it in AnythingLLM **by URL**. AnythingLLM's remote-MCP support is
confirmed via the official docs (docs.anythingllm.com/mcp-compatibility/overview): the same
`plugins/anythingllm_mcp_servers.json` file accepts **remote** entries alongside the existing stdio
entry. Chosen config shape:

```json
{
  "mcpServers": {
    "knowledge-base": {
      "type": "sse",
      "url": "http://knowledge-base:8080/sse",
      "headers": { "X-API-KEY": "<shared-internal-key>" },
      "anythingllm": { "autoStart": true }
    }
  }
}
```

Key facts and decisions:
- **Both `sse` and `streamable` remote transports are supported**; `type` omitted defaults to `sse`.
  Remote entries require `url`; optional `headers` carry auth. Stdio entries use `command`/`args`/`env`.
- **Docker networking**: the `url` MUST use the compose **service name** (`http://knowledge-base:<port>`),
  NOT `localhost` — inside the AnythingLLM container `localhost` is AnythingLLM itself. Both containers
  already share `dashboard-networks`, so service-name DNS resolves. `host.docker.internal` is only
  needed for servers running on the host, which this is not.
- **Transport choice: prefer `type: "sse"` for v1.** A logged bug (Mintplex-Labs/anything-llm #4411,
  Desktop v1.8.5) had AnythingLLM sending a GET that returned HTTP 405 against a *streamable* server.
  SSE is the more mature/default path here. The KB server will implement the standard MCP HTTP
  transport (support both if cheap) but we register it as `sse` first and only switch to `streamable`
  if needed.
- **Auth / secrets (addresses analysis finding I2)**: authenticate with a shared `X-API-KEY` header.
  The plugins JSON stores literal values (no env interpolation), but that file is **gitignored**
  (`.gitignore:116 service/anythingllm/storage/*`), so the internal key living there is acceptable;
  the KB *server* reads its expected key from an env var and compares. No secret enters source control.
- **Version caveat**: this deployment runs `mintplexlabs/anythingllm:latest` (Docker), and stdio MCP
  already works here (google-drive). Remaining verification during T013 is only that this pinned image
  accepts a *remote* entry — reload the server in the UI and confirm "connected" + tools listed.
- **Fallback**: if remote transport misbehaves on the running image, ship a thin stdio launcher
  (`command`/`args`) that AnythingLLM spawns; the server core is unchanged, only the transport wrapper.

**Net for T000**: remote SSE/HTTP is viable → the containerized design in `plan.md` stands. No pivot
to a stdio-only design is required; T003/T004/T011/T013 proceed with the SSE URL registration above.

## R2. Retrieval approach — token-efficient and accurate

**Decision**: **Structured filter first, fuzzy text match second.** Filter rows by normalized
`client_name` (and optional `subject`) and `superseded_by IS NULL` for current queries, then rank
candidate rows within that client using PostgreSQL `pg_trgm` similarity against the question /
subject. Return the top few full records (not document chunks) to the LLM.

**Rationale**:
- At medium scale (hundreds of current records, a handful of subjects per client) a structured
  filter reduces the candidate set to a few rows before any text ranking — minimal tokens, high
  precision.
- `pg_trgm` ships with stock PostgreSQL 15 (no new image or extension server needed) and handles
  typo/lexical variation well for short subjects and questions.
- Returning whole structured records (short `information` bodies) keeps the LLM context small while
  preserving accuracy (SC-006: ≥30% token reduction vs. naive full-document RAG).

**Alternatives considered**:
- *Vector embeddings (pgvector / AnythingLLM embedder)*: deferred to a future option. The
  `multilingual-e5-small` embedder is already present in AnythingLLM, so embeddings are feasible
  later if recall proves insufficient, but they add infrastructure (pgvector image or an embed
  service) that is unjustified at v1 scale.
- *Full-text `tsvector`*: viable but weaker on short/typo'd subjects than trigram similarity;
  `pg_trgm` chosen for fuzzy client/subject matching which also backs FR-003b.

## R3. Storage layout — one table vs. separate historical store

**Decision**: **Single table** `knowledge_records`. Historical/superseded data lives in the same
table; `superseded_by` (nullable FK to the newer record) distinguishes current from historical.

**Rationale**:
- Supersession is a self-referential link; keeping current and historical rows together makes the
  supersession update a single-row `UPDATE` and preserves the audit trail without cross-store joins.
- At medium scale there is no performance need to partition history into a separate store.
- Current queries filter `superseded_by IS NULL`; historical queries drop that filter and use the
  timestamp range — both are simple indexed predicates.

**Alternatives considered**:
- *Separate `knowledge_history` table*: rejected for v1 — adds write-time copy logic and dual-store
  query complexity with no scale benefit. Revisit only if history volume grows by orders of
  magnitude.

## R4. Prefect usage

**Decision**: **Not used in the v1 core.** Interactive ingestion and all retrieval run through the
MCP server synchronously. Prefect is **reserved for a future batch capability**: scheduled re-sync
of large Google Sheets/Docs, where its retry/observability and cron scheduling add value.

**Rationale**:
- The chatbot flows are request/response with a tight latency budget (SC-001/SC-002); Prefect's
  async orchestration would add latency and a UI-run indirection with no benefit.
- The constitution's Prefect-first principle applies to *batch automation*; this is documented as a
  justified deviation in `plan.md` → Complexity Tracking.

**Alternatives considered**:
- *Prefect flow per tool call*: rejected — see Complexity Tracking.

## R5. Google Docs/Sheets ingestion path

**Decision**: The MCP server fetches Google Docs/Sheets content on demand using the **existing
Google OAuth credentials** (`GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REFRESH_TOKEN`),
reusing the auth + read patterns from `service/prefect/tasks/google_tasks.py`. The **LLM performs
extraction** (splitting content into client/subject/information records) and calls the upsert tool
with structured arguments; the MCP server validates and persists.

**Rationale**:
- Keeps the MCP server thin (fetch + CRUD + query) and leverages the LLM for parsing, which is
  where natural-language extraction belongs — token-efficient and simple (constitution: simplicity).
- Reuses proven Google integration code and credentials already configured in the project.

**Alternatives considered**:
- *Reuse the existing `google-drive` MCP server for fetching*: possible, but that server's OAuth
  tokens are hardcoded in JSON (see R7) and its tool surface is broad; a focused fetch in our
  service is cleaner and centralizes KB logic.
- *Deterministic server-side parsing*: rejected for v1 — brittle across arbitrary doc layouts; the
  LLM handles heterogeneous documents better.

## R6. Large documents, duplicates, multi-tab sheets (edge cases)

**Decision**:
- **Large docs**: the LLM chunks its own extraction into multiple `kb_upsert_record` calls per
  subject; the server imposes no single-pass size limit beyond a per-call `information` length cap.
- **Duplicates**: an upsert with identical `client_name`+`subject`+`information` to the current
  record is a **no-op** (returns "unchanged") rather than creating a redundant supersession.
- **Multi-tab sheets**: the server returns all tabs' content to the LLM; the LLM decides how to map
  tabs/rows to subjects.

**Rationale**: Pushes heterogeneous-layout judgement to the LLM while keeping the server's
correctness guarantees (idempotent upsert, supersession) deterministic.

## R7. Security remediation (pre-existing)

**Finding**: `service/anythingllm/storage/plugins/anythingllm_mcp_servers.json` contains
**plaintext Google OAuth client secret, refresh token, and access token**. This violates
constitution Principle IV (no secrets in stored config).

**Decision**: Out of scope to fully rework the google-drive MCP here, but the plan **flags** this
and the new `knowledge-base` service will source all credentials from environment variables only.
Recommend a follow-up task to rotate the exposed tokens and move them to env references. Verify the
plugins file is gitignored; if tracked, remove it from version control.

## Summary of resolved unknowns

| Spec-deferred question | Resolution |
|------------------------|------------|
| Retrieval approach | Structured filter + `pg_trgm` fuzzy match; embeddings deferred (R2) |
| Prefect usage | Not in v1 core; reserved for future batch sync (R4) |
| Historical store layout | Single table, `superseded_by` distinguishes current/historical (R3) |
| AnythingLLM integration | Dedicated MCP tool server over SSE/HTTP (R1) |
| Google ingestion | On-demand fetch via existing OAuth; LLM extracts, server persists (R5) |
