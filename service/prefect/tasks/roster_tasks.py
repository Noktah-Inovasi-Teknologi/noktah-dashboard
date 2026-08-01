"""
Roster reconciliation tasks (feature 004-relational-spine).

Single-responsibility upserts for clients, aliases, accounts, and client-account
roles. Each public `@task` opens its own pool and delegates to a plain `_*_impl`
function that takes an already-open connection — the same plain functions are
called directly (sharing one connection/transaction) by the reconciliation
engine in flows/common/roster.py, so the two entry points can never diverge.

Tasks raise on failure so Prefect's retry mechanism can handle transient DB
errors (constitution I). A UNIQUE violation from `client_aliases.alias_key` is
deliberately NOT swallowed here — FR-003 requires a conflict to be rejected and
reported, and the caller (the engine) is what decides how to report it.
"""
import logging
import os
import uuid
from typing import Any, Dict, Optional

import asyncpg
from prefect import task

try:
    from ..db import db_pool
    from ..hashmap import normalize_client_key as _normalize_key
except ImportError:
    import sys
    sys.path.append(os.path.dirname(os.path.dirname(__file__)))
    from db import db_pool
    from hashmap import normalize_client_key as _normalize_key

logger = logging.getLogger(__name__)


async def _db_pool() -> asyncpg.Pool:
    return await db_pool("SPINE_DB_URL")


# ---------------------------------------------------------------------------
# Plain implementations — share one caller-supplied connection
# ---------------------------------------------------------------------------


async def _upsert_client_impl(conn: asyncpg.Connection, client_key: str, display_name: str, dry_run: bool = False) -> Dict[str, Any]:
    existing = await conn.fetchrow(
        "SELECT id, display_name, is_active FROM clients WHERE client_key = $1", client_key
    )
    if existing is None:
        if dry_run:
            return {"client_id": None, "action": "created"}
        row = await conn.fetchrow(
            "INSERT INTO clients (client_key, display_name) VALUES ($1, $2) RETURNING id",
            client_key, display_name,
        )
        return {"client_id": row["id"], "action": "created"}

    changed = False
    if existing["display_name"] != display_name:
        # Preserve the former name as a resolvable alias before overwriting it,
        # so a rename never breaks a lookup by the old display name.
        former_key = _normalize_key(existing["display_name"])
        if not dry_run:
            try:
                await conn.execute(
                    "INSERT INTO client_aliases (client_id, alias_key, alias_text, source) VALUES ($1, $2, $3, 'former_name')",
                    existing["id"], former_key, existing["display_name"],
                )
            except asyncpg.UniqueViolationError:
                pass  # already aliased under this name
            await conn.execute(
                "UPDATE clients SET display_name = $2, updated_at = now() WHERE id = $1",
                existing["id"], display_name,
            )
        changed = True
    if not existing["is_active"]:
        if not dry_run:
            await conn.execute("UPDATE clients SET is_active = true, updated_at = now() WHERE id = $1", existing["id"])
        changed = True
    return {"client_id": existing["id"], "action": "updated" if changed else "unchanged"}


async def _upsert_alias_impl(
    conn: asyncpg.Connection, client_id: Optional[str], alias_text: str, source: str, dry_run: bool = False
) -> Dict[str, Any]:
    alias_key = _normalize_key(alias_text)
    existing = await conn.fetchrow("SELECT client_id FROM client_aliases WHERE alias_key = $1", alias_key)
    if existing is not None:
        if client_id is not None and str(existing["client_id"]) != str(client_id):
            return {"action": "conflict", "alias_key": alias_key, "already_owned_by_client_id": str(existing["client_id"])}
        return {"action": "unchanged", "alias_key": alias_key}
    if dry_run:
        return {"action": "created", "alias_key": alias_key}
    await conn.execute(
        "INSERT INTO client_aliases (client_id, alias_key, alias_text, source) VALUES ($1, $2, $3, $4)",
        client_id, alias_key, alias_text, source,
    )
    return {"action": "created", "alias_key": alias_key}


