"""Registry routes (US1 read scoping, US4 editing)."""
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict

from .. import db
from ..auth import Caller, current_caller
from ..deps import caller_brands, client_brand, require
from ..errors import Invalid, NotFound
from ..permissions import Action
from . import queries, service

router = APIRouter(prefix="/v1")


@router.get("/clients")
async def list_clients(
    status: str = Query("active"), brand: str | None = Query(None), caller: Caller = Depends(current_caller),
) -> list:
    if status not in {"active", "pending", "inactive", "all"}:
        raise Invalid("Status tidak dikenal.")
    async with db.pool().acquire() as conn:
        brands = await caller_brands(conn, caller)
        if brand:
            brands = [b for b in brands if b == brand]
        is_owner = any(a.role == "owner" for a in caller.assignments)
        return await queries.list_clients(conn, brands, include_unbranded=is_owner and not brand, status=status)


@router.get("/clients/{client_id}")
async def get_client(client_id: str, caller: Caller = Depends(current_caller)) -> dict:
    async with db.pool().acquire() as conn:
        brand = await client_brand(conn, caller, client_id)
        require(caller, Action.READ, brand)
        record = await queries.client_record(conn, client_id)
    if record is None:
        raise NotFound("Klien tidak ditemukan.")
    return record


# ── US4: editing ──────────────────────────────────────────────────────────────

class ClientIn(BaseModel):
    name: str
    brand: str
    status: str = "pending"
    quota_post: Optional[int] = None
    quota_story: Optional[int] = None
    quota_short_video: Optional[int] = None
    drive_folder_id: Optional[str] = None
    content_plan_folder_id: Optional[str] = None
    jira_component_id: Optional[str] = None


class ClientPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int
    name: Optional[str] = None
    status: Optional[str] = None
    quota_post: Optional[int] = None
    quota_story: Optional[int] = None
    quota_short_video: Optional[int] = None
    drive_folder_id: Optional[str] = None
    content_plan_folder_id: Optional[str] = None
    jira_component_id: Optional[str] = None


class TeamIn(BaseModel):
    version: int
    person_id: Optional[str] = None


class AccountIn(BaseModel):
    version: int
    platform: str
    handle: str
    relation: str


class AccountPatch(BaseModel):
    version: int
    relation: Optional[str] = None
    is_active: Optional[bool] = None


async def _editable(conn, caller: Caller, client_id: str) -> None:
    require(caller, Action.EDIT_REGISTRY, await client_brand(conn, caller, client_id))


async def _record(conn, client_id: str) -> dict:
    return await queries.client_record(conn, client_id)


@router.post("/clients")
async def create_client(body: ClientIn, caller: Caller = Depends(current_caller)) -> dict:
    async with db.pool().acquire() as conn:
        if body.brand not in await caller_brands(conn, caller):
            raise NotFound("Noktah Brand tidak ditemukan.")
        require(caller, Action.EDIT_REGISTRY, body.brand)
        fields = body.model_dump(exclude={"name", "brand", "status"}, exclude_none=True)
        async with conn.transaction():
            cid = await service.create_client(conn, name=body.name, brand=body.brand, status=body.status,
                                              person_id=caller.person_id, fields=fields)
        return await _record(conn, cid)


@router.patch("/clients/{client_id}")
async def update_client(client_id: str, body: ClientPatch, caller: Caller = Depends(current_caller)) -> dict:
    changes = body.model_dump(exclude={"version"}, exclude_unset=True)
    if "name" in changes:
        changes["display_name"] = changes.pop("name")
    async with db.pool().acquire() as conn:
        await _editable(conn, caller, client_id)
        async with conn.transaction():
            await service.update_client(conn, client_id, body.version, changes, caller.person_id)
        return await _record(conn, client_id)


@router.put("/clients/{client_id}/team/{team_role}")
async def set_team(client_id: str, team_role: str, body: TeamIn, caller: Caller = Depends(current_caller)) -> dict:
    async with db.pool().acquire() as conn:
        await _editable(conn, caller, client_id)
        async with conn.transaction():
            await service.set_team(conn, client_id, body.version, team_role, body.person_id, caller.person_id)
        return await _record(conn, client_id)


@router.post("/clients/{client_id}/accounts")
async def link_account(client_id: str, body: AccountIn, caller: Caller = Depends(current_caller)) -> dict:
    async with db.pool().acquire() as conn:
        await _editable(conn, caller, client_id)
        async with conn.transaction():
            await service.link_account(conn, client_id, body.version, body.platform, body.handle, body.relation,
                                       caller.person_id)
        return await _record(conn, client_id)


@router.patch("/clients/{client_id}/accounts/{account_id}")
async def update_account(client_id: str, account_id: str, body: AccountPatch,
                         caller: Caller = Depends(current_caller)) -> dict:
    async with db.pool().acquire() as conn:
        await _editable(conn, caller, client_id)
        async with conn.transaction():
            await service.update_account_link(conn, client_id, account_id, body.version, body.relation,
                                              body.is_active, caller.person_id)
        return await _record(conn, client_id)


@router.get("/clients/{client_id}/history")
async def client_history(client_id: str, caller: Caller = Depends(current_caller)) -> list:
    async with db.pool().acquire() as conn:
        require(caller, Action.READ, await client_brand(conn, caller, client_id))
        return await service.history(conn, client_id)
