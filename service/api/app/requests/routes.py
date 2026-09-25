"""Requests: everything a Client asked for, word for word, with its status (G-26)."""
from datetime import date
from typing import Optional

import asyncpg
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from .. import db
from ..auth import Caller, current_caller
from ..deps import client_brand, require
from ..errors import Invalid, NotFound, check_version
from ..permissions import Action

router = APIRouter(prefix="/v1")
STATUSES = ("baru", "diproses", "selesai", "ditolak")
CHANNELS = ("whatsapp_group", "meeting", "email", "lainnya")


class RequestIn(BaseModel):
    requested_on: date
    text: str
    requested_by: Optional[str] = None
    is_pic: bool = False
    channel: str = "whatsapp_group"
    link: Optional[str] = None


class RequestPatch(BaseModel):
    version: int
    status: Optional[str] = None
    reject_reason: Optional[str] = None
    link: Optional[str] = None


def _view(r: asyncpg.Record) -> dict:
    return {
        "id": r["id"], "requested_on": r["requested_on"].isoformat(), "text": r["text"],
        "requested_by": r["requested_by"], "is_pic": r["is_pic"], "channel": r["channel"], "status": r["status"],
        "reject_reason": r["reject_reason"], "link": r["link"], "version": r["version"],
        "created_by": r["created_by_name"], "created_at": r["created_at"].isoformat(),
    }


_SELECT = """SELECT r.id::text AS id, r.requested_on, r.text, r.requested_by, r.is_pic, r.channel, r.status,
                    r.reject_reason, r.link, r.version, r.created_at, p.display_name AS created_by_name,
                    r.client_id::text AS client_id
             FROM client_requests r JOIN people p ON p.id = r.created_by"""


async def create_request(conn: asyncpg.Connection, *, client_id: str, body: RequestIn, person_id: str,
                         from_proposal_id: Optional[str] = None) -> dict:
    if not body.text.strip():
        raise Invalid("Isi permintaan wajib diisi.")
    if body.channel not in CHANNELS:
        raise Invalid("Kanal tidak dikenal.")
    rid = await conn.fetchval(
        """INSERT INTO client_requests (client_id, requested_on, text, requested_by, is_pic, channel, link,
                                        from_proposal_id, created_by)
           VALUES ($1::uuid, $2, $3, $4, $5, $6, $7, $8::uuid, $9::uuid) RETURNING id::text""",
        client_id, body.requested_on, body.text.strip(), body.requested_by, body.is_pic, body.channel, body.link,
        from_proposal_id, person_id)
    await conn.execute(
        "INSERT INTO client_request_events (request_id, from_status, to_status, person_id) VALUES ($1::uuid, NULL, 'baru', $2::uuid)",
        rid, person_id)
    return _view(await conn.fetchrow(_SELECT + " WHERE r.id = $1::uuid", rid))


@router.get("/clients/{client_id}/requests")
async def list_requests(client_id: str, status: Optional[str] = Query(None), caller: Caller = Depends(current_caller)) -> list:
    async with db.pool().acquire() as conn:
        brand = await client_brand(conn, caller, client_id)
        require(caller, Action.READ, brand)
        rows = await conn.fetch(
            _SELECT + " WHERE r.client_id = $1::uuid AND ($2::text IS NULL OR r.status = $2) "
                      "ORDER BY r.requested_on DESC, r.created_at DESC", client_id, status)
    return [_view(r) for r in rows]


@router.post("/clients/{client_id}/requests")
async def add_request(client_id: str, body: RequestIn, caller: Caller = Depends(current_caller)) -> dict:
    async with db.pool().acquire() as conn:
        brand = await client_brand(conn, caller, client_id)
        require(caller, Action.EDIT_REQUESTS, brand)
        async with conn.transaction():
            return await create_request(conn, client_id=client_id, body=body, person_id=caller.person_id)


@router.patch("/requests/{request_id}")
async def patch_request(request_id: str, body: RequestPatch, caller: Caller = Depends(current_caller)) -> dict:
    async with db.pool().acquire() as conn:
        row = await conn.fetchrow(_SELECT + " WHERE r.id = $1::uuid", request_id)
        if row is None:
            raise NotFound("Permintaan tidak ditemukan.")
        brand = await client_brand(conn, caller, row["client_id"])
        require(caller, Action.EDIT_REQUESTS, brand)
        if body.status is not None and body.status not in STATUSES:
            raise Invalid("Status tidak dikenal.")
        if body.status == "ditolak" and not (body.reject_reason or "").strip():
            raise Invalid("Alasan penolakan wajib diisi.")
        async with conn.transaction():
            locked = await conn.fetchrow("SELECT version, status FROM client_requests WHERE id = $1::uuid FOR UPDATE",
                                         request_id)
            check_version(body.version, locked["version"], _view(row))
            new_status = body.status or locked["status"]
            await conn.execute(
                """UPDATE client_requests SET status = $2, reject_reason = $3, link = COALESCE($4, link),
                          version = version + 1, updated_at = now() WHERE id = $1::uuid""",
                request_id, new_status, body.reject_reason if new_status == "ditolak" else None, body.link)
            if new_status != locked["status"]:
                await conn.execute(
                    """INSERT INTO client_request_events (request_id, from_status, to_status, reason, person_id)
                       VALUES ($1::uuid, $2, $3, $4, $5::uuid)""",
                    request_id, locked["status"], new_status, body.reject_reason, caller.person_id)
            return _view(await conn.fetchrow(_SELECT + " WHERE r.id = $1::uuid", request_id))
