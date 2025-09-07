# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Enterprise dashboard platform for AI-powered data analysis and content creation, built with Nuxt 4 frontend and Python/CrewAI backend services.

## Development Commands

### Frontend (Nuxt 4)
```bash
# Install dependencies
pnpm install

# Development server (http://localhost:3000)
pnpm dev

# Production build
pnpm build

# Preview production build
pnpm preview

# Run tests
pnpm test

# Run unit tests only
pnpm test:unit

# Run Nuxt-specific tests
pnpm test:nuxt
```

### Backend Services (Python/CrewAI)
```bash
# Install Python dependencies
cd service/crewai
pip install -r requirements.txt

# Run CrewAI service directly (development)
cd service/crewai
python main.py

# FastAPI development server with hot reload
uvicorn main:app --reload --port 8000
```

### Docker Environment
```bash
# Development environment (minimal services: nuxt, postgres, redis, prefect, crewai)
docker-compose -f docker-compose.dev.yml up -d

# Production environment (full monitoring stack)
docker-compose -f docker-compose.prod.yml up -d

# Development: Start core services only
docker-compose -f docker-compose.dev.yml up -d postgres redis

# Production: Start core services only (without Nuxt app)
docker-compose -f docker-compose.prod.yml up -d postgres redis prometheus grafana loki promtail

# Graceful shutdown
docker-compose -f docker-compose.dev.yml down  # Development
docker-compose -f docker-compose.prod.yml down  # Production

# Force rebuild and restart (after code changes)
docker-compose -f docker-compose.dev.yml down && docker-compose -f docker-compose.dev.yml up -d --build  # Development
docker-compose -f docker-compose.prod.yml down && docker-compose -f docker-compose.prod.yml up -d --build  # Production

# View service status
docker-compose -f docker-compose.dev.yml ps  # Development
docker-compose -f docker-compose.prod.yml ps  # Production

# View logs (all services)
docker-compose -f docker-compose.dev.yml logs -f  # Development
docker-compose -f docker-compose.prod.yml logs -f  # Production

# View logs (specific service)
docker-compose -f docker-compose.dev.yml logs -f [service_name]  # Development
docker-compose -f docker-compose.prod.yml logs -f [service_name]  # Production

# Monitor resource usage
docker stats
```

### Cloudflare Tunnel Deployment (WSL2)
```bash
# Access Docker Desktop WSL2 environment
wsl -d docker-desktop

# Download and install cloudflared
wget -O ./cloudflared https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64
chmod +x ./cloudflared

# Authenticate with Cloudflare
./cloudflared tunnel login

# Create tunnel (replace with your tunnel name)
./cloudflared tunnel create [TUNNEL_NAME]

# Set up DNS routing (replace with your domain)
./cloudflared tunnel route dns [TUNNEL_NAME] [YOUR_DOMAIN]

# Run tunnel (config file at /root/.cloudflared/config.yml)
./cloudflared tunnel --config /root/.cloudflared/config.yml run

# List tunnels
./cloudflared tunnel list

# Get tunnel info
./cloudflared tunnel info [TUNNEL_NAME]
```

## Architecture

### Microservices Structure
- **Frontend**: Nuxt 4 app with TypeScript, Vue 3, and Nuxt UI components
- **CrewAI Service**: FastAPI-based AI agent orchestration (Port 8000)
- **Prefect Service**: Workflow orchestration (Port 4200)
- **Backup Service**: Automated Google Drive backups
- **Database**: PostgreSQL 15 with health monitoring
- **Cache**: Redis with persistence
- **Proxy**: Nginx reverse proxy and load balancer (Production only)
- **Monitoring**: Prometheus + Grafana + Loki stack (Production only)

