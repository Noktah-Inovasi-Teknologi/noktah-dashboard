"""Shared pytest fixtures for the Client Knowledge Base test suite.

Tests run against a real disposable PostgreSQL instance (KB_TEST_DATABASE_URL),
truncating the table between tests for isolation.
"""
import os

import asyncpg
import pytest

import db as db_module

TEST_DSN = os.getenv(
    "KB_TEST_DATABASE_URL", "postgresql://postgres:testpass@localhost:15432/kbtest"
)


@pytest.fixture(autouse=True)
async def _reset_pool_and_table():
    """Point the shared pool at the test database and truncate between tests."""
    os.environ["KB_DATABASE_URL"] = TEST_DSN
    db_module._pool = None

    pool = await db_module.get_pool()
    async with pool.acquire() as conn:
        await conn.execute("TRUNCATE TABLE knowledge_records")

    yield

    await db_module.close_pool()


@pytest.fixture
async def conn():
    pool = await db_module.get_pool()
    async with pool.acquire() as c:
        yield c
