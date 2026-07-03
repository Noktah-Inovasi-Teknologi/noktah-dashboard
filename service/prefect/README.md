# Prefect Service

Prefect 3.x workflow orchestration service that reads content plans from Google Sheets and creates Jira issues from them. Runs in Docker via the root `docker-compose.yml`, backed by PostgreSQL for flow-run state.

## Structure

```
service/prefect/
├── flows/
│   ├── content_plan_spreadsheet_to_jira_issue.py  # Main flow (read Sheets -> convert -> create Jira issues)
│   └── common/                                    # Shared flow helpers
├── tasks/
│   ├── google_tasks.py    # Sheets/Drive operations
│   ├── jira_tasks.py      # Jira read/write operations
│   └── utility_tasks.py   # Date/text/number formatting, JSON output helpers
├── blocks/
│   ├── google_credentials.py  # GoogleCredentials block (OAuth refresh token)
│   └── jira_credentials.py    # JiraCredentials block (Basic auth: email + API token)
├── hashmap.py              # Static mappings: WORKERS, COMPONENTS, CONTENT_EDITOR, FIELD_ASSOCIATE
├── main.py                 # CLI entry point
├── run_google_oauth.py     # One-time Google OAuth setup (local dev)
├── pyproject.toml / uv.lock
├── Dockerfile
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

## Running

### In Docker (recommended)

```bash
# From the repo root
docker-compose up -d prefect

# Run the content plan -> Jira flow (defaults to next month)
docker exec prefect python flows/content_plan_spreadsheet_to_jira_issue.py

# Target a specific month
docker exec prefect python flows/content_plan_spreadsheet_to_jira_issue.py --month "Juni 2026"
docker exec prefect python flows/content_plan_spreadsheet_to_jira_issue.py --month-name Juni --year 2026

# Dry run - validate Jira issue data without creating issues
docker exec prefect python flows/content_plan_spreadsheet_to_jira_issue.py --validate-only

# Follow logs
docker-compose logs -f prefect

# Rebuild after Dockerfile/dependency changes
docker-compose up -d --build prefect
```

Prefect UI: http://localhost:4200

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
| `--month` | `str` | Target month as `"Month YYYY"`, e.g. `"Juni 2026"` or `"June 2026"`. Omit to auto-target next month. |
| `--month-name` | `str` | Month name only (e.g. `"Juni"`). Must be paired with `--year`. |
| `--year` | `int` | Year (e.g. `2026`). Must be paired with `--month-name`. |
| `--validate-only` | flag | Validates converted Jira issue data without creating issues in Jira. |

## What the flow does

`content_plan_spreadsheet_to_jira_issue.py` chains several sub-flows:

1. **Search** client Drive folders for the target month's content plan spreadsheet.
2. **Read** each spreadsheet's rows via the Google Sheets API.
3. **Format** rows uniformly (dates, text, numeric fields).
4. **Convert** rows into Jira issue-type payloads (issue type `10009`), using `hashmap.py` to resolve worker/component/editor/associate assignments per client.
5. **Validate or create** issues in Jira in bulk (max 45 per request), per client.

Each run writes a timestamped directory under `data/` (e.g. `data/20260702_191619/`) containing a step-by-step JSON trace (`step1_client_data.json`, `step4_content_plan_data.json`, `step7_validation_per_client.json`, etc.) for debugging and auditing.

## Adding a New Flow

1. Add task(s) to the relevant file in `tasks/` (or a new file if it's a new API group), following the `api-group.resource.action` naming convention.
2. Create a new file in `flows/` with an `@flow`-decorated async function returning a `Dict[str, Any]` (`start_time`, `end_time`, `data`, `summary`, optional `error`).
3. Add an `argparse` CLI block under `if __name__ == "__main__":` for standalone execution, mirroring the existing flow.
4. Test locally with `uv run python flows/your_flow.py` before running it in Docker.

See [.claude/rules/backend/prefect.md](../../.claude/rules/backend/prefect.md) for full development standards (flow/task/block conventions, error handling, logging).

## Troubleshooting

- **Credential errors**: verify `GOOGLE_*` / `JIRA_*` env vars are set in the container (`docker exec prefect env`), or that the `google-creds` / `jira-creds` blocks are registered in the Prefect UI.
- **Rebuild needed**: after changing `pyproject.toml`, `uv.lock`, or the `Dockerfile`, run `docker-compose up -d --build prefect`.
- **Flow run history**: check the Prefect UI at http://localhost:4200 for detailed logs per flow run.
