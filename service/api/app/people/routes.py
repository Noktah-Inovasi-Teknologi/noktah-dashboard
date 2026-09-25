"""Identity (/v1/me) and People management (US1, US5)."""
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field

from .. import db
from ..auth import Caller, current_caller
from ..deps import caller_brands
from ..errors import Invalid, NotFound
from ..permissions import Action, Decision, can
from ..registry.changes import record_change
from . import service

router = APIRouter(prefix="/v1")


@router.get("/me")
async def me(caller: Caller = Depends(current_caller)) -> dict:
    """Who is signed in, their roles, and what the interface should offer them."""
    async with db.pool().acquire() as conn:
        brands = await caller_brands(conn, caller)

    def anywhere(action: Action) -> bool:
        return any(can(caller.assignments, action, b) is not Decision.DENY for b in brands) or \
            can(caller.assignments, action, None) is not Decision.DENY

    return {
        "person": {"id": caller.person_id, "display_name": caller.display_name, "email": caller.email},
        "roles": [{"role": a.role, "noktah_brand": a.brand} for a in caller.assignments],
        "brands": brands,
        "can": {
            "edit_registry": anywhere(Action.EDIT_REGISTRY),
            "edit_profil": anywhere(Action.EDIT_PROFIL),
            "edit_guideline": anywhere(Action.EDIT_GUIDELINE),
            "approve": anywhere(Action.APPROVE_GUIDELINE),
            "manage_people": anywhere(Action.MANAGE_PEOPLE),
            "appoint_bm": anywhere(Action.APPOINT_BRAND_MANAGER),
            "run_intake": anywhere(Action.RUN_INTAKE),
        },
    }


# ── US5: People and roles ─────────────────────────────────────────────────────

class PersonIn(BaseModel):
    display_name: str
    emails: List[str] = Field(default_factory=list)
    jira_account_id: Optional[str] = None
    slack_user_id: Optional[str] = None


class PersonPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int
    display_name: Optional[str] = None
    jira_account_id: Optional[str] = None
    slack_user_id: Optional[str] = None
    status: Optional[str] = None


class EmailIn(BaseModel):
    email: str


class RoleIn(BaseModel):
    role: str
    brand: Optional[str] = None


async def _loaded(conn, caller: Caller, person_id: str) -> dict:
    return await service.get_person(conn, caller, await caller_brands(conn, caller), person_id)


async def _editable(conn, caller: Caller, person_id: str) -> None:
    person = await _loaded(conn, caller, person_id)
    service.require_manage(caller, person["roles"])


@router.get("/people")
async def list_people(status: str = Query("active"), caller: Caller = Depends(current_caller)) -> list:
    if status not in ("active", "left", "all"):
        raise Invalid("Status harus active, left, atau all.")
    async with db.pool().acquire() as conn:
        return await service.list_people(conn, caller, await caller_brands(conn, caller), status)


@router.get("/people/{person_id}")
async def get_person(person_id: str, caller: Caller = Depends(current_caller)) -> dict:
    async with db.pool().acquire() as conn:
        return await _loaded(conn, caller, person_id)


@router.post("/people")
async def create_person(body: PersonIn, caller: Caller = Depends(current_caller)) -> dict:
    service.require_manage(caller, [])
    async with db.pool().acquire() as conn:
        async with conn.transaction():
            pid = await service.create_person(conn, display_name=body.display_name, emails=body.emails,
                                              jira_account_id=body.jira_account_id, slack_user_id=body.slack_user_id,
                                              by=caller.person_id)
        return await _loaded(conn, caller, pid)


@router.patch("/people/{person_id}")
async def update_person(person_id: str, body: PersonPatch, caller: Caller = Depends(current_caller)) -> dict:
    async with db.pool().acquire() as conn:
        await _editable(conn, caller, person_id)
        if person_id == caller.person_id and body.status == "left":
            raise Invalid("Anda tidak bisa menandai diri sendiri keluar.")
        async with conn.transaction():
            await service.update_person(conn, person_id, body.version,
                                        body.model_dump(exclude={"version"}, exclude_unset=True), caller.person_id)
        return await _loaded(conn, caller, person_id)


@router.post("/people/{person_id}/emails")
async def add_email(person_id: str, body: EmailIn, caller: Caller = Depends(current_caller)) -> dict:
    async with db.pool().acquire() as conn:
        await _editable(conn, caller, person_id)
        async with conn.transaction():
            await service.add_email(conn, person_id, body.email, caller.person_id)
        return await _loaded(conn, caller, person_id)


@router.delete("/people/{person_id}/emails/{email}")
async def remove_email(person_id: str, email: str, caller: Caller = Depends(current_caller)) -> dict:
    async with db.pool().acquire() as conn:
        await _editable(conn, caller, person_id)
        async with conn.transaction():
            gone = await conn.fetchval("DELETE FROM person_emails WHERE person_id = $1::uuid AND email = $2 RETURNING email",
                                       person_id, email.strip().lower())
            if gone is None:
                raise NotFound("Email tidak ditemukan pada orang ini.")
            await record_change(conn, entity="person_email", entity_id=person_id, field="email", old=gone, new=None,
                                person_id=caller.person_id)
        return await _loaded(conn, caller, person_id)


@router.post("/people/{person_id}/roles")
async def grant_role(person_id: str, body: RoleIn, caller: Caller = Depends(current_caller)) -> dict:
    async with db.pool().acquire() as conn:
        await _loaded(conn, caller, person_id)  # visibility; the grant rule itself is may_grant
        async with conn.transaction():
            await service.grant_role(conn, caller, person_id, body.role, body.brand)
        return await _loaded(conn, caller, person_id)


@router.post("/people/{person_id}/roles/{role_id}/end")
async def end_role(person_id: str, role_id: str, caller: Caller = Depends(current_caller)) -> dict:
    async with db.pool().acquire() as conn:
        await _loaded(conn, caller, person_id)
        async with conn.transaction():
            await service.end_role(conn, caller, person_id, role_id)
        return await _loaded(conn, caller, person_id)
