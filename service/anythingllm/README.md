# AnythingLLM Service

Self-hosted AI chat / RAG (retrieval-augmented generation) platform, running the official [Mintplex Labs](https://github.com/Mintplex-Labs/anything-llm) Docker image.

## Quick Start

```bash
# Copy the runtime env template (first time only)
cp .env.example .env
# then edit .env and set JWT_SECRET (openssl rand -hex 32)

# From the repo root, start all services
docker-compose up -d anythingllm

# Check status
docker-compose ps anythingllm
```

### Access
- **UI**: http://localhost:3001 (override host port with `ANYTHINGLLM_PORT` in the root `.env`)

## Configuration

`service/anythingllm/.env` is bind-mounted directly into the container at `/app/server/.env`. Unlike the root `.env`, AnythingLLM treats this as live application state — once you configure an LLM provider, vector database, or embedder from the web UI, it writes those settings back into this file. Don't hand-edit it while the container is running.

## Persistent Data

| Host path | Container path | Purpose |
|-----------|-----------------|---------|
| `storage/` | `/app/server/storage` | Vector cache, documents, SQLite DB |
| `collector/hotdir/` | `/app/collector/hotdir` | Drop-in folder for document ingestion |
| `collector/outputs/` | `/app/collector/outputs` | Processed document output |

These directories are gitignored (only `.gitkeep` placeholders are tracked).

## Container Management

```bash
# Restart
docker-compose restart anythingllm

# View logs
docker-compose logs -f anythingllm

# Pull latest image and recreate
docker-compose pull anythingllm
docker-compose up -d anythingllm
```
