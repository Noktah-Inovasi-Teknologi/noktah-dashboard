"""
Constraint tests for the relational-spine schema (feature 004-relational-spine).

These assert the database-level uniqueness rules from data-model.md / research R7
directly — inserting rows and expecting asyncpg to raise a UniqueViolationError —
because the defect this feature fixes is a matching rule that lived in code where
it could be silently wrong. Re-testing the rule only at the application layer would
reproduce that; these tests hold the schema itself to account.

Requires a real disposable PostgreSQL database (see tests/conftest.py). Skipped
automatically when SPINE_TEST_DATABASE_URL is unreachable.
"""
import asyncpg
import pytest

pytestmark = pytest.mark.schema


async def _insert_client(conn, key: str, name: str) -> str:
    row = await conn.fetchrow(
        "INSERT INTO clients (client_key, display_name) VALUES ($1, $2) RETURNING id",
        key, name,
    )
    return row["id"]


async def _insert_account(conn, platform: str = "instagram") -> str:
    row = await conn.fetchrow(
        "INSERT INTO accounts (platform) VALUES ($1) RETURNING id", platform,
    )
    return row["id"]


async def _insert_handle(conn, account_id, platform: str, handle: str, is_current: bool = True):
    await conn.execute(
        """
        INSERT INTO account_handles (account_id, platform, handle_key, handle_text, identifier_kind, is_current)
        VALUES ($1, $2, $3, $4, 'handle', $5)
        """,
        account_id, platform, handle.lower(), handle, is_current,
    )


# --------------------------------------------------------------------------
# FR-011c: a handle resolves to one account, per platform
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_duplicate_handle_same_platform_rejected(spine_db):
    a1 = await _insert_account(spine_db, "instagram")
    a2 = await _insert_account(spine_db, "instagram")
    await _insert_handle(spine_db, a1, "instagram", "sameclient")
    with pytest.raises(asyncpg.UniqueViolationError):
        await _insert_handle(spine_db, a2, "instagram", "sameclient")


@pytest.mark.asyncio
async def test_same_handle_text_different_platform_is_two_accounts(spine_db):
    """Per the spec's edge case: same display handle on two platforms is two accounts."""
    a1 = await _insert_account(spine_db, "instagram")
    a2 = await _insert_account(spine_db, "tiktok")
    await _insert_handle(spine_db, a1, "instagram", "sameclient")
    # Must NOT raise — different platform, same handle_key is allowed.
    await _insert_handle(spine_db, a2, "tiktok", "sameclient")
    count = await spine_db.fetchval("SELECT count(*) FROM account_handles WHERE handle_key = 'sameclient'")
    assert count == 2


# --------------------------------------------------------------------------
# FR-009: at most one `owned` relationship per account
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_second_owner_of_same_account_rejected(spine_db):
    c1 = await _insert_client(spine_db, "client-a", "Client A")
    c2 = await _insert_client(spine_db, "client-b", "Client B")
    account = await _insert_account(spine_db)
    await spine_db.execute(
        "INSERT INTO client_account_roles (client_id, account_id, role) VALUES ($1, $2, 'owned')",
        c1, account,
    )
    with pytest.raises(asyncpg.UniqueViolationError):
        await spine_db.execute(
            "INSERT INTO client_account_roles (client_id, account_id, role) VALUES ($1, $2, 'owned')",
            c2, account,
        )


@pytest.mark.asyncio
async def test_owner_handover_permitted_when_old_owner_deactivated(spine_db):
    """A handover — old owner deactivated, new owner added — must be representable."""
    c1 = await _insert_client(spine_db, "client-a", "Client A")
    c2 = await _insert_client(spine_db, "client-b", "Client B")
    account = await _insert_account(spine_db)
    await spine_db.execute(
        "INSERT INTO client_account_roles (client_id, account_id, role) VALUES ($1, $2, 'owned')",
        c1, account,
    )
    await spine_db.execute(
        "UPDATE client_account_roles SET is_active = false WHERE client_id = $1 AND account_id = $2",
        c1, account,
    )
    # Must NOT raise — the partial index only counts active owned relationships.
    await spine_db.execute(
        "INSERT INTO client_account_roles (client_id, account_id, role) VALUES ($1, $2, 'owned')",
        c2, account,
    )


@pytest.mark.asyncio
async def test_competitor_and_reference_roles_do_not_trigger_owned_uniqueness(spine_db):
    """Only `owned` is capped at one; other roles may repeat across clients for the same account."""
    c1 = await _insert_client(spine_db, "client-a", "Client A")
    c2 = await _insert_client(spine_db, "client-b", "Client B")
    account = await _insert_account(spine_db)
    await spine_db.execute(
        "INSERT INTO client_account_roles (client_id, account_id, role) VALUES ($1, $2, 'competitor')",
        c1, account,
    )
    # Must NOT raise — a shared competitor is the whole point of FR-008.
    await spine_db.execute(
        "INSERT INTO client_account_roles (client_id, account_id, role) VALUES ($1, $2, 'competitor')",
        c2, account,
    )


