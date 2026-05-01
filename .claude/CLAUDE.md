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
- **Redis**: Cache and task queues (Port 6379)

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
| Redis | 6379 | localhost:6379 |

### Health Check Endpoints
- **Prefect API**: http://localhost:4200/api/health

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

# Redis Configuration
REDIS_PASSWORD=your_redis_password

# Google Services (for workflows)
GOOGLE_CLIENT_ID=your_google_client_id
GOOGLE_CLIENT_SECRET=your_google_client_secret
GOOGLE_REFRESH_TOKEN=your_google_refresh_token

# Jira Integration
JIRA_URL=https://your-domain.atlassian.net
JIRA_USERNAME=your_email@domain.com
JIRA_API_TOKEN=your_jira_api_token
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
- **Redis** - Cache

## Deployment Environment

### Infrastructure Setup
- **Platform**: Windows with Docker Desktop
- **Container Runtime**: Docker Desktop
- **Database**: PostgreSQL container with persistent volumes
- **Cache**: Redis container with persistence

### Current Services
- PostgreSQL (healthy)
- Redis (healthy)
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
- Redis password protection
- No secrets in version control
