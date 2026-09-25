"""Client Card routes and Approvals (US3). Contract: contracts/hub-api.md → Client Card."""
from datetime import date
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from .. import db, notify
from ..auth import Caller, current_caller
from ..deps import caller_brands, client_brand, definition, require
from ..errors import Invalid, NotFound
from ..permissions import Action, Decision
from ..settings import get_settings
from . import values

router = APIRouter(prefix="/v1")
PARTS = {"profil": Action.EDIT_PROFIL, "guideline": Action.EDIT_GUIDELINE}


class ValueIn(BaseModel):
    card_version: int
    value: Any
    valid_until: Optional[date] = None
    source: Optional[Dict[str, Any]] = None


class CopyIn(BaseModel):
    card_version: int
    from_client_id: str


class RejectIn(BaseModel):
    reason: str


def _part_action(part: str) -> Action:
    if part not in PARTS:
        raise NotFound("Bagian kartu tidak dikenal.")
    return PARTS[part]


@router.get("/card-definition")
async def card_definition(caller: Caller = Depends(current_caller)) -> dict:
    return definition().body


async def _summary(conn, client_id: str) -> Optional[dict]:
    from ..summary.service import summary_view
    return await summary_view(conn, client_id)


@router.get("/clients/{client_id}/card")
async def get_card(client_id: str, caller: Caller = Depends(current_caller)) -> dict:
    async with db.pool().acquire() as conn:
        brand = await client_brand(conn, caller, client_id)
        require(caller, Action.READ, brand)
        result = await values.card(conn, definition(), client_id)
        result["summary"] = await _summary(conn, client_id)
    return result


async def _notify_pending(conn, client_id: str, part: str, field: str, caller: Caller) -> None:
    row = await conn.fetchrow(
        """SELECT c.display_name, b.slack_managerial_env FROM clients c
           LEFT JOIN noktah_brands b ON b.id = c.noktah_brand_id WHERE c.id = $1::uuid""", client_id)
    label = definition().field(part, field)["label"]
    await notify.approval_pending(
        row["slack_managerial_env"] if row else None, row["display_name"] if row else "?", label,
        caller.display_name, f"{get_settings().public_hub_url}/approvals")


async def _write(client_id: str, part: str, field: str, body: ValueIn, caller: Caller, correction: bool) -> dict:
    action = _part_action(part)
    async with db.pool().acquire() as conn:
        brand = await client_brand(conn, caller, client_id)
        decision = require(caller, action, brand)
        pending = decision is Decision.NEEDS_APPROVAL
        async with conn.transaction():
            result = await values.write(
                conn, definition(), client_id=client_id, part=part, field=field, value=body.value,
                valid_until=body.valid_until, source=body.source, person_id=caller.person_id,
                card_version=body.card_version, pending=pending, correction=correction)
        if pending:
            await _notify_pending(conn, client_id, part, field, caller)
    return result


@router.put("/clients/{client_id}/card/{part}/{field}")
async def put_value(client_id: str, part: str, field: str, body: ValueIn,
                    caller: Caller = Depends(current_caller)) -> dict:
    return await _write(client_id, part, field, body, caller, correction=False)


@router.post("/clients/{client_id}/card/{part}/{field}/correct")
async def correct_value(client_id: str, part: str, field: str, body: ValueIn,
                        caller: Caller = Depends(current_caller)) -> dict:
    return await _write(client_id, part, field, body, caller, correction=True)


@router.get("/clients/{client_id}/card/{part}/{field}/history")
async def value_history(client_id: str, part: str, field: str, caller: Caller = Depends(current_caller)) -> list:
    _part_action(part)
    async with db.pool().acquire() as conn:
        brand = await client_brand(conn, caller, client_id)
        require(caller, Action.READ, brand)
        return await values.history(conn, client_id, part, field)


