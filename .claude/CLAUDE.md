# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Prefect workflow orchestration service for automating integrations with Google APIs (Sheets, Drive) and Jira. Built with Python and Prefect 3.x, running in Docker containers.

## Development Commands

### Prefect Workflows
```bash
# Run flows inside Prefect container (recommended)
docker exec prefect python flows/content_plan_spreadsheet_to_jira_issue.py

# Run for a specific month/year
docker exec prefect python flows/content_plan_spreadsheet_to_jira_issue.py --month "Juni 2026"

# Dry run (validate only, no Jira issues created)
docker exec prefect python flows/content_plan_spreadsheet_to_jira_issue.py --validate-only

# Access Prefect UI
# http://localhost:4200

# View flow logs
docker-compose logs -f prefect
```

### Prefect Deployments
```bash
# Deployments are defined in service/prefect/prefect.yaml and run on the
# noktah-pool process work pool, executed by the prefect-worker container.
# The social-harvest-* deployments have NO schedule — trigger them manually.

# (Re)register all deployments after editing prefect.yaml or flow code
docker exec prefect prefect deploy --all

# List deployments / trigger an ad-hoc run of a deployment
docker exec prefect prefect deployment ls
docker exec prefect prefect deployment run 'social-harvest-recent/social-harvest-recent' \
  --param profiles='["https://www.tiktok.com/@name"]' --param n=5

# Stories-only harvest (ephemeral ~24h; run frequently, unscheduled for now)
docker exec prefect python flows/social_harvest_stories.py --profiles "https://www.instagram.com/lasikasyik/"

# Sheet→DB sync: reconcile harvested_signals + detail sheets from the canonical
# Account Social Harvest sheets (reviewer-edited `advertisement` flag). Scheduled daily.
docker exec prefect python flows/social_harvest_sync.py

# Worker logs (deployed run execution)
docker-compose logs -f prefect-worker
```
Note: the `social-harvest-*` deployments ship with empty `profiles` — pass real
profile URLs at run time (via the Prefect UI "Run" form or the `--param` above).
`social-harvest-stories` collects only currently-active Stories (Instagram + TikTok)
via roach's `stories_only` listing — it's unscheduled; run it often to catch stories
before they expire.

### Songbird Content Generation
```bash
# Monthly content plan → reviewable draft (default)
docker exec prefect python flows/songbird_monthly_plan.py --client "Ecky Dental Center" --month "Agustus 2026"

# Monthly plan appended straight into the live content-plan worksheet
docker exec prefect python flows/songbird_monthly_plan.py --client "Ecky Dental Center" --month "Agustus 2026" --target live

# On-demand incidental content (standalone, draft-only, no dates)
docker exec prefect python flows/songbird_generate.py --client "Ecky Dental Center" --quantity 3 --platform instagram

# Deployments: songbird-monthly-plan (monthly cron) + songbird-generate (manual)
docker exec prefect prefect deploy --all
docker exec prefect prefect deployment run 'songbird-generate/songbird-generate' \
  --param client="Ecky Dental Center" --param quantity=3
```
Monthly quantity per client is read from the Clients worksheet; competitor/own handles come from
`hashmap.py::CLIENT_SOCIAL`. See `.claude/rules/backend/songbird.md`.

### Docker Environment
```bash
# Start all services
docker-compose up -d

# Start with rebuild
docker-compose up -d --build

# Graceful shutdown
docker-compose down

# View service status
docker-compose ps

# View logs (all services)
docker-compose logs -f

# View logs (specific service)
docker-compose logs -f prefect

# Monitor resource usage
docker stats
```

### Local Development (Python)
```bash
# Navigate to Prefect service
cd service/prefect

# Install dependencies with UV
uv sync

# Run flows locally
uv run python flows/content_plan_spreadsheet_to_jira_issue.py

# Run Google OAuth setup
uv run python run_google_oauth.py
```

## Architecture

### Services
- **Prefect**: Workflow orchestration server (Port 4200)
- **PostgreSQL**: Database for Prefect state persistence (Port 5432)

### Key Directories
- `service/prefect/` - Prefect workflow service
  - `flows/` - Workflow definitions
  - `tasks/` - Reusable task definitions
  - `blocks/` - Credential storage blocks
  - `data/` - Output data directory
- `config/postgres/` - Database initialization scripts
- `script/` - Setup and utility scripts
- `secrets/` - Secret files (not in version control)

### Prefect Service Structure
```
service/prefect/
├── Dockerfile          # Container definition with UV
├── pyproject.toml      # UV package manifest
├── uv.lock             # Locked dependencies
├── main.py             # CLI entry point
├── hashmap.py          # Static data mappings
├── run_google_oauth.py # Google OAuth setup
├── flows/              # Workflow definitions
├── tasks/              # Task definitions
├── blocks/             # Credential blocks
└── data/               # Output directory
```

## Service Endpoints & Ports

| Service | Port | URL |
|---------|------|-----|
| Prefect UI | 4200 | http://localhost:4200 |
| PostgreSQL | 5432 | localhost:5432 |
| roach API | 8081 (host) → 8080 (container) | http://localhost:8081 |

### Health Check Endpoints
- **Prefect API**: http://localhost:4200/api/health
- **roach API**: http://localhost:8081/health (host); `http://roach:8080/health` on the internal Docker network

