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
```

### Backend Services (Python/CrewAI)
```bash
# Install Python dependencies
cd service/crewai
pip install -r requirements.txt

# Run CrewAI service directly (development)
cd service/crewai
python main.py

# FastAPI development server
uvicorn main:app --reload --port 8000
```

### Docker Environment
```bash
# Full setup (Windows)
script/setup.bat

# Development environment
docker-compose -f docker-compose.dev.yml up -d

# Production environment  
docker-compose -f docker-compose.prod.yml up -d

# Standard environment
docker-compose up -d

# Stop all services
docker-compose down

# View service status
docker-compose ps

# View logs
docker-compose logs -f [service_name]
```

## Architecture

### Microservices Structure
- **Frontend**: Nuxt 4 app with TypeScript, Vue 3, and Nuxt UI components
- **CrewAI Service**: FastAPI-based AI agent orchestration (Port 8000)
- **Backup Service**: Automated Google Drive backups
- **Database**: PostgreSQL 15 with health monitoring
- **Cache**: Redis with persistence
- **Proxy**: Nginx reverse proxy and load balancer
- **Monitoring**: Prometheus + Grafana + Loki stack

### Key Directories
- `app/` - Nuxt application entry point
- `service/crewai/` - AI agent orchestration service
- `service/backup/` - Database backup automation
- `config/` - Service configuration files (nginx, prometheus, grafana)
- `data/` - Persistent data storage and backups
- `script/` - Setup and utility scripts

### CrewAI Service Architecture
- `agent/` - AI agent definitions (currently empty, needs implementation)
- `crew/` - Crew workflow definitions (currently empty, needs implementation)  
- `model/` - Pydantic request/response models
- `tool/` - Custom tools for agents
- `util/` - Utility functions (database, redis helpers referenced but not implemented)

## Service Endpoints

### Main Services
- **Dashboard**: http://localhost (Nginx proxy)
- **Nuxt Dev**: http://localhost:3000 (direct access)
- **CrewAI API**: http://localhost:8000 (FastAPI with Swagger docs at `/docs`)
- **Grafana**: http://localhost:3001 (monitoring dashboards)
- **Prefect**: http://localhost:4200 (workflow orchestration)
- **Prometheus**: http://localhost:9090 (metrics collection)

### Database Services
- **PostgreSQL**: localhost:5432
- **Redis**: localhost:6379

## Configuration

### Environment Variables
- Copy `.env.example` to `.env` (currently empty, needs configuration)
- Configure API keys, database credentials, and external service tokens
- Google OAuth2 and Drive service account keys required
- Sentry DSN for error tracking
- OpenRouter API key for AI model access

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

### Backend
- **FastAPI** - REST API framework
- **CrewAI** (0.177.0) - AI agent framework
- **Pydantic** - Data validation and serialization
- **Async PostgreSQL** via asyncpg
- **Redis** - Caching and task queues

### AI/ML
- **LangChain** ecosystem for AI workflows
- **OpenAI/Anthropic** clients via OpenRouter
- **ChromaDB** for vector storage
- **Embedchain** for RAG implementations

## Development Notes

### Current State
- Infrastructure fully configured with monitoring stack
- FastAPI foundation implemented with authentication middleware
- Backup automation working with Google Drive integration
- **Missing**: Core CrewAI crews, database utilities, Nuxt application pages

### Key Dependencies
- CrewAI agents and crews need implementation in `service/crewai/agent/` and `service/crewai/crew/`
- Database and Redis utility classes referenced but not implemented
- Environment variables need documentation and example values

### Testing
- No testing framework currently configured
- `@nuxt/test-utils` included as dependency but not set up
- Consider adding pytest for Python services

### Monitoring & Health Checks
- All services have health check endpoints configured
- Prometheus metrics collection across all services
- Grafana dashboards for system monitoring
- Centralized logging with Loki + Promtail