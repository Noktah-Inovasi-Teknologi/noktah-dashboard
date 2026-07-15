"""
Tests for social_tasks signal capture (feature 003 additions):
- _coerce_count parsing of public-count cells
- social.signal.record issues an idempotent upsert with coerced values
DB access is mocked via a fake asyncpg pool; no real database.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

os.environ.setdefault("HARVEST_DB_URL", "postgresql://test:test@localhost/test")

from tasks import social_tasks as st


@pytest.mark.parametrize("value,expected", [
    ("", None), (None, None),
    ("1234", 1234), (1234, 1234), (12.0, 12),
    ("1.2K", 1200), ("3M", 3_000_000), ("12,345", 12345),
    ("n/a", None),
])
def test_coerce_count(value, expected):
    assert st._coerce_count(value) == expected


class _FakeConn:
    def __init__(self, capture):
        self._capture = capture

    async def execute(self, sql, *args):
        self._capture["sql"] = sql
        self._capture["args"] = args


class _FakeAcquire:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return False


class _FakePool:
    def __init__(self, conn):
        self._conn = conn

    def acquire(self):
        return _FakeAcquire(self._conn)

    async def close(self):
        pass


@pytest.mark.asyncio
async def test_signal_record_upserts_with_coerced_counts(monkeypatch):
    capture = {}

    async def fake_pool():
        return _FakePool(_FakeConn(capture))

    monkeypatch.setattr(st, "_db_pool", fake_pool)

    # Call the task's underlying function directly (bypass Prefect runtime).
    await st.social_signal_record.fn(
        platform="instagram", profile_key="acme", content_id="c1", content_type="video",
        published_at="2026-07-01T00:00:00Z", caption="halo", hashtags="#promo",
        views="1.2K", likes="3,400", comments="56",
        subtitle="sub", content_flow="hook", summary="ringkas",
    )

    sql = capture["sql"]
    assert "INSERT INTO harvested_signals" in sql
    assert "ON CONFLICT (platform, content_id) DO UPDATE" in sql  # idempotent (FR-013)
    args = capture["args"]
    # views/likes/comments coerced to ints (positional: see task signature order).
    assert 1200 in args and 3400 in args and 56 in args