# --------------------------------------------------------------------------
# FR-010: one relationship per client-account pair
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_second_relationship_for_same_pair_rejected(spine_db):
    client = await _insert_client(spine_db, "client-a", "Client A")
    account = await _insert_account(spine_db)
    await spine_db.execute(
        "INSERT INTO client_account_roles (client_id, account_id, role) VALUES ($1, $2, 'competitor')",
        client, account,
    )
    with pytest.raises(asyncpg.UniqueViolationError):
        await spine_db.execute(
            "INSERT INTO client_account_roles (client_id, account_id, role) VALUES ($1, $2, 'reference')",
            client, account,
        )


# --------------------------------------------------------------------------
# One current handle per account
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_two_current_handles_for_same_account_rejected(spine_db):
    account = await _insert_account(spine_db)
    await _insert_handle(spine_db, account, "instagram", "oldhandle", is_current=True)
    with pytest.raises(asyncpg.UniqueViolationError):
        await _insert_handle(spine_db, account, "instagram", "newhandle", is_current=True)


@pytest.mark.asyncio
async def test_rename_sequence_clear_old_then_activate_new(spine_db):
    """The only order that never violates `uq_account_handle_current` mid-transaction:
    the partial unique index is checked immediately (it is a plain index, not a
    deferrable constraint), so setting the new row current WHILE the old row is
    still current fails instantly. Clearing the old row first leaves zero current
    rows momentarily (always valid), then activating the new row restores exactly
    one. Both handles must remain resolvable afterward."""
    account = await _insert_account(spine_db)
    await _insert_handle(spine_db, account, "instagram", "oldhandle", is_current=True)

    await spine_db.execute(
        """
        INSERT INTO account_handles (account_id, platform, handle_key, handle_text, identifier_kind, is_current)
        VALUES ($1, 'instagram', 'newhandle', 'newhandle', 'handle', false)
        """,
        account,
    )
    await spine_db.execute(
        "UPDATE account_handles SET is_current = false WHERE account_id = $1 AND handle_key = 'oldhandle'",
        account,
    )
    await spine_db.execute(
        "UPDATE account_handles SET is_current = true WHERE account_id = $1 AND handle_key = 'newhandle'",
        account,
    )

    old = await spine_db.fetchval(
        "SELECT account_id FROM account_handles WHERE handle_key = 'oldhandle' AND platform = 'instagram'"
    )
    new = await spine_db.fetchval(
        "SELECT account_id FROM account_handles WHERE handle_key = 'newhandle' AND platform = 'instagram'"
    )
    assert old == new == account


@pytest.mark.asyncio
async def test_setting_new_current_before_clearing_old_is_rejected(spine_db):
    """Documents WHY the order in the test above is mandatory: the naive
    'insert new as current, then clear old' sequence violates the partial
    unique index the instant the new row is inserted, because both are
    momentarily current."""
    account = await _insert_account(spine_db)
    await _insert_handle(spine_db, account, "instagram", "oldhandle", is_current=True)
    with pytest.raises(asyncpg.UniqueViolationError):
        await spine_db.execute(
            """
            INSERT INTO account_handles (account_id, platform, handle_key, handle_text, identifier_kind, is_current)
            VALUES ($1, 'instagram', 'newhandle', 'newhandle', 'handle', true)
            """,
            account,
        )


# --------------------------------------------------------------------------
# FR-003: an alias resolves to at most one client
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_alias_conflict_rejected(spine_db):
    c1 = await _insert_client(spine_db, "client-a", "Client A")
    c2 = await _insert_client(spine_db, "client-b", "Client B")
    await spine_db.execute(
        "INSERT INTO client_aliases (client_id, alias_key, alias_text, source) VALUES ($1, $2, $3, 'manual')",
        c1, "shared name", "Shared Name",
    )
    with pytest.raises(asyncpg.UniqueViolationError):
        await spine_db.execute(
            "INSERT INTO client_aliases (client_id, alias_key, alias_text, source) VALUES ($1, $2, $3, 'manual')",
            c2, "shared name", "Shared Name",
        )


# --------------------------------------------------------------------------
# Composite FK: a handle's platform must match its account's
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_handle_platform_mismatch_rejected(spine_db):
    account = await _insert_account(spine_db, "instagram")
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await _insert_handle(spine_db, account, "tiktok", "wrongplatform")


# --------------------------------------------------------------------------
# FR-013a: follower_count is NOT NULL by design — no placeholder rows
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_follower_observation_cannot_be_null(spine_db):
    account = await _insert_account(spine_db)
    with pytest.raises(asyncpg.NotNullViolationError):
        await spine_db.execute(
            "INSERT INTO account_follower_observations (account_id, follower_count) VALUES ($1, NULL)",
            account,
        )
