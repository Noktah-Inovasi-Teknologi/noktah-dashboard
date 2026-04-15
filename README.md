# Prefect Workflow Service

Workflow orchestration service for automating integrations with Google APIs and Jira.

## Quick Start

### Prerequisites
- Docker Desktop
- `.env` file with required credentials

### Start Services

```bash
# Start all services
docker-compose up -d

# Check service status
docker-compose ps
```

### Access
- **Prefect UI**: http://localhost:4200
- **API Health**: http://localhost:4200/api/health

## Running Workflows

```bash
# Execute a flow
docker exec prefect python flows/content_plan_spreadsheet_to_jira_issue.py

# View logs
docker-compose logs -f prefect
```

## Services

| Service | Port | Purpose |
|---------|------|---------|
| Prefect | 4200 | Workflow orchestration |
| PostgreSQL | 5432 | State persistence |
| Redis | 6379 | Cache and queues |

## Configuration

Create a `.env` file with:

```bash
# Database
POSTGRES_DB=dashboard
POSTGRES_DB_PREFECT=prefect
POSTGRES_USER=user
POSTGRES_PASSWORD=password
REDIS_PASSWORD=password

# Google APIs
GOOGLE_CLIENT_ID=your_client_id
GOOGLE_CLIENT_SECRET=your_client_secret
GOOGLE_REFRESH_TOKEN=your_refresh_token

# Jira
JIRA_URL=https://your-domain.atlassian.net
JIRA_USERNAME=your_email
JIRA_API_TOKEN=your_token
```

## Development

```bash
# Local development with UV
cd service/prefect
uv sync
uv run python flows/content_plan_spreadsheet_to_jira_issue.py
```

## Documentation

- [Prefect Service README](service/prefect/README.md) - Detailed workflow documentation
- [Prefect Development Rules](.claude/rules/backend/prefect.md) - Development guidelines
