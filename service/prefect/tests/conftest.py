"""
Shared pytest fixtures for schema/constraint tests (feature 004-relational-spine).

Partial unique indexes and the NOT VALID -> VALIDATE sequence are exactly the class
of bug that does not reproduce against a mock, so these tests run against a real,
disposable PostgreSQL database rather than a fake.

Tests that need a real database are marked `@pytest.mark.schema` (registered in
pyproject.toml, T003). When no database is reachable at SPINE_TEST_DATABASE_URL,
`pytest_collection_modifyitems` below marks only those tests as skipped — every
other (mocked) test in the suite is unaffected.
"""
import os
from pathlib import Path

import asyncpg
import pytest
import pytest_asyncio

TEST_DSN = os.environ.get(
    "SPINE_TEST_DATABASE_URL",
    "postgresql://noktah:noktah_local_dev@localhost:5432/spine_test",
)

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "config" / "postgres" / "migrations"

# The migration chain, in application order. Single source of truth for both the
# `spine_db` fixture and test_schema_parity.py — two lists would silently drift
# the moment a migration is added, and the parity test exists precisely to catch
# that class of drift.
FULL_CHAIN = [
    "000_schema_migrations",
    "001_knowledge_records",
    "002_harvested_items",
    "003_harvested_signals",
    "004_relational_spine",
    "005_link_existing",
    "006_enforce_account_link",
    "007_signal_field_coverage",
    "008_observation_history",
    "009_structured_extraction",
    "010_hub_registry_card",
    "011_client_brand_required",
]

# `spine_db` skips 006: it enforces account_id IS NOT NULL and fails by design
# until a backfill has run, which most tests using the fixture do not do. 007 is
# additive and safe, so it IS included — feature 005's tests need
# field_availability / capture_outcomes, and excluding it by taking a tail slice
# would silently drop every migration after 006 as the chain grows.
# It skips 011 for the same reason: 011 requires every client to have a Noktah
# Brand, which roster-sync (written before brands existed, and paused once the
# Hub owns the roster) never sets.
BASE_CHAIN = [m for m in FULL_CHAIN if m not in ("006_enforce_account_link", "011_client_brand_required")]

_db_available_cache = None


async def _probe() -> bool:
    # Probe the server, not the target database — `spine_test` doesn't exist
    # until the `spine_db` fixture creates it for each test.
    admin_dsn = TEST_DSN.rsplit("/", 1)[0] + "/postgres"
    try:
        conn = await asyncpg.connect(admin_dsn, timeout=2)
    except Exception:
        return False
    await conn.close()
    return True


def db_available() -> bool:
    """Cheap, cached reachability check (one connection attempt per test session)."""
    global _db_available_cache
    if _db_available_cache is None:
        import asyncio

        _db_available_cache = asyncio.run(_probe())
    return _db_available_cache


def pytest_collection_modifyitems(config, items):
    if db_available():
        return
    skip = pytest.mark.skip(
        reason=f"No database reachable at SPINE_TEST_DATABASE_URL ({TEST_DSN}); "
        f"point it at any Postgres the tests may create throwaway databases on."
    )
    for item in items:
        if "schema" in item.keywords:
            item.add_marker(skip)


class _PoolFromConnection:
    """
    Wrap a single asyncpg connection as a fake pool exposing `.acquire()`/`.close()`,
    so task modules' `_db_pool()` can be monkeypatched onto the already-migrated
    `spine_db` connection instead of opening a second real pool per test.
    """

    def __init__(self, conn):
        self._conn = conn

    def acquire(self):
        return self

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return False

    async def close(self):
        pass


def pool_from_connection(conn):
    """
    Returns an ASYNC callable suitable for `monkeypatch.setattr(module, "_db_pool", ...)`
    — every task module's real `_db_pool` is `async def`, called as `await _db_pool()`.

    Strict arity on purpose: every module's `_db_pool` is called with at most an
    optional env-var name, so a signature mismatch should fail loudly here rather
    than be absorbed by a permissive factory.
    """

    async def _factory(*_args):
        return _PoolFromConnection(conn)

    return _factory


@pytest_asyncio.fixture
async def spine_db():
    """
    A disposable database with every migration EXCEPT 006 applied, fresh per test.

    006 (the NOT VALID -> VALIDATE enforcement) is deliberately not applied here —
    it fails until the harvest tables' account_id columns are fully backfilled,
    which most tests using this fixture do not do. Tests exercising 006 apply it
    explicitly after populating account_id.

    007 (field_availability / capture_outcomes) IS applied: it is purely additive
    and feature 005's constraint tests depend on it.
    """
    admin = await asyncpg.connect(TEST_DSN.rsplit("/", 1)[0] + "/postgres")
    dbname = TEST_DSN.rsplit("/", 1)[-1]
    try:
        await admin.execute(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')
    except asyncpg.PostgresError:
        await admin.execute(f'DROP DATABASE IF EXISTS "{dbname}"')
    await admin.execute(f'CREATE DATABASE "{dbname}"')
    await admin.close()

    conn = await asyncpg.connect(TEST_DSN)
    try:
        for version in BASE_CHAIN:
            await conn.execute((MIGRATIONS_DIR / f"{version}.sql").read_text(encoding="utf-8"))
        yield conn
    finally:
        await conn.close()
