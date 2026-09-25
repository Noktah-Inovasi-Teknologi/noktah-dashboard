"""The Registry change log (FR-013): append-only rows of who changed what, old → new.

Every Registry and people write goes through `record_change` inside the same
transaction as the change itself, so a change without its log row cannot exist.
The sheet copy reads this table to know whether anything is left to copy (R5).
"""
import json
from typing import Any, Optional

import asyncpg


def _plain(value: Any) -> Any:
    """JSON-safe copy (dates and UUIDs as strings); the connection codec encodes it."""
    return None if value is None else json.loads(json.dumps(value, default=str, ensure_ascii=False))


async def record_change(
    conn: asyncpg.Connection,
    *,
    entity: str,
    entity_id: Optional[str],
    field: str,
    old: Any,
    new: Any,
    person_id: Optional[str],
    client_id: Optional[str] = None,
) -> Optional[int]:
    """Append one change row; returns its id. No row when old == new."""
    if old == new:
        return None
    return await conn.fetchval(
        """INSERT INTO registry_changes (entity, entity_id, client_id, field, old_value, new_value, person_id)
           VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING id""",
        entity, entity_id, client_id, field, _plain(old), _plain(new), person_id,
    )
