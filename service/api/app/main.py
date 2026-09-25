"""
Noktah Hub API: the only service that writes Hub data, and the only place its AI runs.

Reached only through the Cloudflare tunnel at hub-api.noktah.co, behind a Cloudflare
Access application that admits just the Hub Worker's service token. It publishes no
port on the host. Every /v1 route needs a verified Person with a Manager role
(auth.py, permissions.py); /internal routes are for Prefect on the Docker network
(internal.py). Contract: specs/008-hub-registry-client-card/contracts/hub-api.md.
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from . import db, deps, errors, internal
from .ai.routes import router as ai_router
from .card import definition as card_definition
from .card.routes import router as card_router
from .intake.routes import router as intake_router
from .people.routes import router as people_router
from .registry.routes import router as registry_router
from .requests.routes import router as requests_router
from .settings import get_settings

logger = logging.getLogger("hub-api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    await db.open_pool(settings.database_url)
    definition = card_definition.load_file(settings.card_definition_dir)
    async with db.pool().acquire() as conn:
        result = await card_definition.sync(conn, definition)
    logger.info(f"card definition {definition.version}: {result}")
    deps.set_definition(definition)
    yield
    await db.close_pool()


# No interactive docs: nothing here is meant to be browsed.
app = FastAPI(title="Noktah Hub API", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
errors.install(app)
app.include_router(people_router)
app.include_router(registry_router)
app.include_router(card_router)
app.include_router(requests_router)
app.include_router(intake_router)
app.include_router(ai_router)
app.include_router(internal.router)


@app.get("/health")
async def health() -> dict:
    """For Docker's health check. Says nothing about company data."""
    async with db.pool().acquire() as conn:
        await conn.fetchval("SELECT 1")
    return {"ok": True}
