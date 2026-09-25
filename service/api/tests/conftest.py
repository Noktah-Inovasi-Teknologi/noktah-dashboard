"""
Real-Postgres fixtures for the Hub's invariants (history, approvals, access).

Same approach as service/prefect/tests/conftest.py: partial unique indexes and
transaction ordering only fail against a real database, never a mock. Tests that
need it are marked `@pytest.mark.schema`; with no database reachable at
HUB_TEST_DATABASE_URL, only those are skipped.

`hub_db` builds a FRESH database per test from the full migration chain (000…010,
the same explicit list the parity check uses; never a glob), points app.db at it,
and syncs card definition v1.
"""
import asyncio
import os
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import asyncpg
import pytest
import pytest_asyncio

from app import db
from app.card import definition as card_definition

TEST_DSN = os.environ.get("HUB_TEST_DATABASE_URL", "postgresql://noktah:noktah_local_dev@localhost:5432/hub_test")
ROOT = Path(__file__).resolve().parents[3]
MIGRATIONS = ROOT / "config" / "postgres" / "migrations"
CHAIN = [
    "000_schema_migrations", "001_knowledge_records", "002_harvested_items", "003_harvested_signals",
    "004_relational_spine", "005_link_existing", "006_enforce_account_link", "007_signal_field_coverage",
    "008_observation_history", "009_structured_extraction", "010_hub_registry_card",
    "011_client_brand_required",
]


def _admin_dsn() -> str:
    u = urlparse(TEST_DSN)
    return urlunparse(u._replace(path="/postgres"))


def _db_name() -> str:
    return urlparse(TEST_DSN).path.lstrip("/")


async def _probe() -> bool:
    try:
        conn = await asyncpg.connect(_admin_dsn(), timeout=2)
        await conn.close()
        return True
    except Exception:
        return False


def pytest_collection_modifyitems(config, items):
    if not any("schema" in item.keywords for item in items):
        return
    if asyncio.run(_probe()):
        return
    skip = pytest.mark.skip(reason=f"No database reachable at HUB_TEST_DATABASE_URL ({TEST_DSN})")
    for item in items:
        if "schema" in item.keywords:
            item.add_marker(skip)


@pytest_asyncio.fixture
async def hub_db(monkeypatch):
    monkeypatch.setenv("HUB_API_DATABASE_URL", TEST_DSN)
    monkeypatch.setenv("HUB_API_ACCESS_API_AUD", "test-api-aud")
    admin = await asyncpg.connect(_admin_dsn())
    name = _db_name()
    await admin.execute(
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = $1 AND pid <> pg_backend_pid()", name)
    await admin.execute(f'DROP DATABASE IF EXISTS "{name}"')
    await admin.execute(f'CREATE DATABASE "{name}"')
    await admin.close()

    conn = await asyncpg.connect(TEST_DSN)
    await conn.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto; CREATE EXTENSION IF NOT EXISTS pg_trgm;")
    for migration in CHAIN:
        if migration == "006_enforce_account_link":
            continue  # fails by design on an empty harvested_signals backfill state; unrelated to the Hub
        await conn.execute((MIGRATIONS / f"{migration}.sql").read_text(encoding="utf-8"))
    definition = card_definition.load_file(str(ROOT / "config" / "hub"))
    await card_definition.sync(conn, definition)
    await conn.close()

    await db.open_pool(TEST_DSN)
    try:
        yield db.pool()
    finally:
        await db.close_pool()


@pytest_asyncio.fixture
async def api(hub_db):
    """In-process API against the test DB. `api(email)` → an httpx client acting as that
    verified sign-in (the Cloudflare JWT check itself is covered by test_auth.py)."""
    import httpx

    from app import auth, deps
    from app.main import app

    deps.set_definition(card_definition.load_file(str(ROOT / "config" / "hub")))
    clients = []

    from fastapi import Request

    # Each client carries its own identity, so several signed-in users can act in one
    # test without the last `api(...)` call silently re-identifying the others.
    async def fake_user(request: Request):
        return auth.User(email=request.headers["x-test-email"])
    app.dependency_overrides[auth.current_user] = fake_user

    def make(email: str) -> httpx.AsyncClient:
        c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://hub-api",
                              headers={"x-test-email": email.lower()})
        clients.append(c)
        return c

    yield make
    app.dependency_overrides.clear()
    for c in clients:
        await c.aclose()


async def add_person(conn, email: str, name: str, role: str, brand_key):
    """Seed a Person with one active role (brand_key None for owner)."""
    pid = await conn.fetchval("INSERT INTO people (display_name) VALUES ($1) RETURNING id", name)
    await conn.execute("INSERT INTO person_emails (person_id, email) VALUES ($1, $2)", pid, email)
    if role:
        await conn.execute(
            """INSERT INTO person_roles (person_id, role, noktah_brand_id)
               VALUES ($1, $2, (SELECT id FROM noktah_brands WHERE brand_key = $3))""",
            pid, role, brand_key)
    return str(pid)


async def add_client(conn, name: str, brand_key):
    return str(await conn.fetchval(
        """INSERT INTO clients (client_key, display_name, noktah_brand_id, status)
           VALUES (lower($1), $1, (SELECT id FROM noktah_brands WHERE brand_key = $2), 'active') RETURNING id""",
        name, brand_key))
