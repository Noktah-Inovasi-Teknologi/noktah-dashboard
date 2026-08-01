"""
Schema parity tests (feature 004-relational-spine, User Story 3, FR-029/SC-006).

Proves config/postgres/init.sql (the greenfield path) and the numbered
migration chain (the upgrade path) produce structurally identical databases —
the two files are hand-written in parallel and will drift the moment one is
edited alone, which is exactly how harvested_items and harvested_signals came
to have no migration at all before this feature. See
specs/004-relational-spine/contracts/migrations.md.

Also proves every migration is safe to apply twice (FR-028).

Runs against a real disposable Postgres (two throwaway databases per test);
skipped automatically when SPINE_TEST_DATABASE_URL is unreachable.
"""
from pathlib import Path

import asyncpg
import pytest

from tests.conftest import FULL_CHAIN, MIGRATIONS_DIR, TEST_DSN

pytestmark = pytest.mark.schema

INIT_SQL = Path(__file__).resolve().parents[3] / "config" / "postgres" / "init.sql"

_ADMIN_DSN = TEST_DSN.rsplit("/", 1)[0] + "/postgres"


async def _fresh_db(name: str) -> None:
    admin = await asyncpg.connect(_ADMIN_DSN)
    try:
        try:
            await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        except asyncpg.PostgresError:
            await admin.execute(f'DROP DATABASE IF EXISTS "{name}"')
        await admin.execute(f'CREATE DATABASE "{name}"')
    finally:
        await admin.close()


def _dsn_for(name: str) -> str:
    return TEST_DSN.rsplit("/", 1)[0] + f"/{name}"


async def _apply_init_sql(conn: asyncpg.Connection) -> None:
    # init.sql's preamble (CREATE DATABASE / GRANT) targets the maintenance
    # connection at first container boot; only the table-defining portion
    # (from the extensions onward) applies to an already-created database.
    text = INIT_SQL.read_text(encoding="utf-8")
    table_defining_portion = "\n".join(text.splitlines()[17:])  # from "-- Client Knowledge Base" onward
    await conn.execute(table_defining_portion)


async def _apply_chain(conn: asyncpg.Connection, versions=FULL_CHAIN) -> None:
    for version in versions:
        sql = (MIGRATIONS_DIR / f"{version}.sql").read_text(encoding="utf-8")
        await conn.execute(sql)


async def _structure_snapshot(conn: asyncpg.Connection) -> dict:
    tables = await conn.fetch(
        "SELECT table_name FROM information_schema.tables WHERE table_schema='public' ORDER BY 1"
    )
    columns = await conn.fetch(
        """
        SELECT table_name || '.' || column_name || ':' || data_type || ':' || is_nullable
               || ':' || COALESCE(column_default, '') AS row
        FROM information_schema.columns WHERE table_schema='public' ORDER BY 1
        """
    )
    indexes = await conn.fetch(
        "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname='public' ORDER BY 1"
    )
    constraints = await conn.fetch(
        """
        SELECT conname, pg_get_constraintdef(oid) AS def
        FROM pg_constraint WHERE connamespace = 'public'::regnamespace ORDER BY 1
        """
    )
    return {
        "tables": [r["table_name"] for r in tables],
        "columns": [r["row"] for r in columns],
        "indexes": [(r["indexname"], r["indexdef"]) for r in indexes],
        "constraints": [(r["conname"], r["def"]) for r in constraints],
    }


@pytest.mark.asyncio
async def test_init_sql_and_migration_chain_are_structurally_identical():
    db_a, db_b = "parity_pytest_init", "parity_pytest_chain"
    await _fresh_db(db_a)
    await _fresh_db(db_b)

    conn_a = await asyncpg.connect(_dsn_for(db_a))
    conn_b = await asyncpg.connect(_dsn_for(db_b))
    try:
        await conn_a.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto; CREATE EXTENSION IF NOT EXISTS pg_trgm;")
        await _apply_init_sql(conn_a)

        await conn_b.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto; CREATE EXTENSION IF NOT EXISTS pg_trgm;")
        await _apply_chain(conn_b)

        snap_a = await _structure_snapshot(conn_a)
        snap_b = await _structure_snapshot(conn_b)
    finally:
        await conn_a.close()
        await conn_b.close()
        await _fresh_db(db_a)  # leaves an empty db rather than a populated leftover
        admin = await asyncpg.connect(_ADMIN_DSN)
        await admin.execute(f'DROP DATABASE IF EXISTS "{db_a}"')
        await admin.execute(f'DROP DATABASE IF EXISTS "{db_b}"')
        await admin.close()

    assert snap_a["tables"] == snap_b["tables"]
    assert snap_a["columns"] == snap_b["columns"]
    assert snap_a["indexes"] == snap_b["indexes"]
    assert snap_a["constraints"] == snap_b["constraints"]


@pytest.mark.asyncio
async def test_migration_chain_is_idempotent():
    """FR-028: every migration must be safe to apply twice."""
    db = "parity_pytest_idempotent"
    await _fresh_db(db)
    conn = await asyncpg.connect(_dsn_for(db))
    try:
        await conn.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto; CREATE EXTENSION IF NOT EXISTS pg_trgm;")
        await _apply_chain(conn)
        # Second pass must not raise.
        await _apply_chain(conn)
        applied = await conn.fetch("SELECT version FROM schema_migrations ORDER BY version")
        assert len(applied) == len(FULL_CHAIN)
    finally:
        await conn.close()
        admin = await asyncpg.connect(_ADMIN_DSN)
        await admin.execute(f'DROP DATABASE IF EXISTS "{db}"')
        await admin.close()