### Key Directories
- `app/` - Nuxt application entry point
- `server/` - Nuxt server-side code (API routes, middleware)
- `content/` - Nuxt Content markdown files
- `public/` - Static assets
- `test/` - Test files (unit, e2e, Nuxt-specific)
- `service/crewai/` - AI agent orchestration service
- `service/backup/` - Database backup automation  
- `service/prefect/` - Workflow orchestration
- `config/` - Service configuration files (nginx, prometheus, grafana)
- `data/` - Persistent data storage and backups
- `script/` - Setup and utility scripts
- `secrets/` - Secret files (not in version control)

### CrewAI Service Architecture
- `agent/` - AI agent definitions (currently empty, needs implementation)
- `crew/` - Crew workflow definitions (currently empty, needs implementation)  
- `model/` - Pydantic request/response models
- `tool/` - Custom tools for agents
- `util/` - Utility functions (database, redis helpers referenced but not implemented)

## Service Endpoints & Ports

### Development Environment
- **Nuxt App**: http://localhost:3000 (Nuxt 4 application)
- **CrewAI API**: http://localhost:8000 (FastAPI with Swagger docs at `/docs`)
- **Prefect Workflows**: http://localhost:4200 (workflow orchestration)
- **PostgreSQL Database**: localhost:5432
- **Redis Cache**: localhost:6379

### Production Environment
- **Main Dashboard**: http://localhost (Nginx proxy, ports 80/443)
- **Nuxt App Direct**: http://localhost:3000 (Nuxt 4 application)
- **Grafana Monitoring**: http://localhost:3001 (dashboards & analytics)
- **Prometheus Metrics**: http://localhost:9090 (raw metrics data)
- **CrewAI API**: http://localhost:8000 (FastAPI with Swagger docs at `/docs`)
- **Prefect Workflows**: http://localhost:4200 (workflow orchestration)
- **PostgreSQL Database**: localhost:5432
- **Redis Cache**: localhost:6379

### Health Check Endpoints
- **Nuxt App**: http://localhost:3000/api/health-check
- **Nginx**: http://localhost (returns Nuxt app if healthy, production only)
- **Database**: Check via container logs or health check endpoint

## Configuration

### Environment Variables
Create `.env` file with the following variables:

```bash
# Database Configuration
POSTGRES_DB=your_database_name
POSTGRES_USER=your_database_user
POSTGRES_PASSWORD=your_secure_password

# Redis Configuration  
REDIS_PASSWORD=your_redis_password

# Authentication
JWT_SECRET=your_jwt_secret_key

# Google Services
GOOGLE_CLIENT_ID=your_google_client_id
GOOGLE_CLIENT_SECRET=your_google_client_secret
GOOGLE_DRIVE_FOLDER_ID=your_drive_folder_id

# AI Services
OPENROUTER_API_KEY=your_openrouter_api_key

# Monitoring (Production)
GRAFANA_ADMIN_PASSWORD=your_grafana_password

# Backup Configuration
BACKUP_SCHEDULE=0 6 * * *  # Daily at 6 AM
BACKUP_RETENTION_DAYS=30

# Error Tracking (Optional)
SENTRY_DSN=your_sentry_dsn
```

### Required External Services
- **Google OAuth2** - User authentication
- **Google Drive API** - Backup storage  
- **OpenRouter** - AI model access
- **Sentry** - Error tracking (optional)

## Technology Stack

### Frontend
- **Nuxt 4** (4.0.3) with Vue 3 and TypeScript
- **Package Manager**: pnpm (10.7.0)
- **UI Library**: Nuxt UI (3.3.3)
- **Content**: Nuxt Content (3.6.3) for markdown processing
- **Image Optimization**: Nuxt Image (1.11.0)
- **Scripts**: Nuxt Scripts (0.11.13)

### Backend
- **FastAPI** - REST API framework
- **CrewAI** (0.177.0) - AI agent framework
- **Pydantic** - Data validation and serialization
- **Async PostgreSQL** via asyncpg
- **Redis** - Caching and task queues
- **Prefect** - Workflow orchestration

