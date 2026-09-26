"""Identity (/v1/me) and People management (US1, US5)."""
from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field

from .. import db
from ..auth import Caller, current_caller
from ..ai import costs
from ..deps import caller_brands
from ..errors import Invalid
from ..permissions import PERMISSIONS, Action, Decision, can, is_owner, may_manage, may_set_unit
from . import catalog, service

router = APIRouter(prefix="/v1")


@router.get("/me")
async def me(caller: Caller = Depends(current_caller)) -> dict:
    """Who is signed in, what they may do, and the catalog of Units and roles."""
    async with db.pool().acquire() as conn:
        brands = await caller_brands(conn, caller)
        units = await catalog.load(conn)
    access = caller.access

    def anywhere(action: Action) -> bool:
        return any(can(access, action, b) is not Decision.DENY for b in brands) or             can(access, action, None) is not Decision.DENY

    return {
        "person": {"id": caller.person_id, "display_name": caller.display_name, "email": caller.email},
        "roles": [{"role": a.role, "noktah_brand": a.brand} for a in caller.assignments],
        "units": sorted(access.units),
        "permissions": list(PERMISSIONS) if is_owner(access) else [p for p in PERMISSIONS if p in access.permissions],
        "brands": brands,
        # Units this Person may put people in, and their roles; the forms offer these.
        "manageable_units": [u["key"] for u in units if may_set_unit(access, u["key"])],
        "catalog": units,
        "can": {
            "edit_registry": anywhere(Action.EDIT_REGISTRY),
            "edit_profil": anywhere(Action.EDIT_PROFIL),
            "edit_guideline": anywhere(Action.EDIT_GUIDELINE),
            "approve": anywhere(Action.APPROVE_GUIDELINE),
            "edit_requests": anywhere(Action.EDIT_REQUESTS),
            "manage_people": may_manage(access),
            "appoint_bm": anywhere(Action.APPOINT_BRAND_MANAGER),
            "run_intake": anywhere(Action.RUN_INTAKE),
            "view_ai_costs": costs.may_view(caller),
            # Otomasi & Laporan (spec 009): Eskala only
            "manage_automation": can(access, Action.MANAGE_AUTOMATION, "eskala") is Decision.ALLOW,
            "view_reports": can(access, Action.VIEW_REPORTS, "eskala") is Decision.ALLOW,
            "view_incentive": can(access, Action.VIEW_INCENTIVE, "eskala") is Decision.ALLOW,
        },
    }


# ── US5: People and roles ─────────────────────────────────────────────────────

class RoleIn(BaseModel):
    role: str
    brand: Optional[str] = None  # the role's Unit (brand_key)


class PersonIn(BaseModel):
    display_name: str
    emails: List[str] = Field(default_factory=list)
    jira_account_id: Optional[str] = None
    slack_user_id: Optional[str] = None
    units: List[str] = Field(default_factory=list)
    roles: List[RoleIn] = Field(default_factory=list)
    permissions: List[str] = Field(default_factory=list)


class PersonSave(BaseModel):
    """The whole profile form: what the Person should look like after the save."""
    model_config = ConfigDict(extra="forbid")
    version: int
    display_name: str
    jira_account_id: Optional[str] = None
    slack_user_id: Optional[str] = None
    emails: List[str]
    units: List[str]
    roles: List[RoleIn]
    permissions: List[str]
    # "Mulai bekerja" (spec 009 G-27). Optional so an older form that doesn't send it keeps the date.
    started_on: Optional[date] = None


class PersonPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int
    display_name: Optional[str] = None
    jira_account_id: Optional[str] = None
    slack_user_id: Optional[str] = None
    status: Optional[str] = None
    started_on: Optional[date] = None


class EmailIn(BaseModel):
    email: str


async def _loaded(conn, caller: Caller, person_id: str) -> dict:
    return await service.get_person(conn, caller, person_id)


async def _editable(conn, caller: Caller, person_id: str) -> None:
    person = await _loaded(conn, caller, person_id)
    service.require_manage(caller, person["units"], person["roles"])


@router.get("/people")
async def list_people(status: str = Query("active"), caller: Caller = Depends(current_caller)) -> list:
    if status not in ("active", "left", "all"):
        raise Invalid("Status harus active, left, atau all.")
    async with db.pool().acquire() as conn:
        return await service.list_people(conn, caller, status)


@router.get("/people/{person_id}")
async def get_person(person_id: str, caller: Caller = Depends(current_caller)) -> dict:
    async with db.pool().acquire() as conn:
        return await _loaded(conn, caller, person_id)


@router.post("/people")
async def create_person(body: PersonIn, caller: Caller = Depends(current_caller)) -> dict:
    service.require_manage(caller, [], [])
    async with db.pool().acquire() as conn:
        async with conn.transaction():
            pid = await service.create_person(conn, display_name=body.display_name,
                                              jira_account_id=body.jira_account_id, slack_user_id=body.slack_user_id,
                                              by=caller.person_id)
            await service.save_person(
                conn, caller, pid, version=0, fields={}, emails=body.emails, units=body.units,
                roles=[(r.role, r.brand) for r in body.roles], permissions=body.permissions)
        return await _loaded(conn, caller, pid)


@router.put("/people/{person_id}")
async def save_person(person_id: str, body: PersonSave, caller: Caller = Depends(current_caller)) -> dict:
    async with db.pool().acquire() as conn:
        await _loaded(conn, caller, person_id)  # visibility; each change checks its own rule
        async with conn.transaction():
            await service.save_person(
                conn, caller, person_id, version=body.version,
                fields={"display_name": body.display_name, "jira_account_id": body.jira_account_id,
                        "slack_user_id": body.slack_user_id},
                emails=body.emails, units=body.units, roles=[(r.role, r.brand) for r in body.roles],
                permissions=body.permissions)
            if "started_on" in body.model_fields_set:
                person = await _loaded(conn, caller, person_id)
                service.require_manage(caller, person["units"], person["roles"])
                await service.update_person(conn, person_id, None, {"started_on": body.started_on}, caller.person_id)
        return await _loaded(conn, caller, person_id)


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
            await service.remove_email(conn, person_id, email, caller.person_id)
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