> ⚠️ **roach + Instagram/TikTok: always impersonate a browser.** Every request to these platforms
> (yt-dlp, gallery-dl, and any direct API call such as the IG Reels/clips endpoint) MUST use a real
> browser TLS fingerprint (`curl_cffi` / gallery-dl `browser=`) — a bare `requests`/`httpx` call is
> `429`/`403`-flagged even with valid cookies. roach's `/list` also merges the IG Reels tab and enriches
> Reel **views**/comments from the Reels-grid API. Editing roach source requires an image rebuild
> (`docker-compose up -d --build roach`); it is NOT volume-mounted. See `.claude/rules/backend/roach.md`.

## Configuration

### Environment Variables
Create `.env` file with the following variables:

```bash
# Database Configuration
POSTGRES_DB=your_database_name
POSTGRES_DB_DEV=your_dev_database
POSTGRES_DB_PREFECT=prefect
POSTGRES_USER=your_database_user
POSTGRES_PASSWORD=your_secure_password

# Google Services (for workflows)
GOOGLE_CLIENT_ID=your_google_client_id
GOOGLE_CLIENT_SECRET=your_google_client_secret
GOOGLE_REFRESH_TOKEN=your_google_refresh_token

# Jira Integration
JIRA_URL=https://your-domain.atlassian.net
JIRA_USERNAME=your_email@domain.com
JIRA_API_TOKEN=your_jira_api_token

# Social Content Harvest (roach + social-harvest flows)
ROACH_API_KEY=your_roach_api_key           # must match service/roach/.env's ROACH_API_KEY
HARVEST_DRIVE_PARENT_ID=your_google_drive_parent_folder_id
# ROACH_API_URL and HARVEST_DB_URL are set automatically in docker-compose.yml
# (http://roach:8080 and a DSN built from POSTGRES_USER/PASSWORD/DB above) — no need to set here

# Songbird content generation (songbird-* flows)
OPENROUTER_API_KEY=your_openrouter_api_key  # now needed by the Prefect services (was roach-only)
OPENROUTER_MODEL=xiaomi/mimo-v2.5           # optional; house generation model
SONGBIRD_DRIVE_PARENT_ID=your_drive_folder_id_for_draft_content_plans
# Optional Clients-sheet / live-target overrides (defaults target the content-plan workbook):
# SONGBIRD_CLIENTS_SPREADSHEET_ID, SONGBIRD_CLIENTS_TAB, SONGBIRD_CLIENTS_NAME_COLUMN,
# SONGBIRD_QUANTITY_COLUMN, SONGBIRD_LIVE_SPREADSHEET_ID, SONGBIRD_LIVE_TAB
```

### Required External Services
- **Google APIs** - Sheets and Drive access for workflows
- **Jira** - Issue creation and management

## Technology Stack

### Core
- **Prefect** (3.6.9) - Workflow orchestration framework
- **Python** (3.14) - Runtime
- **UV** - Package manager

### Dependencies
- **asyncpg** - Async PostgreSQL driver for Prefect 3.x
- **pandas** - Data processing and transformation
- **atlassian-python-api** - Jira API integration
- **google-api-python-client** - Google Workspace APIs
- **google-auth** - Google authentication
- **pydantic-settings** - Configuration management

### Infrastructure
- **Docker** - Containerization
- **PostgreSQL 15** - Database

## Deployment Environment

### Infrastructure Setup
- **Platform**: Windows with Docker Desktop
- **Container Runtime**: Docker Desktop
- **Database**: PostgreSQL container with persistent volumes

### Current Services
- PostgreSQL (healthy)
- Prefect Server (healthy)

## Development Notes

### Current Implementation Status
- Prefect workflows with Google Sheets integration
- Prefect tasks for Google API and Jira operations
- Content plan spreadsheet to Jira issue flow (fully working)
- Docker environment with single compose file
- Health check endpoints configured

### Available Workflows
- `content_plan_spreadsheet_to_jira_issue.py` - Reads content plans from Google Sheets and creates Jira issues

### Running Workflows
```bash
# Execute a flow in the container (defaults to next month)
docker exec prefect python flows/content_plan_spreadsheet_to_jira_issue.py

# Run for a specific month and year (combined format)
docker exec prefect python flows/content_plan_spreadsheet_to_jira_issue.py --month "Juni 2026"
docker exec prefect python flows/content_plan_spreadsheet_to_jira_issue.py --month "June 2026"

# Run for a specific month and year (separate args)
docker exec prefect python flows/content_plan_spreadsheet_to_jira_issue.py --month-name Juni --year 2026

# Dry run — validate Jira issues without creating them
docker exec prefect python flows/content_plan_spreadsheet_to_jira_issue.py --validate-only

# Combine month targeting with validate-only
docker exec prefect python flows/content_plan_spreadsheet_to_jira_issue.py --month "Juni 2026" --validate-only

# View execution in Prefect UI
# http://localhost:4200
```

#### Flow Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `--month` | `str` | Target month in `"Month YYYY"` format, e.g. `"Juni 2026"` or `"June 2026"`. Omit to auto-use next month. |
| `--month-name` | `str` | Month name only (e.g. `"Juni"`). Must be paired with `--year`. |
| `--year` | `int` | Year (e.g. `2026`). Must be paired with `--month-name`. |
| `--validate-only` | flag | Dry-run mode — validates Jira issue data without creating issues in Jira. |

### Security Considerations
- Environment variables for all sensitive data
- Postgres authentication with SCRAM-SHA-256
- No secrets in version control
