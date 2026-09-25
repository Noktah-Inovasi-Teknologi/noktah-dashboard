"""
Client Card values: append-only history, approvals, corrections (research R2).

State machine per (client, part, field):
    pending  → current (approved)            | rejected (reason required)
    current  → superseded (a newer value)    | corrected (fixed as a mistake)
Nothing is ever deleted (G-32). At most ONE current and ONE pending row per field
(partial unique indexes in migration 010).

Ordering matters: the unique indexes are checked immediately, so the old current
row is moved out of `current` BEFORE the new one is inserted, and `replaced_by`
(a non-deferrable FK) is filled in after the new row exists. All of it runs in
one transaction, with the Client row locked, so two writers can't interleave.

Concurrency (FR-044): every write carries the `card_version` it read; a mismatch
is refused with the current state (409). Each write bumps `clients.card_version`.
"""
import uuid
from datetime import date
from typing import Any, Dict, List, Optional

import asyncpg

from ..errors import Conflict, Invalid, NotFound
from . import definition as card_def
from .definition import Definition


async def _lock_client(conn: asyncpg.Connection, client_id: str, card_version: Optional[int]) -> asyncpg.Record:
    row = await conn.fetchrow(
        "SELECT id, display_name, card_version, noktah_brand_id FROM clients WHERE id = $1::uuid FOR UPDATE", client_id)
    if row is None:
        raise NotFound("Klien tidak ditemukan.")
    if card_version is None:
        raise Invalid("Versi kartu wajib dikirim.")
    if card_version != row["card_version"]:
        raise Conflict("Kartu sudah diubah orang lain. Muat ulang lalu coba lagi.", card_version=row["card_version"])
    return row


async def _bump(conn: asyncpg.Connection, client_id: str) -> int:
    return await conn.fetchval(
        "UPDATE clients SET card_version = card_version + 1, updated_at = now() WHERE id = $1::uuid RETURNING card_version",
        client_id)