@router.post("/clients/{client_id}/card/guideline/{field}/copy-from")
async def copy_from(client_id: str, field: str, body: CopyIn, caller: Caller = Depends(current_caller)) -> dict:
    """'Copy from another client' (G-3): same Noktah Brand only; approval rules apply."""
    async with db.pool().acquire() as conn:
        brand = await client_brand(conn, caller, client_id)
        source_brand = await client_brand(conn, caller, body.from_client_id)
        if source_brand != brand:
            raise Invalid("Hanya bisa menyalin dari klien di Noktah Brand yang sama.")
        decision = require(caller, Action.EDIT_GUIDELINE, brand)
        src = await conn.fetchrow(
            """SELECT value, valid_until FROM card_values
               WHERE client_id = $1::uuid AND part = 'guideline' AND field_key = $2 AND state = 'current'""",
            body.from_client_id, field)
        if src is None:
            raise Invalid("Klien sumber belum punya isian untuk bagian ini.")
        src_name = await conn.fetchval("SELECT display_name FROM clients WHERE id = $1::uuid", body.from_client_id)
        pending = decision is Decision.NEEDS_APPROVAL
        async with conn.transaction():
            result = await values.write(
                conn, definition(), client_id=client_id, part="guideline", field=field, value=src["value"],
                valid_until=src["valid_until"],
                source={"who": caller.display_name, "where": f"Disalin dari {src_name}", "when": date.today().isoformat()},
                person_id=caller.person_id, card_version=body.card_version, pending=pending,
                copied_from_client_id=body.from_client_id)
        if pending:
            await _notify_pending(conn, client_id, "guideline", field, caller)
    return result


@router.get("/approvals")
async def list_approvals(caller: Caller = Depends(current_caller)) -> list:
    async with db.pool().acquire() as conn:
        brands = await caller_brands(conn, caller)
        allowed = [b for b in brands if require_quiet(caller, b)]
        rows = await conn.fetch(
            """SELECT v.id::text AS id, c.id::text AS client_id, c.display_name AS client_name, v.part, v.field_key,
                      v.value AS proposed_value, cur.value AS current_value, p.display_name AS set_by, v.set_at
               FROM card_values v
               JOIN clients c ON c.id = v.client_id
               JOIN noktah_brands b ON b.id = c.noktah_brand_id
               JOIN people p ON p.id = v.set_by
               LEFT JOIN card_values cur ON cur.client_id = v.client_id AND cur.part = v.part
                                        AND cur.field_key = v.field_key AND cur.state = 'current'
               WHERE v.state = 'pending' AND b.brand_key = ANY($1::text[])
               ORDER BY v.set_at""",
            allowed)
    return [{"id": r["id"], "client": {"id": r["client_id"], "name": r["client_name"]}, "part": r["part"],
             "field_key": r["field_key"], "current_value": r["current_value"], "proposed_value": r["proposed_value"],
             "set_by": r["set_by"], "set_at": r["set_at"].isoformat()} for r in rows]


def require_quiet(caller: Caller, brand: str) -> bool:
    from ..permissions import can
    return can(caller.access, Action.APPROVE_GUIDELINE, brand) is Decision.ALLOW


async def _pending_brand(conn, caller: Caller, value_id: str) -> None:
    row = await conn.fetchrow(
        """SELECT v.client_id::text AS client_id FROM card_values v WHERE v.id = $1::uuid""", value_id)
    if row is None:
        raise NotFound("Perubahan tidak ditemukan.")
    brand = await client_brand(conn, caller, row["client_id"])
    decision = require(caller, Action.APPROVE_GUIDELINE, brand)
    if decision is not Decision.ALLOW:
        raise Invalid("Peran Anda tidak bisa menyetujui perubahan ini.")


@router.post("/approvals/{value_id}/approve")
async def approve(value_id: str, caller: Caller = Depends(current_caller)) -> dict:
    async with db.pool().acquire() as conn:
        await _pending_brand(conn, caller, value_id)
        async with conn.transaction():
            return await values.approve(conn, value_id=value_id, person_id=caller.person_id)


@router.post("/approvals/{value_id}/reject")
async def reject(value_id: str, body: RejectIn, caller: Caller = Depends(current_caller)) -> dict:
    async with db.pool().acquire() as conn:
        await _pending_brand(conn, caller, value_id)
        async with conn.transaction():
            return await values.reject(conn, value_id=value_id, person_id=caller.person_id, reason=body.reason)
