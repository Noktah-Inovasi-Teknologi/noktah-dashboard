# Prefect Service

Prefect 3.x workflow orchestration service that reads content plans from Google Sheets and creates Jira issues from them. Runs in Docker via the root `docker-compose.yml`, backed by PostgreSQL for flow-run state.

## Structure

```
service/prefect/
├── flows/
│   ├── content_plan_spreadsheet_to_jira_issue.py  # Pipeline flow + sub-flows (read Sheets -> convert -> create Jira issues)
│   └── common/                                    # Shared flow helpers
├── tasks/
│   ├── google_tasks.py    # Sheets/Drive operations (shared Google rate limiter)
│   ├── jira_tasks.py      # Jira read/write operations (resilient bulk create)
│   └── utility_tasks.py   # Date/text/number formatting, JSON output helpers
├── shared/                # Framework-agnostic shared utilities (unit-testable)
│   ├── rate_limit.py      # AsyncRateLimiter with adaptive backoff-on-429
│   ├── retry.py           # Retryable/terminal errors, Retry-After, backoff runner
│   ├── batching.py        # chunked() / run_in_batches()
│   ├── http.py            # async HTTP wrapper (rate limit + typed errors)
│   ├── jira_api.py        # resilient bulk issue creation (batch + backoff)
│   ├── dates.py           # utc_now_iso(), month helpers
│   └── io.py              # save_json(), run_output_dir()
├── blocks/
│   ├── google_credentials.py  # GoogleCredentials block (OAuth refresh token)
│   └── jira_credentials.py    # JiraCredentials block (Basic auth: email + API token)
├── hashmap.py              # Static mappings: WORKERS, COMPONENTS, CONTENT_EDITOR, FIELD_ASSOCIATE
├── prefect.yaml            # Deployment definitions (work pool: noktah-pool)
├── bootstrap.sh            # Create work pool + register deployments (idempotent)
├── main.py                 # CLI entry point
├── run_google_oauth.py     # One-time Google OAuth setup (local dev)
├── pyproject.toml / uv.lock
├── Dockerfile / .dockerignore
└── data/                   # Timestamped flow output (JSON), gitignored
```

## Setup

Environment variables are read from the `.env` file at the repository root (see `.env.example` there). Required for this service:

```bash
GOOGLE_CLIENT_ID=your_google_client_id
GOOGLE_CLIENT_SECRET=your_google_client_secret
GOOGLE_REFRESH_TOKEN=your_google_refresh_token

JIRA_URL=https://your-domain.atlassian.net
JIRA_USERNAME=your_email@domain.com
JIRA_API_TOKEN=your_jira_api_token   # mapped to JIRA_TOKEN inside the container
```

Google and Jira credentials are normally stored as Prefect Blocks (`google-creds`, `jira-creds`). If a block isn't registered, `GoogleCredentials.load_or_env()` / `JiraCredentials.load_or_env()` fall back to building credentials from the environment variables above, so flows still run without pre-registering blocks.

## Production setup (server + worker + auth)

The stack is **production-only** (no dev bind-mounts): the `prefect` (server) and
`prefect-worker` containers run the code baked into the image. A code change
requires a rebuild: `docker-compose up -d --build prefect prefect-worker`.

```bash
# From the repo root -- start the DB, server and worker
docker-compose up -d --build postgres prefect prefect-worker

# One-time: create the work pool and register deployments (idempotent)
docker exec prefect bash bootstrap.sh
```

**Auth:** the UI/API require basic auth via `PREFECT_API_AUTH_STRING` (`user:pass`
in `.env`). The server port `4200` is bound to `127.0.0.1` only; expose it
publicly by adding a Cloudflare Tunnel public hostname (Zero Trust > Networks >
Tunnels) pointing `prefect.<domain>` -> `http://prefect:4200`, protected with a
Cloudflare Access policy. Postgres has no host port (internal network only).

Prefect UI: http://localhost:4200 (log in with `PREFECT_API_AUTH_STRING`).

## Running the pipeline

### Via deployment (recommended)

Triggered manually (no schedule -- content-plan finalization timing varies):

```bash
# From the UI: Deployments > content-plan-to-jira-pipeline/content-plan-to-jira > Run
# Or from the CLI (inside the container):
docker exec prefect prefect deployment run \
  'content-plan-to-jira-pipeline/content-plan-to-jira' \
  -p target_month="Juli 2026" -p validate_only=true \
  -p 'client_names=["Klinik Utama Gresik","Klinik Mata Jogja","Klinik Mata Boyolali"]'
```

