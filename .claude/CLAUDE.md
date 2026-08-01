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

# (Re)register all deployments after editing prefect.yaml or flow code.
# prefect.yaml is bind-mounted into the container (docker-compose.yml), so edits
# apply without an image rebuild — but a container recreate is needed the first
# time that mount is added (`docker-compose up -d prefect`).
docker exec prefect prefect deploy --all
# Or a subset by name pattern:
docker exec prefect prefect --no-prompt deploy -n "harvest-monthly-*"

# List deployments / trigger an ad-hoc run of a deployment
docker exec prefect prefect deployment ls
docker exec prefect prefect deployment run 'social-harvest-recent/social-harvest-recent' \
  --param profiles='["https://www.tiktok.com/@name"]' --param n=5

# Stories-only harvest (ephemeral ~24h; run frequently, unscheduled for now)
docker exec prefect python flows/social_harvest_stories.py --profiles "https://www.instagram.com/lasikasyik/"

# Per-client monthly harvest: harvest-monthly-<client> deployments (one per client in
# the Clients sheet), monthly cron on the 1st, staggered 30 min apart from 17:30 WIB
# (slots 14-18 spill onto the 2nd, 00:00-02:00). Window = days: 31 — a run collects the
# month it just covered. Dedup means the corpus still accumulates month over month, so
# the window only bounds a single run's reach; a NEW account back-fills one month, not a
# quarter, so run `social-harvest-window --days 90` once by hand when onboarding one.
# These deployments pass NO harvest_name — the flow falls back to Prefect's
# auto-generated flow-run name, so a delivered sheet row traces to the run that made it.
docker exec prefect prefect deployment run 'social-harvest-window/harvest-monthly-ecky-dental-center'

# Per-competitor monthly harvest: harvest-monthly-comp-<handle>, one per handle in the
# CLIENT_SOCIAL block of the Hashmaps sheet. Same shape as the client blocks (days: 31,
# monthly) so both sides are measured identically — songbird's ranking is a
# within-account comparison, which only means anything on symmetric sampling.
# Runs 02:30-05:00 WIB on the 2nd, clear of the 03:00 social-harvest-sync cron.
# Adding a competitor row to the sheet does NOT create a deployment — add a block
# to prefect.yaml too, then redeploy.
docker exec prefect prefect deployment run 'social-harvest-window/harvest-monthly-comp-kmneyecare'

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

### Relational Spine (clients, accounts, roles, runs)
```bash
# Roster sync: reconcile clients/accounts/roles from the Clients + Hashmaps
# worksheets into the datastore. Daily cron (03:30 WIB), also triggerable on
# demand. Nothing gates a harvest on this having run (FR-022a) — an
# unregistered handle is simply skipped and reported until the next sync.
docker exec prefect python flows/roster_sync.py
docker exec prefect python flows/roster_sync.py --validate-only   # preview only, writes nothing

# Spine backfill: one-time link of existing harvested_signals /
# harvested_items / knowledge_records rows to accounts/clients. Run once,
# after roster-sync has populated the roster. Safe to re-run.
docker exec prefect python flows/spine_backfill.py
docker exec prefect python flows/spine_backfill.py --validate-only

# Ownership answerable in SQL, no spreadsheet — see
# specs/004-relational-spine/quickstart.md for the full query and expected shape:
docker exec postgres psql -U noktah -d noktah_dashboard -c "
  SELECT c.display_name, r.role, ah.handle_text, s.platform, count(*) AS posts
  FROM harvested_signals s
  JOIN accounts a ON a.id = s.account_id
  JOIN account_handles ah ON ah.account_id = a.id AND ah.is_current
  LEFT JOIN client_account_roles r ON r.account_id = a.id AND r.is_active
  LEFT JOIN clients c ON c.id = r.client_id
  GROUP BY 1,2,3,4 ORDER BY 1,2;"
```
See `.claude/rules/backend/schema.md` for the migration workflow and
`docs/roster-maintenance.md` for the two maintainer actions this introduces
(recording a handle rename, correcting a client name mapping).

