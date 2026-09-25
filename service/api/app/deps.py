"""Helpers every /v1 route shares: the card definition, Noktah Brand lookups, and
permission checks that turn `permissions.can()` into the right HTTP answer.

Scoping (G-10): a Client in a Noktah Brand the caller can't see answers 404, not
403, so another Noktah Brand's Clients don't even reveal that they exist.
"""
from typing import List, Optional

import asyncpg

from .auth import Caller
from .card.definition import Definition
from .errors import Forbidden, NotFound
from .permissions import Action, Decision, can, visible_brands

_definition: Optional[Definition] = None


def set_definition(definition: Definition) -> None:
    global _definition
    _definition = definition


def definition() -> Definition:
    if _definition is None:
        raise RuntimeError("card definition not loaded")
    return _definition


async def all_brand_keys(conn: asyncpg.Connection) -> List[str]:
    return [r["brand_key"] for r in await conn.fetch("SELECT brand_key FROM noktah_brands WHERE kind = 'brand' ORDER BY brand_key")]


async def caller_brands(conn: asyncpg.Connection, caller: Caller) -> List[str]:
    return sorted(visible_brands(caller.access, await all_brand_keys(conn)))


async def client_brand(conn: asyncpg.Connection, caller: Caller, client_id: str) -> str:
    """The Client's Noktah Brand key, or NotFound if it doesn't exist or isn't visible."""
    row = await conn.fetchrow(
        """SELECT b.brand_key FROM clients c LEFT JOIN noktah_brands b ON b.id = c.noktah_brand_id
           WHERE c.id = $1::uuid""",
        client_id,
    )
    if row is None:
        raise NotFound("Klien tidak ditemukan.")
    brand = row["brand_key"]
    if brand is None:
        # Not yet imported into a Noktah Brand: only the Owner may see it.
        if any(a.role == "owner" for a in caller.assignments):
            return ""
        raise NotFound("Klien tidak ditemukan.")
    if brand not in visible_brands(caller.access, [brand]):
        raise NotFound("Klien tidak ditemukan.")
    return brand


def require(caller: Caller, action: Action, brand: Optional[str]) -> Decision:
    """ALLOW or NEEDS_APPROVAL passes (the caller handles the latter); DENY → 403."""
    decision = can(caller.access, action, brand or None)
    if decision is Decision.DENY:
        raise Forbidden("Peran Anda tidak mengizinkan tindakan ini.")
    return decision