Runs are picked up by `prefect-worker` and appear as a single parent flow run
(with nested sub-flows) in the UI.

### Via the standalone CLI (fallback)

```bash
# Defaults to next month; validate-only is the safe default in the deployment,
# but the CLI creates issues unless --validate-only is passed.
docker exec prefect python flows/content_plan_spreadsheet_to_jira_issue.py --validate-only

# Target a specific month
docker exec prefect python flows/content_plan_spreadsheet_to_jira_issue.py --month "Juli 2026"
docker exec prefect python flows/content_plan_spreadsheet_to_jira_issue.py --month-name Juli --year 2026

# One or several clients (validate only)
docker exec prefect python flows/content_plan_spreadsheet_to_jira_issue.py \
  --month "Juli 2026" --validate-only \
  --clients "Klinik Utama Gresik" "Klinik Mata Jogja" "Klinik Mata Boyolali"

# Follow logs
docker-compose logs -f prefect prefect-worker

# Rebuild after code/Dockerfile/dependency changes
docker-compose up -d --build prefect prefect-worker
```

### Locally with UV

```bash
cd service/prefect
uv sync
uv run python flows/content_plan_spreadsheet_to_jira_issue.py --validate-only

# One-time OAuth setup for local Google API access
uv run python run_google_oauth.py
```

## Flow Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `--month` | `str` | Target month as `"Month YYYY"`, e.g. `"Juli 2026"` or `"July 2026"`. Omit to auto-target next month. |
| `--month-name` | `str` | Month name only (e.g. `"Juli"`). Must be paired with `--year`. |
| `--year` | `int` | Year (e.g. `2026`). Must be paired with `--month-name`. |
| `--validate-only` | flag | Validates converted Jira issue data without creating issues in Jira. |
| `--single` | `str` | Run for a single client by name (alias for one `--clients` value). |
| `--clients` | `str...` | One or more client names (space-separated; quote names with spaces). |

The deployment exposes the same options as flow parameters: `target_month`,
`validate_only`, `client_names` (list), `max_issues`.

## What the flow does

`content_plan_spreadsheet_to_jira_issue.py` chains several sub-flows:

1. **Search** client Drive folders for the target month's content plan spreadsheet.
2. **Read** each spreadsheet's rows via the Google Sheets API.
3. **Format** rows uniformly (dates, text, numeric fields).
4. **Convert** rows into Jira issue-type payloads (issue type `10009`), using `hashmap.py` to resolve worker/component/editor/associate assignments per client.
5. **Validate or create** issues in Jira in bulk (max 45 per request), per client.

Each run writes a timestamped directory under `data/` (e.g. `data/20260702_191619/`) containing a step-by-step JSON trace (`step1_client_data.json`, `step4_content_plan_data.json`, `step7_validation_per_client.json`, etc.) for debugging and auditing.

## Adding a New Workflow

1. Reuse `shared/` for cross-cutting concerns instead of re-implementing them:
   rate limiting (`shared.rate_limit`), retries/backoff (`shared.retry`),
   batching (`shared.batching`), HTTP (`shared.http`), timestamps/months
   (`shared.dates`), JSON I/O (`shared.io`).
2. Add task(s) to the relevant file in `tasks/` (or a new API-group file), following
   the `api-group.resource.action` naming convention; keep tasks thin over `shared/`.
3. Create a new file in `flows/` with an `@flow`-decorated async entrypoint.
4. Register it: add a `deployments:` entry in `prefect.yaml` pointing at
   `flows/your_flow.py:your_flow`, `work_pool.name: noktah-pool`, then re-run
   `docker exec prefect bash bootstrap.sh` (or `prefect deploy --all`).
5. Test locally first with `uv run python flows/your_flow.py`.

The shared `noktah-pool` worker runs every deployment, so new workflows need no
new infrastructure. Scale throughput by adding `prefect-worker` replicas.

See [.claude/rules/backend/prefect.md](../../.claude/rules/backend/prefect.md) for full development standards (flow/task/block conventions, error handling, logging).

## Troubleshooting

- **Credential errors**: verify `GOOGLE_*` / `JIRA_*` env vars are set in the container (`docker exec prefect env`), or that the `google-creds` / `jira-creds` blocks are registered in the Prefect UI.
- **Rebuild needed**: after changing `pyproject.toml`, `uv.lock`, or the `Dockerfile`, run `docker-compose up -d --build prefect`.
- **Flow run history**: check the Prefect UI at http://localhost:4200 for detailed logs per flow run.
