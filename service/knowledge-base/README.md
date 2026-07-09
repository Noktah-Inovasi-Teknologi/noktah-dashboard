# Client Knowledge Base — MCP Tool Server

MCP (Model Context Protocol) tool server that lets the AnythingLLM chatbot feed and retrieve
structured client information. See [specs/001-client-knowledge-base/](../../specs/001-client-knowledge-base/)
for the full spec, plan, data model, and tool contracts.

## What it does

- Stores client knowledge in a PostgreSQL table (`knowledge_records`), not a vector store, so
  supersession and current-vs-historical filtering are deterministic.
- Exposes 5 tools to AnythingLLM: `kb_find_client`, `kb_upsert_record`, `kb_query_current`,
  `kb_query_history`, `kb_fetch_google_source`.
- Updating information for the same client+subject automatically supersedes the prior record
  while preserving full history — nothing is ever deleted.

## Local development

```bash
cd service/knowledge-base
uv sync
uv run pytest -v
```

Tests require a disposable PostgreSQL instance. Point `KB_TEST_DATABASE_URL` at one, e.g.:

```bash
docker run -d --name kb-test-postgres -e POSTGRES_PASSWORD=testpass -e POSTGRES_DB=kbtest \
  -p 15432:5432 postgres:15.14-alpine
docker exec -i kb-test-postgres psql -U postgres -d kbtest \
  < ../../config/postgres/migrations/001_knowledge_records.sql

KB_TEST_DATABASE_URL="postgresql://postgres:testpass@localhost:15432/kbtest" uv run pytest -v
```

## Running in Docker

```bash
docker-compose up -d --build knowledge-base
docker-compose logs -f knowledge-base
```

The server listens on SSE HTTP transport, `0.0.0.0:8080`, path `/sse`. It is **not** published to
the host by default — it's reachable in-cluster at `http://knowledge-base:8080` (compose service
name), which is also the URL AnythingLLM must use (not `localhost`).

## Environment variables

| Variable | Required | Description |
|----------|----------|--------------|
| `KB_DATABASE_URL` | yes | Postgres DSN (set automatically in docker-compose from `POSTGRES_*`) |
| `KB_MCP_API_KEY` | recommended | Shared secret checked against the `X-API-KEY` header on every request except `/health` |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` / `GOOGLE_REFRESH_TOKEN` | for Google ingestion | Reused from the project's existing Google OAuth setup |

Add `KB_MCP_API_KEY` to the root `.env` file (generate with `openssl rand -hex 32`).

## Registering with AnythingLLM

1. Ensure `KB_MCP_API_KEY` is set in the root `.env` and the service is running.
2. Edit `service/anythingllm/storage/plugins/anythingllm_mcp_servers.json` and set the
   `knowledge-base` entry's `headers.X-API-KEY` to the **same value** as `KB_MCP_API_KEY`.
   This file does not support `${VAR}` interpolation — the value must be pasted in literally.
   The file is gitignored, so this is safe to do directly.
3. In the AnythingLLM UI, reload the MCP servers list and confirm `knowledge-base` shows as
   connected with 5 tools listed.
4. Paste [agent/workspace-prompt.md](./agent/workspace-prompt.md) into the target workspace's
   system prompt so the chatbot follows the confirm-before-save flow.

If the pinned AnythingLLM image rejects the remote SSE entry, see
[research.md R1](../../specs/001-client-knowledge-base/research.md) for the stdio-launcher fallback.

## Manual end-to-end validation

Automated tests cover the repository, tool contracts, and Google-fetch logic (31 tests, run
against a real disposable Postgres — see above). They do **not** exercise the live AnythingLLM
chat UI or real LLM token usage. Run the scenarios in
[quickstart.md](../../specs/001-client-knowledge-base/quickstart.md) (V1–V8) manually in
AnythingLLM to validate the end-to-end chatbot experience and the SC-001/SC-002/SC-006
performance targets.