### AI/ML Stack
- **LangChain** (0.3.27) ecosystem for AI workflows
- **OpenAI** (1.99.5) and **Anthropic** (0.66.0) clients
- **ChromaDB** (0.5.23) for vector storage
- **Embedchain** (0.1.128) for RAG implementations
- **LanceDB** (0.24.3) for vector database
- **Cohere** (5.17.0) for additional AI capabilities

### Testing Framework
- **Vitest** (3.2.4) - Main test runner
- **Playwright** (1.55.0) - E2E testing
- **@vue/test-utils** - Vue component testing
- **@nuxt/test-utils** - Nuxt-specific testing utilities
- **Happy-DOM** - DOM environment for testing

### Development Tools
- **TypeScript** (5.6.3) - Type safety
- **Better SQLite3** - Local database for testing
- **Docker** - Containerization
- **WSL2** - Windows development environment

## Deployment Environment

### Infrastructure Setup
- **Platform**: Windows with WSL2 (Docker Desktop)
- **WSL2 Distribution**: `docker-desktop` (Alpine-based)
- **Container Runtime**: Docker Desktop on WSL2
- **Reverse Proxy**: Cloudflare Tunnel
- **Public Access**: Via Cloudflare domain

### Deployment Architecture
- **Core Services**: Running in Docker containers on WSL2
- **Frontend**: Nuxt 4 application (port 3000)
- **Tunnel**: Cloudflared running in docker-desktop WSL2
- **Database**: PostgreSQL container with persistent volumes
- **Monitoring**: Full Prometheus/Grafana/Loki stack (Production only)

### Current Deployment Status
- ✅ Docker services running (PostgreSQL, Redis)
- ✅ Cloudflare Tunnel configured and active
- ✅ DNS routing configured
- ✅ SSL/HTTPS handled by Cloudflare
- ✅ Separate dev/prod Docker configurations
- ⚠️ Monitoring stack (production only)

### Tunnel Configuration Files
- **Config**: `/root/.cloudflared/config.yml` (in docker-desktop WSL2)
- **Credentials**: `/root/.cloudflared/[TUNNEL_ID].json` (in docker-desktop WSL2)
- **Certificate**: `/root/.cloudflared/cert.pem` (in docker-desktop WSL2)

## Development Notes

### Current Implementation Status
- ✅ Nuxt 4 frontend with TypeScript and Vue 3
- ✅ Docker development and production environments
- ✅ FastAPI foundation with authentication middleware
- ✅ Testing framework configured (Vitest + Playwright)
- ✅ Backup automation with Google Drive integration
- ✅ Health check endpoints implemented
- ⚠️ CrewAI agents and crews need implementation
- ⚠️ Database and Redis utility classes need implementation
- ⚠️ Frontend application pages need development

### Missing Implementations
- CrewAI agents in `service/crewai/agent/`
- CrewAI crews in `service/crewai/crew/`
- Database utility classes in `service/crewai/util/`
- Redis utility classes in `service/crewai/util/`
- Main dashboard UI components
- Authentication pages and flows

### Testing
- **Framework**: Vitest with multiple project configuration
- **Unit Tests**: `pnpm test:unit` - Test utilities and functions
- **Nuxt Tests**: `pnpm test:nuxt` - Test Nuxt-specific functionality
- **E2E Tests**: Playwright for end-to-end testing
- **Test Files**: Located in `test/` directory with subdirectories for different test types

### Monitoring & Health Checks
- Health check endpoints configured for all services
- Prometheus metrics collection (production)
- Grafana dashboards for system monitoring (production)
- Centralized logging with Loki + Promtail (production)
- Container health checks for database and Redis services

### Security Considerations
- Environment variables for all sensitive data
- JWT-based authentication
- Postgres authentication with SCRAM-SHA-256
- Redis password protection
- SSL/TLS through Cloudflare
- No secrets in version control