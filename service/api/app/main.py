"""
Noktah Hub API: the only service that writes company data, and later the AI intake.

Reached only through the Cloudflare tunnel at hub-api.noktah.co, behind a Cloudflare
Access application that admits just the Hub Worker's service token. It publishes no
port on the host. Every /v1 route needs a verified manager (see auth.py).
"""
from contextlib import asynccontextmanager
from typing import List

from fastapi import Depends, FastAPI
from pydantic import BaseModel

from . import db
from .auth import User, current_user
from .settings import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.open_pool(get_settings().database_url)
    yield
    await db.close_pool()


# No interactive docs: nothing here is meant to be browsed.
app = FastAPI(title="Noktah Hub API", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.get("/health")
async def health() -> dict:
    """For Docker's health check. Says nothing about company data."""
    async with db.pool().acquire() as conn:
        await conn.fetchval("SELECT 1")
    return {"ok": True}


class Me(BaseModel):
    email: str


@app.get("/v1/me", response_model=Me)
async def me(user: User = Depends(current_user)) -> Me:
    return Me(email=user.email)


class ClientSummary(BaseModel):
    id: str
    name: str
    is_active: bool


@app.get("/v1/clients", response_model=List[ClientSummary])
async def list_clients(user: User = Depends(current_user)) -> List[ClientSummary]:
    async with db.pool().acquire() as conn:
        rows = await conn.fetch("SELECT id, display_name, is_active FROM clients ORDER BY display_name")
    return [ClientSummary(id=str(r["id"]), name=r["display_name"], is_active=r["is_active"]) for r in rows]
