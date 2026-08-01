"""
Tests for run-record tasks (feature 004-relational-spine, User Story 4).

Runs against a real disposable Postgres — `summary` is JSONB and needs a real
round-trip to catch serialization mistakes (a dict passed to asyncpg without
an explicit ::jsonb cast/json.dumps is exactly the kind of bug a mock hides).
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("HARVEST_DB_URL", "postgresql://test:test@localhost/test")

from tests.conftest import pool_from_connection  # noqa: E402
from tasks import run_tasks as rt  # noqa: E402

pytestmark = pytest.mark.schema


@pytest.mark.asyncio
async def test_run_created_at_start_and_closed_at_finish(spine_db, monkeypatch):
    monkeypatch.setattr(rt, "_db_pool", pool_from_connection(spine_db))

    run_id = await rt.run_record_start.fn(kind="collection", flow_name="social-harvest-window")
    row = await spine_db.fetchrow("SELECT kind, flow_name, status, ended_at FROM runs WHERE id = $1", run_id)
    assert row["kind"] == "collection"
    assert row["flow_name"] == "social-harvest-window"
    assert row["status"] == "running"
    assert row["ended_at"] is None

    await rt.run_record_finish.fn(run_id=run_id, status="completed", summary={"items_collected": 5})
    row = await spine_db.fetchrow("SELECT status, ended_at, summary FROM runs WHERE id = $1", run_id)
    assert row["status"] == "completed"
    assert row["ended_at"] is not None
    import json
    assert json.loads(row["summary"])["items_collected"] == 5


@pytest.mark.asyncio
async def test_crashed_run_leaves_ended_at_null(spine_db, monkeypatch):
    """A crashed flow that never reaches run.record.finish must not show a
    false completion — ended_at stays null, distinguishing 'still running or
    crashed' from a genuinely finished run."""
    monkeypatch.setattr(rt, "_db_pool", pool_from_connection(spine_db))
    run_id = await rt.run_record_start.fn(kind="generation", flow_name="songbird-generate")
    row = await spine_db.fetchrow("SELECT ended_at, status FROM runs WHERE id = $1", run_id)
    assert row["ended_at"] is None
    assert row["status"] == "running"


@pytest.mark.asyncio
async def test_run_with_no_client_id_is_valid(spine_db, monkeypatch):
    """FR-019: client_id is optional (null for multi-client or unattributed runs)."""
    monkeypatch.setattr(rt, "_db_pool", pool_from_connection(spine_db))
    run_id = await rt.run_record_start.fn(kind="collection", flow_name="social-harvest-recent", client_id=None)
    row = await spine_db.fetchrow("SELECT client_id FROM runs WHERE id = $1", run_id)
    assert row["client_id"] is None
