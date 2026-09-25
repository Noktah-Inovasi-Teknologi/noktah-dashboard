"""Intake routes (US2). Contract: contracts/hub-api.md → Intake."""
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from .. import db
from ..ai import budget
from ..auth import Caller, current_caller
from ..card.routes import _notify_pending
from ..deps import client_brand, definition, require
from ..errors import Forbidden, Invalid
from ..permissions import Action, Decision, is_owner
from . import pipeline, sources

router = APIRouter(prefix="/v1")


def may_run_intake_somewhere(caller: Caller) -> bool:
    return is_owner(caller.access) or "run_intake" in caller.access.permissions


class IntakeIn(BaseModel):
    kind: str
    text: Optional[str] = None
    image_base64: Optional[str] = None
    mime: Optional[str] = None
    url: Optional[str] = None
    pdf_base64: Optional[str] = None
    filename: Optional[str] = None


class DecideIn(BaseModel):
    outcome: str
    final_value: Any = None
    ticks: Dict[str, Any] = Field(default_factory=dict)
    card_version: Optional[int] = None


@router.post("/clients/{client_id}/intakes")
async def submit_intake(client_id: str, body: IntakeIn, caller: Caller = Depends(current_caller)) -> dict:
    async with db.pool().acquire() as conn:
        brand = await client_brand(conn, caller, client_id)
        require(caller, Action.RUN_INTAKE, brand)
        try:
            source = await sources.read(body.kind, text=body.text, image_base64=body.image_base64, mime=body.mime,
                                        url=body.url, pdf_base64=body.pdf_base64, filename=body.filename)
        except sources.SourceError as e:
            raise Invalid(e.message) from e
        outcome = await pipeline.run_intake(conn, definition(), client_id=client_id, source=source,
                                            submitted_by=caller.person_id)
        view = await pipeline.intake_view(conn, outcome.intake_id)
    return view | {"cached": outcome.cached, "dropped": outcome.dropped}


@router.get("/intakes/{intake_id}")
async def get_intake(intake_id: str, caller: Caller = Depends(current_caller)) -> dict:
    async with db.pool().acquire() as conn:
        view = await pipeline.intake_view(conn, intake_id)
        if view["client"] is None:
            # An unmatched old note has no brand yet: anyone who may run Intake somewhere sees it (US6).
            if not may_run_intake_somewhere(caller):
                raise Forbidden("Peran Anda tidak bisa melihat catatan ini.")
        else:
            require(caller, Action.READ, await client_brand(conn, caller, view["client"]["id"]))
    return view


@router.get("/clients/{client_id}/intakes")
async def list_intakes(client_id: str, caller: Caller = Depends(current_caller)) -> list:
    async with db.pool().acquire() as conn:
        require(caller, Action.READ, await client_brand(conn, caller, client_id))
        rows = await conn.fetch(
            """SELECT i.id::text AS id, i.kind, i.status, i.failure_reason, i.submitted_at, i.source_ref,
                      sp.display_name AS submitted_by,
                      count(p.id) AS proposals, count(p.id) FILTER (WHERE p.outcome = 'pending') AS pending
               FROM intakes i LEFT JOIN people sp ON sp.id = i.submitted_by
               LEFT JOIN intake_proposals p ON p.intake_id = i.id
               WHERE i.client_id = $1::uuid
               GROUP BY i.id, sp.display_name ORDER BY i.submitted_at DESC LIMIT 100""", client_id)
    return [{"id": r["id"], "kind": r["kind"], "status": r["status"], "failure_reason": r["failure_reason"],
             "submitted_at": r["submitted_at"].isoformat(), "submitted_by": r["submitted_by"],
             "source_ref": r["source_ref"], "proposals": r["proposals"], "pending": r["pending"]} for r in rows]


@router.post("/proposals/{proposal_id}/decide")
async def decide(proposal_id: str, body: DecideIn, caller: Caller = Depends(current_caller)) -> dict:
    d = definition()
    async with db.pool().acquire() as conn:
        proposal = await pipeline.load_proposal(conn, proposal_id)
        brand = await client_brand(conn, caller, proposal["client_id"])
        require(caller, Action.RUN_INTAKE, brand)
        pending = False
        if proposal["target"] == "guideline" and body.outcome != "reject":
            pending = require(caller, Action.EDIT_GUIDELINE, brand) is Decision.NEEDS_APPROVAL
        elif proposal["target"] == "profil" and body.outcome != "reject":
            require(caller, Action.EDIT_PROFIL, brand)
        elif proposal["target"] == "request" and body.outcome != "reject":
            require(caller, Action.EDIT_REQUESTS, brand)
        async with conn.transaction():
            result = await pipeline.decide(
                conn, d, proposal=proposal, outcome=body.outcome, final_value=body.final_value, ticks=body.ticks,
                card_version=body.card_version, person_id=caller.person_id, pending=pending,
                person_name=caller.display_name)
        if pending:
            await _notify_pending(conn, proposal["client_id"], "guideline", proposal["field_key"], caller)
        current = await pipeline.values.current_values(conn, proposal["client_id"])
        row = await conn.fetchrow(pipeline._PROPOSALS + " WHERE p.id = $1::uuid", proposal_id)
    return result | {"proposal": pipeline.proposal_view(row, current)}


@router.get("/ai/usage")
async def ai_usage(caller: Caller = Depends(current_caller)) -> dict:
    async with db.pool().acquire() as conn:
        return await budget.usage(conn)