async def _upsert_account_impl(
    conn: asyncpg.Connection, platform: str, handle_text: str, identifier_kind: str = "handle", dry_run: bool = False
) -> Dict[str, Any]:
    handle_key = handle_text.strip().lower()
    existing = await conn.fetchrow(
        """
        SELECT ah.account_id, a.is_active FROM account_handles ah
        JOIN accounts a ON a.id = ah.account_id
        WHERE ah.platform = $1 AND ah.handle_key = $2
        """,
        platform, handle_key,
    )
    if existing is not None:
        if not existing["is_active"]:
            if not dry_run:
                await conn.execute(
                    "UPDATE accounts SET is_active = true, updated_at = now() WHERE id = $1", existing["account_id"]
                )
            return {"account_id": existing["account_id"], "action": "reactivated"}
        return {"account_id": existing["account_id"], "action": "unchanged"}

    if dry_run:
        # Random placeholder, not None — a caller may use this id in a further
        # dry-run lookup (e.g. the owned-role check), and a UUID column binds
        # a random UUID safely while correctly matching nothing that exists yet.
        return {"account_id": uuid.uuid4(), "action": "created"}
    account_id = await conn.fetchval("INSERT INTO accounts (platform) VALUES ($1) RETURNING id", platform)
    await conn.execute(
        """
        INSERT INTO account_handles (account_id, platform, handle_key, handle_text, identifier_kind, is_current)
        VALUES ($1, $2, $3, $4, $5, true)
        """,
        account_id, platform, handle_key, handle_text, identifier_kind,
    )
    return {"account_id": account_id, "action": "created"}


async def _upsert_role_impl(
    conn: asyncpg.Connection, client_id: str, account_id: str, role: str, dry_run: bool = False
) -> Dict[str, Any]:
    existing = await conn.fetchrow(
        "SELECT role, is_active FROM client_account_roles WHERE client_id = $1 AND account_id = $2",
        client_id, account_id,
    )
    if existing is None:
        if dry_run:
            return {"action": "created"}
        await conn.execute(
            "INSERT INTO client_account_roles (client_id, account_id, role) VALUES ($1, $2, $3)",
            client_id, account_id, role,
        )
        return {"action": "created"}
    if existing["role"] != role or not existing["is_active"]:
        if dry_run:
            return {"action": "changed"}
        await conn.execute(
            "UPDATE client_account_roles SET role = $3, is_active = true, updated_at = now() "
            "WHERE client_id = $1 AND account_id = $2",
            client_id, account_id, role,
        )
        return {"action": "changed"}
    return {"action": "unchanged"}


async def _record_rename_impl(
    conn: asyncpg.Connection, account_id: str, platform: str, new_handle_text: str, identifier_kind: str = "handle"
) -> Dict[str, Any]:
    """FR-011b: an explicit maintainer action, never inferred. Rejects — never
    merges — when the new handle already belongs to a different account.

    Ordering is load-bearing: `uq_account_handle_current` is a plain partial
    UNIQUE INDEX, not a deferrable constraint, so it is checked immediately per
    statement. Setting the new row current WHILE the old row is still current
    would violate it instantly. The only safe order is clear-old-then-activate-new
    (see tests/test_spine_schema.py::test_setting_new_current_before_clearing_old_is_rejected
    for the failure this avoids).
    """
    new_handle_key = new_handle_text.strip().lower()
    conflict = await conn.fetchrow(
        "SELECT account_id FROM account_handles WHERE platform = $1 AND handle_key = $2",
        platform, new_handle_key,
    )
    if conflict is not None and str(conflict["account_id"]) != str(account_id):
        raise ValueError(
            f"Handle '{new_handle_text}' on {platform} already belongs to a different account "
            f"({conflict['account_id']}) — this is the shape of an account merge, which FR-011b "
            f"forbids inferring. Resolve manually before recording this rename."
        )

    async with conn.transaction():
        await conn.execute(
            "UPDATE account_handles SET is_current = false WHERE account_id = $1 AND is_current",
            account_id,
        )
        if conflict is None:
            await conn.execute(
                """
                INSERT INTO account_handles (account_id, platform, handle_key, handle_text, identifier_kind, is_current)
                VALUES ($1, $2, $3, $4, $5, true)
                """,
                account_id, platform, new_handle_key, new_handle_text, identifier_kind,
            )
        else:
            await conn.execute(
                "UPDATE account_handles SET is_current = true, last_seen_at = now() "
                "WHERE account_id = $1 AND platform = $2 AND handle_key = $3",
                account_id, platform, new_handle_key,
            )
    return {"account_id": account_id, "new_handle": new_handle_text}