def _clean_source(source: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    source = source or {}
    return {k: (str(source.get(k)) if source.get(k) is not None else None) for k in ("who", "where", "when")}


async def write(
    conn: asyncpg.Connection, d: Definition, *, client_id: str, part: str, field: str, value: Any,
    valid_until: Optional[date], source: Optional[Dict[str, Any]], person_id: str, card_version: Optional[int],
    pending: bool, correction: bool = False, from_proposal_id: Optional[str] = None,
    copied_from_client_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Write one value. `pending=True` for a Guideline change that needs Approval.

    Returns {id, state, card_version}. Must be called inside a transaction.
    """
    try:
        card_def.validate_value(d, part, field, value)
    except ValueError as e:
        raise Invalid(str(e)) from e
    await _lock_client(conn, client_id, card_version)
    new_id = str(uuid.uuid4())
    source_json = _clean_source(source)

    if pending:
        # A newer proposal for the same field replaces the older pending one.
        await conn.execute(
            """UPDATE card_values SET state = 'rejected', reject_reason = 'Diganti usulan yang lebih baru',
                      decided_at = now()
               WHERE client_id = $1::uuid AND part = $2 AND field_key = $3 AND state = 'pending'""",
            client_id, part, field)
        state = "pending"
        old_current = None
    else:
        old_current = await conn.fetchval(
            """UPDATE card_values SET state = $4
               WHERE client_id = $1::uuid AND part = $2 AND field_key = $3 AND state = 'current'
               RETURNING id::text""",
            client_id, part, field, "corrected" if correction else "superseded")
        state = "current"

    await conn.execute(
        """INSERT INTO card_values (id, client_id, part, field_key, definition_version, value, valid_until,
                                    source, state, set_by, from_proposal_id, copied_from_client_id)
           VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, $7, $8, $9, $10::uuid, $11::uuid, $12::uuid)""",
        new_id, client_id, part, field, d.version, value, valid_until, source_json, state, person_id,
        from_proposal_id, copied_from_client_id)
    if old_current:
        await conn.execute("UPDATE card_values SET replaced_by = $2::uuid WHERE id = $1::uuid", old_current, new_id)
    await card_def.freeze(conn, d.version)
    return {"id": new_id, "state": state, "card_version": await _bump(conn, client_id)}


async def approve(conn: asyncpg.Connection, *, value_id: str, person_id: str) -> Dict[str, Any]:
    row = await conn.fetchrow(
        "SELECT client_id::text AS client_id, part, field_key, state FROM card_values WHERE id = $1::uuid FOR UPDATE",
        value_id)
    if row is None or row["state"] != "pending":
        raise Conflict("Perubahan ini sudah diputuskan atau tidak ada lagi.")
    await conn.execute("SELECT 1 FROM clients WHERE id = $1::uuid FOR UPDATE", row["client_id"])
    old_current = await conn.fetchval(
        """UPDATE card_values SET state = 'superseded'
           WHERE client_id = $1::uuid AND part = $2 AND field_key = $3 AND state = 'current' RETURNING id::text""",
        row["client_id"], row["part"], row["field_key"])
    await conn.execute(
        "UPDATE card_values SET state = 'current', decided_by = $2::uuid, decided_at = now() WHERE id = $1::uuid",
        value_id, person_id)
    if old_current:
        await conn.execute("UPDATE card_values SET replaced_by = $2::uuid WHERE id = $1::uuid", old_current, value_id)
    return {"id": value_id, "state": "current", "card_version": await _bump(conn, row["client_id"])}


async def reject(conn: asyncpg.Connection, *, value_id: str, person_id: str, reason: str) -> Dict[str, Any]:
    if not (reason or "").strip():
        raise Invalid("Alasan penolakan wajib diisi.")
    row = await conn.fetchrow("SELECT client_id::text AS client_id, state FROM card_values WHERE id = $1::uuid FOR UPDATE",
                              value_id)
    if row is None or row["state"] != "pending":
        raise Conflict("Perubahan ini sudah diputuskan atau tidak ada lagi.")
    await conn.execute(
        """UPDATE card_values SET state = 'rejected', reject_reason = $3, decided_by = $2::uuid, decided_at = now()
           WHERE id = $1::uuid""",
        value_id, person_id, reason.strip())
    return {"id": value_id, "state": "rejected", "card_version": await _bump(conn, row["client_id"])}


def _view(r: asyncpg.Record, today: date) -> Dict[str, Any]:
    return {
        "id": r["id"], "value": r["value"],
        "valid_until": r["valid_until"].isoformat() if r["valid_until"] else None,
        "expired": bool(r["valid_until"] and r["valid_until"] < today),
        "source": r["source"], "set_by": r["set_by_name"], "set_at": r["set_at"].isoformat(),
        "from_intake": r["intake_id"],
    }


_SELECT = """SELECT v.id::text AS id, v.part, v.field_key, v.value, v.valid_until, v.source, v.state,
                    v.set_at, p.display_name AS set_by_name, ip.intake_id::text AS intake_id,
                    v.reject_reason, dp.display_name AS decided_by_name, v.decided_at
             FROM card_values v
             JOIN people p ON p.id = v.set_by
             LEFT JOIN people dp ON dp.id = v.decided_by
             LEFT JOIN intake_proposals ip ON ip.id = v.from_proposal_id"""


async def card(conn: asyncpg.Connection, d: Definition, client_id: str) -> Dict[str, Any]:
    today = date.today()
    version = await conn.fetchval("SELECT card_version FROM clients WHERE id = $1::uuid", client_id)
    rows = await conn.fetch(_SELECT + " WHERE v.client_id = $1::uuid AND v.state IN ('current', 'pending')", client_id)
    parts: Dict[str, Dict[str, Any]] = {"profil": {}, "guideline": {}}
    current_values: Dict[str, Dict[str, Any]] = {"profil": {}, "guideline": {}}
    pending: List[Dict[str, Any]] = []
    for r in rows:
        if r["state"] == "current":
            parts[r["part"]][r["field_key"]] = _view(r, today)
            current_values[r["part"]][r["field_key"]] = r["value"]
        else:
            pending.append({"id": r["id"], "part": r["part"], "field_key": r["field_key"], "value": r["value"],
                            "set_by": r["set_by_name"], "set_at": r["set_at"].isoformat()})
    open_requests = await conn.fetchval(
        "SELECT count(*) FROM client_requests WHERE client_id = $1::uuid AND status IN ('baru', 'diproses')", client_id)
    return {
        "card_version": version, "profil": parts["profil"], "guideline": parts["guideline"], "pending": pending,
        "completeness": card_def.completeness(d, current_values), "requests_open": open_requests,
    }


async def history(conn: asyncpg.Connection, client_id: str, part: str, field: str) -> List[Dict[str, Any]]:
    today = date.today()
    rows = await conn.fetch(
        _SELECT + " WHERE v.client_id = $1::uuid AND v.part = $2 AND v.field_key = $3 ORDER BY v.set_at DESC",
        client_id, part, field)
    out = []
    for r in rows:
        item = _view(r, today) | {"state": r["state"], "reject_reason": r["reject_reason"],
                                  "decided_by": r["decided_by_name"],
                                  "decided_at": r["decided_at"].isoformat() if r["decided_at"] else None}
        out.append(item)
    return out


async def current_values(conn: asyncpg.Connection, client_id: str) -> Dict[str, Dict[str, Any]]:
    rows = await conn.fetch(
        "SELECT part, field_key, value FROM card_values WHERE client_id = $1::uuid AND state = 'current'", client_id)
    out: Dict[str, Dict[str, Any]] = {"profil": {}, "guideline": {}}
    for r in rows:
        out[r["part"]][r["field_key"]] = r["value"]
    return out
