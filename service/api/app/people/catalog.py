"""
The central role catalog (`unit_roles`, migration 012): which Units exist, which
roles each Unit has, which of them are Client team slots, and the permissions a
role suggests. The Hub reads roles from here, never from a list in code.
"""
from typing import Any, Dict, List, Optional

import asyncpg


async def load(conn: asyncpg.Connection) -> List[Dict[str, Any]]:
    """Every Unit with its roles, the group first, then the brands by name."""
    units = await conn.fetch(
        "SELECT id, brand_key, display_name, kind FROM noktah_brands ORDER BY kind DESC, display_name")
    roles = await conn.fetch(
        """SELECT noktah_brand_id, role, display_name, team_slot, default_permissions
           FROM unit_roles ORDER BY sort_order, display_name""")
    return [{"key": u["brand_key"], "name": u["display_name"], "kind": u["kind"],
             "roles": [{"key": r["role"], "name": r["display_name"], "team_slot": r["team_slot"],
                        "default_permissions": list(r["default_permissions"])}
                       for r in roles if r["noktah_brand_id"] == u["id"]]}
            for u in units]


async def unit_id(conn: asyncpg.Connection, unit: Optional[str]) -> Optional[str]:
    if not unit:
        return None
    return await conn.fetchval("SELECT id::text FROM noktah_brands WHERE brand_key = $1", unit)


async def role_exists(conn: asyncpg.Connection, role: str, unit: Optional[str]) -> bool:
    return bool(await conn.fetchval(
        """SELECT 1 FROM unit_roles u JOIN noktah_brands b ON b.id = u.noktah_brand_id
           WHERE u.role = $1 AND b.brand_key = $2""", role, unit))


async def team_slots(conn: asyncpg.Connection, brand: Optional[str]) -> List[str]:
    """The Client team roles of a brand, in catalog order."""
    rows = await conn.fetch(
        """SELECT u.role FROM unit_roles u JOIN noktah_brands b ON b.id = u.noktah_brand_id
           WHERE b.brand_key = $1 AND u.team_slot ORDER BY u.sort_order""", brand)
    return [r["role"] for r in rows]
