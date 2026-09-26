"""/v1 Laporan → Insentif and sanctions (spec 009 US8/US9), behind "Lihat poin insentif" in
Eskala (G-23); /internal/sanctions/* for the hub-sanctions flow."""
from collections import defaultdict
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from .. import db
from ..auth import Caller, current_caller
from ..deps import require
from ..permissions import Action
from ..reports import jira_store, station_report
from ..automation.store import month_of
from . import events as event_store
from . import ladder, sanctions

router = APIRouter(prefix="/v1")
internal = APIRouter()

TEAM_REWARD_SHARE = 0.6


def _gate(caller: Caller) -> None:
    require(caller, Action.VIEW_INCENTIVE, "eskala")


class HoldIn(BaseModel):
    reason: str


class RecordIn(BaseModel):
    person_id: str
    level: str
    issued_on: date
    category_code: Optional[str] = None
    note: Optional[str] = None


class TickIn(BaseModel):
    now: Optional[datetime] = None


class LetterResults(BaseModel):
    results: List[Dict[str, Any]] = Field(default_factory=list)


def _event(e: event_store.Event) -> Dict[str, Any]:
    o = e.outcome
    return {"key": e.key, "summary": e.summary, "type": o.kind, "category": o.category, "points": o.points,
            "state": o.state, "reason": o.reason, "missing": o.missing, "formula": o.formula,
            "direct_item": o.direct_item, "status": e.status,
            "created_at": e.created_at.isoformat(), "judged_at": e.judged_at.isoformat() if e.judged_at else None}


async def _team_reward(conn, month: date) -> Dict[str, Dict[str, Any]]:
    """Per person: of the Client teams they're on, how many met both thresholds (v2.1 §5.2)."""
    rep = await station_report.report(conn, month)
    met = {t["client"]["id"]: t["both_met"] for t in rep["teams"] if t["client"]["id"]}
    rows = await conn.fetch(
        """SELECT DISTINCT person_id::text AS person_id, client_id::text AS client_id FROM client_team_assignments
           WHERE team_role IN ('content_planner', 'field_associate', 'content_editor', 'qc', 'quality_assurance')
             AND valid_from < $2 AND (valid_to IS NULL OR valid_to > $1)""",
        datetime.combine(month, datetime.min.time()),
        datetime.combine(date(month.year + (month.month == 12), month.month % 12 + 1, 1), datetime.min.time()))
    teams: Dict[str, List[str]] = defaultdict(list)
    for r in rows:
        if r["client_id"] in met:
            teams[r["person_id"]].append(r["client_id"])
    out = {}
    for pid, clients in teams.items():
        n_met = sum(1 for c in clients if met.get(c))
        out[pid] = {"teams": len(clients), "teams_met": n_met,
                    "qualifies": bool(clients) and n_met / len(clients) >= TEAM_REWARD_SHARE}
    return out