### Songbird Content Generation
```bash
# Monthly content plan → reviewable draft (default)
docker exec prefect python flows/songbird_monthly_plan.py --client "Ecky Dental Center" --month "Agustus 2026"

# Monthly plan appended straight into the live content-plan worksheet
docker exec prefect python flows/songbird_monthly_plan.py --client "Ecky Dental Center" --month "Agustus 2026" --target live

# Batch: one client or many, comma-separated (month defaults to next month)
docker exec prefect python flows/songbird_batch_plan.py --clients "Klinik Utama Gresik"
docker exec prefect python flows/songbird_batch_plan.py \
  --clients "Klinik Utama Gresik, Klinik Mata Sampang, Klinik Utama Sumenep"

# On-demand incidental content (standalone, draft-only, no dates)
docker exec prefect python flows/songbird_generate.py --client "Ecky Dental Center" --quantity 3 --platform instagram

# Deployments: songbird-monthly-plan (monthly cron); songbird-batch-plan and
# songbird-generate are unscheduled — trigger manually.
docker exec prefect prefect deploy --all
docker exec prefect prefect deployment run 'songbird-generate/songbird-generate' \
  --param client="Ecky Dental Center" --param quantity=3
docker exec prefect prefect deployment run 'songbird-batch-plan/songbird-batch-plan' \
  --param clients="Klinik Utama Gresik, Klinik Mata Sampang"

# Override the configured amounts for one run
docker exec prefect python flows/songbird_monthly_plan.py --client "MCafe" --month "Agustus 2026" \
  --content-mix "Post=4,Story=2,Short Video=3"
```
Monthly amounts are read **per content type** from the Clients worksheet's `Post` / `Story` /
`Short Video` columns — the total is their sum and the composition is enforced on the generated
plan (a zero means that type is never generated). Competitor handles come from the
`CLIENT_SOCIAL` block of the "Hashmaps" worksheet (see below); the client's **own** handles are
derived from the Clients worksheet's `Instagram`/`TikTok` URLs. Both merge with per-run
`own_handles` / `competitor_handles` params.

The draft sheet matches the **"DRAFT v5" content-plan layout** exactly (20 columns, incl. a
ready-to-post `Caption`); songbird fills only the content columns and leaves scheduling/production/
approval fields blank. Each content type is generated in its own call and **topped up until the
configured amount is met**, so a client contracted for 9 Short Videos gets 9.

Exemplars are scored **relative to each account's own baseline, per content type**, blending reach
(`views`) with engagement — a flat `likes + comments` ranking returned 8/8 video and 0 Posts. On top
of that, a bandit allocates the month between proven themes (repeat with a new angle) and untested
ones (explore), and burst detection surfaces rising topics. See
`.claude/rules/backend/songbird.md` for the constraints — several are counter-intuitive.

### Hashmaps (WORKERS / COMPONENTS / CONTENT_EDITOR / FIELD_ASSOCIATE / CLIENT_SOCIAL)
These mappings are **not hardcoded** — they live in the **"Hashmaps"** tab of the Clients workbook
(`1-aV46TIn4m_zs3vtCNeS_Bvl3Tt-tgg09uuG_NqgNNY`) and `hashmap.py` reads them at runtime. Add or
remove *rows* freely; only a change to the sheet's *columns* needs a code change
(`SHEET_LAYOUT` / `CLIENT_SOCIAL_LAYOUT` in `hashmap.py`).

```bash
# Entry counts per block (forces a fresh read of the sheet)
docker exec prefect python hashmap.py

# Dump one block as JSON
docker exec prefect python hashmap.py --block CLIENT_SOCIAL
```
Reads are cached in-process for `HASHMAP_TTL_SECONDS` (default 300) and snapshotted to
`data/hashmap_cache.json`, which is used as a fallback if the Sheets API is unreachable. Clients
absent from a block simply get no mapping (e.g. no Jira component, or no competitor signal for
songbird) — so a client must exist in the sheet to be wired up.

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
├── hashmap.py          # Sheet-backed data mappings (see "Hashmaps" below)
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
# SONGBIRD_CONTENT_TYPE_COLUMNS, SONGBIRD_LIVE_SPREADSHEET_ID, SONGBIRD_LIVE_TAB
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
