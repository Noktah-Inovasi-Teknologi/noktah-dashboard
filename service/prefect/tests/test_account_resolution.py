"""
Tests for `social.account.resolve` (feature 004-relational-spine), per
specs/004-relational-spine/contracts/account-resolution.md.

Runs against a real disposable Postgres (see tests/conftest.py) because the
lookup depends on the `UNIQUE (platform, handle_key)` constraint and the
current/former handle history, not just application logic.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("HARVEST_DB_URL", "postgresql://test:test@localhost/test")

from tests.conftest import pool_from_connection  # noqa: E402
from tasks import social_tasks as st  # noqa: E402

pytestmark = pytest.mark.schema


async def _account(conn, platform="instagram"):
    return await conn.fetchval(
        "INSERT INTO accounts (platform, is_active) VALUES ($1, true) RETURNING id", platform
    )


async def _handle(conn, account_id, platform, handle, is_current=True):
    await conn.execute(
        """
        INSERT INTO account_handles (account_id, platform, handle_key, handle_text, identifier_kind, is_current)
        VALUES ($1, $2, $3, $4, 'handle', $5)
        """,
        account_id, platform, handle.lower(), handle, is_current,
    )


@pytest.mark.asyncio
async def test_resolved_when_handle_registered_and_active(spine_db, monkeypatch):
    account = await _account(spine_db)
    await _handle(spine_db, account, "instagram", "eckydentalcenter")
    monkeypatch.setattr(st, "_db_pool", pool_from_connection(spine_db))

    result = await st.social_account_resolve.fn(platform="instagram", handle="eckydentalcenter")
    assert result["outcome"] == "resolved"
    assert result["account_id"] == account


@pytest.mark.asyncio
async def test_case_insensitive_lookup(spine_db, monkeypatch):
    account = await _account(spine_db)
    await _handle(spine_db, account, "instagram", "eckydentalcenter")
    monkeypatch.setattr(st, "_db_pool", pool_from_connection(spine_db))

    result = await st.social_account_resolve.fn(platform="instagram", handle="EckyDentalCenter")
    assert result["outcome"] == "resolved"
    assert result["account_id"] == account


@pytest.mark.asyncio
async def test_unregistered_when_no_handle_row(spine_db, monkeypatch):
    monkeypatch.setattr(st, "_db_pool", pool_from_connection(spine_db))
    result = await st.social_account_resolve.fn(platform="instagram", handle="neverseen")
    assert result["outcome"] == "unregistered"
    assert result["account_id"] is None


@pytest.mark.asyncio
async def test_inactive_when_account_deactivated(spine_db, monkeypatch):
    account = await _account(spine_db)
    await _handle(spine_db, account, "instagram", "leftroster")
    await spine_db.execute("UPDATE accounts SET is_active = false WHERE id = $1", account)
    monkeypatch.setattr(st, "_db_pool", pool_from_connection(spine_db))

    result = await st.social_account_resolve.fn(platform="instagram", handle="leftroster")
    assert result["outcome"] == "inactive"
    assert result["account_id"] == account


@pytest.mark.asyncio
async def test_former_handle_still_resolves_after_rename(spine_db, monkeypatch):
    """FR-011c: lookup must resolve current AND former handles, so a rename
    does not orphan historical records still carrying the old handle."""
    account = await _account(spine_db)
    await _handle(spine_db, account, "instagram", "oldhandle", is_current=True)
    # roster.account.record-rename's actual sequence: clear old current flag first
    # (the partial unique index rejects two current rows, checked immediately),
    # then insert/activate the new one.
    await spine_db.execute(
        "UPDATE account_handles SET is_current = false WHERE account_id = $1 AND handle_key = 'oldhandle'",
        account,
    )
    await spine_db.execute(
        """
        INSERT INTO account_handles (account_id, platform, handle_key, handle_text, identifier_kind, is_current)
        VALUES ($1, 'instagram', 'newhandle', 'newhandle', 'handle', true)
        """,
        account,
    )
    monkeypatch.setattr(st, "_db_pool", pool_from_connection(spine_db))

    old_result = await st.social_account_resolve.fn(platform="instagram", handle="oldhandle")
    new_result = await st.social_account_resolve.fn(platform="instagram", handle="newhandle")
    assert old_result["outcome"] == "resolved"
    assert new_result["outcome"] == "resolved"
    assert old_result["account_id"] == new_result["account_id"] == account


@pytest.mark.asyncio
async def test_never_creates_an_account(spine_db, monkeypatch):
    """FR-016b: the write path must never create an account, only report unregistered."""
    monkeypatch.setattr(st, "_db_pool", pool_from_connection(spine_db))
    before = await spine_db.fetchval("SELECT count(*) FROM accounts")
    await st.social_account_resolve.fn(platform="instagram", handle="brandnew")
    after = await spine_db.fetchval("SELECT count(*) FROM accounts")
    assert before == after
