"""
Tests for the one-time spine backfill (feature 004-relational-spine),
flows/spine_backfill.py, per data-model.md "Backfill" and research.md R2.

Runs against a real disposable Postgres — the linking logic depends on the
`account_handles` uniqueness rules and the harvest tables' existing columns.
"""
import os
import sys

import pytest

_PREFECT_ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, _PREFECT_ROOT)
sys.path.insert(0, os.path.join(_PREFECT_ROOT, "flows"))
os.environ.setdefault("HARVEST_DB_URL", "postgresql://test:test@localhost/test")

import spine_backfill as backfill  # noqa: E402

pytestmark = pytest.mark.schema


async def _seed_account_with_handle(conn, platform, handle):
    account = await conn.fetchval(
        "INSERT INTO accounts (platform) VALUES ($1) RETURNING id", platform
    )
    await conn.execute(
        """
        INSERT INTO account_handles (account_id, platform, handle_key, handle_text, identifier_kind, is_current)
        VALUES ($1, $2, $3, $4, 'handle', true)
        """,
        account, platform, handle.lower(), handle,
    )
    return account


async def _seed_signal(conn, platform, profile_key, content_id):
    await conn.execute(
        """
        INSERT INTO harvested_signals (platform, profile_key, content_id, content_type)
        VALUES ($1, $2, $3, 'video')
        """,
        platform, profile_key, content_id,
    )


async def _seed_item(conn, platform, profile_key, content_id):
    await conn.execute(
        """
        INSERT INTO harvested_items (platform, profile_key, content_id, drive_target, content_type)
        VALUES ($1, $2, $3, 'folder-x', 'video')
        """,
        platform, profile_key, content_id,
    )


@pytest.mark.asyncio
async def test_signals_linked_to_matching_account(spine_db):
    account = await _seed_account_with_handle(spine_db, "instagram", "eckydentalcenter")
    await _seed_signal(spine_db, "instagram", "eckydentalcenter", "c1")

    await backfill.backfill_spine(spine_db)

    linked = await spine_db.fetchval("SELECT account_id FROM harvested_signals WHERE content_id = 'c1'")
    assert linked == account


@pytest.mark.asyncio
async def test_items_linked_to_matching_account(spine_db):
    account = await _seed_account_with_handle(spine_db, "tiktok", "karungjumbosidoarjo")
    await _seed_item(spine_db, "tiktok", "karungjumbosidoarjo", "i1")

    await backfill.backfill_spine(spine_db)

    linked = await spine_db.fetchval("SELECT account_id FROM harvested_items WHERE content_id = 'i1'")
    assert linked == account


@pytest.mark.asyncio
async def test_case_insensitive_match(spine_db):
    account = await _seed_account_with_handle(spine_db, "instagram", "eckydentalcenter")
    await _seed_signal(spine_db, "instagram", "EckyDentalCenter", "c2")  # different case in ledger

    await backfill.backfill_spine(spine_db)

    linked = await spine_db.fetchval("SELECT account_id FROM harvested_signals WHERE content_id = 'c2'")
    assert linked == account


@pytest.mark.asyncio
async def test_unmatched_handle_becomes_account_with_no_role(spine_db):
    """rsmatadryap-style handle: no account, no roster entry, resolves to nothing.
    Must be created as an account with no client_account_roles row, not dropped."""
    await _seed_signal(spine_db, "instagram", "rsmatadryap", "c3")

    report = await backfill.backfill_spine(spine_db)

    linked = await spine_db.fetchval("SELECT account_id FROM harvested_signals WHERE content_id = 'c3'")
    assert linked is not None
    roles = await spine_db.fetchval(
        "SELECT count(*) FROM client_account_roles WHERE account_id = $1", linked
    )
    assert roles == 0
    assert report["summary"]["accounts_created"] >= 1


@pytest.mark.asyncio
async def test_opaque_identifier_recorded_as_opaque(spine_db):
    opaque = "MS4wLjABAAAAbSCgATHrl-SzAZ8n9B1Up_QTp2tEdIY1xm4T3CGUrbpF-Y51s2woFGy2z1gLaawf"
    await _seed_item(spine_db, "tiktok", opaque, "i4")

    await backfill.backfill_spine(spine_db)

    kind = await spine_db.fetchval(
        "SELECT identifier_kind FROM account_handles WHERE handle_key = lower($1)", opaque
    )
    assert kind == "opaque_id"


@pytest.mark.asyncio
async def test_zero_unlinked_after_backfill(spine_db):
    await _seed_account_with_handle(spine_db, "instagram", "acct1")
    await _seed_signal(spine_db, "instagram", "acct1", "s1")
    await _seed_signal(spine_db, "instagram", "unregistered1", "s2")  # no account anywhere
    await _seed_item(spine_db, "instagram", "acct1", "i1")

    await backfill.backfill_spine(spine_db)

    unlinked_signals = await spine_db.fetchval("SELECT count(*) FROM harvested_signals WHERE account_id IS NULL")
    unlinked_items = await spine_db.fetchval("SELECT count(*) FROM harvested_items WHERE account_id IS NULL")
    assert unlinked_signals == 0  # SC-001: even the unregistered one gets a no-role account
    assert unlinked_items == 0


@pytest.mark.asyncio
async def test_dry_run_writes_nothing(spine_db):
    await _seed_signal(spine_db, "instagram", "someacct", "c5")
    report = await backfill.backfill_spine(spine_db, dry_run=True)
    assert report["summary"]["accounts_created"] >= 1  # reports what WOULD happen
    linked = await spine_db.fetchval("SELECT account_id FROM harvested_signals WHERE content_id = 'c5'")
    assert linked is None  # but nothing was written
    accounts = await spine_db.fetchval("SELECT count(*) FROM accounts")
    assert accounts == 0


@pytest.mark.asyncio
async def test_idempotent_rerun(spine_db):
    await _seed_account_with_handle(spine_db, "instagram", "acct1")
    await _seed_signal(spine_db, "instagram", "acct1", "s1")
    await _seed_signal(spine_db, "instagram", "novel", "s2")

    await backfill.backfill_spine(spine_db)
    accounts_after_first = await spine_db.fetchval("SELECT count(*) FROM accounts")

    await backfill.backfill_spine(spine_db)
    accounts_after_second = await spine_db.fetchval("SELECT count(*) FROM accounts")

    assert accounts_after_first == accounts_after_second