@router.get("/reports/incentive")
async def incentive(month: str = Query(...), caller: Caller = Depends(current_caller)) -> dict:
    _gate(caller)
    m = month_of(month)
    async with db.pool().acquire() as conn:
        evs = await event_store.load(conn)
        in_month = [e for e in evs if e.month == m]
        reward = await _team_reward(conn, m)
        people_rows = await conn.fetch(
            """SELECT DISTINCT p.id::text AS id, p.display_name FROM people p
               JOIN person_units u ON u.person_id = p.id JOIN noktah_brands b ON b.id = u.noktah_brand_id
               WHERE b.brand_key = 'eskala' AND p.status = 'active' ORDER BY p.display_name""")
        sanction_rows = await conn.fetch(
            f"""SELECT {sanctions.SANCTION_COLUMNS} FROM sanctions s
                WHERE s.period = $1 OR (s.source <> 'ladder' AND date_trunc('month', s.created_at) = $1::timestamp)
                   OR s.state IN ('computed', 'held', 'on_appeal', 'flagged')
                ORDER BY s.created_at""", m)
        refreshed = await jira_store.refreshed_at(conn)
    by_person: Dict[str, List[event_store.Event]] = defaultdict(list)
    for e in in_month:
        if e.person_id:
            by_person[e.person_id].append(e)
    san_by_person: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for s in sanction_rows:
        san_by_person[s["person_id"]].append(sanctions.as_dict(s))
    people = []
    names = {r["id"]: r["display_name"] for r in people_rows}
    for pid in sorted(set(names) | set(by_person) | set(san_by_person), key=lambda p: names.get(p, "")):
        evs_p = by_person.get(pid, [])
        counted = [e for e in evs_p if e.outcome.counts]
        people.append({
            "person": {"id": pid, "name": names.get(pid) or next((e.person_name for e in evs_p if e.person_name), "-")},
            "violation_points": sum(e.outcome.points for e in counted if e.outcome.kind == "violation"),
            "excellence_points": sum(e.outcome.points for e in counted if e.outcome.kind == "excellence"),
            "sanctions": san_by_person.get(pid, []),
            "sanction": (san_by_person.get(pid) or [None])[-1],
            "team_reward": reward.get(pid, {"teams": 0, "teams_met": 0, "qualifies": False}),
            "events": [_event(e) for e in evs_p]})
    return {
        "month": m.isoformat()[:7], "refreshed_at": refreshed, "people": people,
        "incomplete": [{"key": e.key, "summary": e.summary, "missing": e.outcome.missing,
                        "person": e.person_name} for e in in_month if e.outcome.state == "belum_lengkap"],
        "unmatched": [{"key": e.key, "account_id": e.account_id} for e in in_month
                      if e.person_id is None and e.outcome.state not in ("not_judged",)],
        "direct_sanctions_manual": [{"key": e.key, "person": e.person_name, "summary": e.summary}
                                    for e in in_month if e.outcome.category == "H2" and not e.outcome.direct_item
                                    and e.outcome.state not in ("not_judged", "reference")],
        "levels": ladder.LABELS,
    }


@router.get("/sanctions")
async def list_sanctions(person_id: Optional[str] = None, caller: Caller = Depends(current_caller)) -> dict:
    _gate(caller)
    async with db.pool().acquire() as conn:
        rows = await conn.fetch(
            f"""SELECT {sanctions.SANCTION_COLUMNS}, p.display_name FROM sanctions s JOIN people p ON p.id = s.person_id
                WHERE $1::uuid IS NULL OR s.person_id = $1::uuid ORDER BY s.created_at DESC LIMIT 200""", person_id)
    return {"sanctions": [sanctions.as_dict(r) | {"person": {"id": r["person_id"], "name": r["display_name"]}}
                          for r in rows]}


@router.post("/sanctions")
async def record_sanction(body: RecordIn, caller: Caller = Depends(current_caller)) -> dict:
    _gate(caller)
    async with db.pool().acquire() as conn:
        sid = await sanctions.record(conn, person_id=body.person_id, level=body.level, issued_on=body.issued_on,
                                     category_code=body.category_code, note=body.note, by=caller.person_id)
        row = await conn.fetchrow(f"SELECT {sanctions.SANCTION_COLUMNS} FROM sanctions s WHERE s.id = $1::uuid", sid)
    return sanctions.as_dict(row)


@router.post("/sanctions/{sanction_id}/hold")
async def hold_sanction(sanction_id: str, body: HoldIn, caller: Caller = Depends(current_caller)) -> dict:
    _gate(caller)
    async with db.pool().acquire() as conn:
        async with conn.transaction():
            await sanctions.hold(conn, sanction_id, body.reason, caller.person_id)
        row = await conn.fetchrow(f"SELECT {sanctions.SANCTION_COLUMNS} FROM sanctions s WHERE s.id = $1::uuid",
                                  sanction_id)
    return sanctions.as_dict(row)


@router.post("/sanctions/{sanction_id}/release")
async def release_sanction(sanction_id: str, caller: Caller = Depends(current_caller)) -> dict:
    _gate(caller)
    async with db.pool().acquire() as conn:
        async with conn.transaction():
            await sanctions.release(conn, sanction_id)
        row = await conn.fetchrow(f"SELECT {sanctions.SANCTION_COLUMNS} FROM sanctions s WHERE s.id = $1::uuid",
                                  sanction_id)
    return sanctions.as_dict(row)


@internal.post("/sanctions/tick")
async def tick(body: TickIn) -> dict:
    async with db.pool().acquire() as conn:
        async with conn.transaction():
            return await sanctions.tick(conn, body.now)


@internal.post("/sanctions/letters")
async def letters_done(body: LetterResults) -> dict:
    async with db.pool().acquire() as conn:
        await sanctions.record_letters(conn, body.results)
    return {}