async def _alias_reassign_impl(conn: asyncpg.Connection, alias_key: str, new_client_id: str) -> Dict[str, Any]:
    """SC-009: the only path allowed to change an alias's client binding. Also
    re-links any knowledge_records already bound to the OLD client through this
    alias, so the correction doesn't leave records pointing at the wrong client."""
    row = await conn.fetchrow("SELECT client_id, alias_text FROM client_aliases WHERE alias_key = $1", alias_key)
    if row is None:
        raise ValueError(f"No alias '{alias_key}' to reassign")
    old_client_id = row["client_id"]

    async with conn.transaction():
        await conn.execute(
            "UPDATE client_aliases SET client_id = $2, source = 'manual' WHERE alias_key = $1",
            alias_key, new_client_id,
        )
        relink_result = await conn.execute(
            """
            UPDATE knowledge_records SET client_id = $2
            WHERE client_id = $1 AND lower(regexp_replace(btrim(client_name), '\\s+', ' ', 'g')) = $3
            """,
            old_client_id, new_client_id, alias_key,
        )
        relinked = int(relink_result.split()[-1]) if relink_result.startswith("UPDATE") else 0
    return {
        "alias_key": alias_key, "old_client_id": old_client_id, "new_client_id": new_client_id,
        "knowledge_records_relinked": relinked,
    }


# ---------------------------------------------------------------------------
# Prefect tasks — the flow-facing entry points
# ---------------------------------------------------------------------------


@task(name="roster.client.upsert", retries=2, retry_delay_seconds=10)
async def roster_client_upsert(client_key: str, display_name: str) -> Dict[str, Any]:
    pool = await _db_pool()
    try:
        async with pool.acquire() as conn:
            return await _upsert_client_impl(conn, client_key, display_name)
    finally:
        await pool.close()


@task(name="roster.alias.upsert", retries=2, retry_delay_seconds=10)
async def roster_alias_upsert(client_id: Optional[str], alias_text: str, source: str) -> Dict[str, Any]:
    pool = await _db_pool()
    try:
        async with pool.acquire() as conn:
            return await _upsert_alias_impl(conn, client_id, alias_text, source)
    finally:
        await pool.close()


@task(name="roster.account.upsert", retries=2, retry_delay_seconds=10)
async def roster_account_upsert(platform: str, handle_text: str, identifier_kind: str = "handle") -> Dict[str, Any]:
    pool = await _db_pool()
    try:
        async with pool.acquire() as conn:
            return await _upsert_account_impl(conn, platform, handle_text, identifier_kind)
    finally:
        await pool.close()


@task(name="roster.role.upsert", retries=2, retry_delay_seconds=10)
async def roster_role_upsert(client_id: str, account_id: str, role: str) -> Dict[str, Any]:
    pool = await _db_pool()
    try:
        async with pool.acquire() as conn:
            return await _upsert_role_impl(conn, client_id, account_id, role)
    finally:
        await pool.close()


@task(name="roster.account.record-rename", retries=0)
async def roster_account_record_rename(account_id: str, platform: str, new_handle_text: str) -> Dict[str, Any]:
    """Not retried — a rename is a one-shot maintainer action; retrying a raised
    ValueError (merge conflict) would just raise the identical error again."""
    pool = await _db_pool()
    try:
        async with pool.acquire() as conn:
            return await _record_rename_impl(conn, account_id, platform, new_handle_text)
    finally:
        await pool.close()


@task(name="roster.alias.reassign", retries=0)
async def roster_alias_reassign(alias_key: str, new_client_id: str) -> Dict[str, Any]:
    """Not retried — a one-shot maintainer correction (SC-009)."""
    pool = await _db_pool()
    try:
        async with pool.acquire() as conn:
            return await _alias_reassign_impl(conn, alias_key, new_client_id)
    finally:
        await pool.close()
