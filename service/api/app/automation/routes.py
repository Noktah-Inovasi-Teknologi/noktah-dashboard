"""/v1 Otomasi (spec 009 US1–US4): Content Plans, Greenlight, "Buat issue Jira", the
Harvest and review marks. Every route needs "Kelola otomasi" in Eskala (G-39)."""
from typing import List

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from .. import db, prefect_api
from ..auth import Caller, current_caller
from ..deps import require
from ..errors import Conflict, Invalid, NotFound
from ..permissions import Action
from . import batches, harvest, store

router = APIRouter(prefix="/v1")

AUTOMATIONS = (
    {"key": "jira", "label": "Jira", "deployments": [prefect_api.PLAN_WATCH, prefect_api.JIRA_CREATE,
                                                     prefect_api.JIRA_SYNC]},
    {"key": "harvest", "label": "Harvest", "deployments": [prefect_api.HARVEST]},
)


def _gate(caller: Caller) -> None:
    require(caller, Action.MANAGE_AUTOMATION, store.ESKALA)


class GreenlightIn(BaseModel):
    fingerprint: str


class BatchIn(BaseModel):
    plan_ids: List[str]


class MarksIn(BaseModel):
    marks: List[str]


@router.get("/automations")
async def automations(caller: Caller = Depends(current_caller)) -> dict:
    _gate(caller)
    out = []
    for a in AUTOMATIONS:
        runs = await prefect_api.runs_for(a["deployments"])
        if runs is None:
            out.append({**a, "deployments": [d.split("/")[-1] for d in a["deployments"]], "unavailable": True,
                        "last_run": None, "next_run_at": None, "history": []})
            continue
        history = runs["history"]
        finished = [h for h in history if h["state"] not in ("RUNNING", "PENDING")]
        out.append({**a, "deployments": [d.split("/")[-1] for d in a["deployments"]], "unavailable": False,
                    "last_run": history[0] if history else None,
                    "failures": sum(1 for h in finished if h["state"] in ("FAILED", "CRASHED")),
                    "next_run_at": runs["next_run_at"], "history": history})
    return {"automations": out}


def _plan_summary(plan, client, gl, made, flags) -> dict:
    rows = (plan["rows"] or []) if plan else []
    from . import plans as rules
    return {
        "id": plan["id"] if plan else None, "client": {"id": client["id"], "name": client["display_name"]},
        "state": plan["state"] if plan else "not_scanned",
        "file_name": plan["file_name"] if plan else None,
        "file_url": f"https://docs.google.com/spreadsheets/d/{plan['drive_file_id']}/edit"
        if plan and plan["drive_file_id"] else None,
        "problem": plan["problem"] if plan else ("Belum ada folder Content Plan di Registry."
                                                 if not client["content_plan_folder_id"] else "Belum diperiksa."),
        "rows": len(rules.content_rows(rows)), "issues": len(made),
        "status": store.status_of(plan, gl, made, flags) if plan else "not_scanned",
        "greenlit": {"by": gl["by"], "at": gl["at"].isoformat()} if gl else None,
        "open_flags": len(flags),
        "scanned_at": plan["scanned_at"].isoformat() if plan else None,
    }


@router.get("/content-plans")
async def list_plans(month: str = Query(...), caller: Caller = Depends(current_caller)) -> dict:
    _gate(caller)
    m = store.month_of(month)
    async with db.pool().acquire() as conn:
        out = []
        for c in await store.eskala_clients(conn):
            row = await conn.fetchrow("SELECT id::text AS id FROM content_plans WHERE client_id = $1::uuid AND month = $2",
                                      c["id"], m)
            plan = await store.load_plan(conn, row["id"]) if row else None
            gl = await store.latest_greenlight(conn, row["id"]) if row else None
            made = await store.issue_rows(conn, row["id"]) if row else {}
            flags = await store.open_flags(conn, row["id"]) if row else []
            out.append(_plan_summary(plan, c, gl, made, flags))
    scanned = [p["scanned_at"] for p in out if p["scanned_at"]]
    return {"month": m.isoformat()[:7], "scanned_at": max(scanned) if scanned else None, "plans": out}


async def _plan_detail(conn, plan_id: str) -> dict:
    plan = await store.load_plan(conn, plan_id)
    gl = await store.latest_greenlight(conn, plan_id)
    made = await store.issue_rows(conn, plan_id)
    flags = await store.open_flags(conn, plan_id)
    a = await store.assessment(conn, plan)
    from . import plans as rules
    by_row = {}
    for f in flags:
        by_row.setdefault(f["row_number"], []).append(f["kind"])
    rows = [{"row_number": r["row_number"], "tanggal": rules.norm(r["cells"].get("Tanggal")),
             "bentuk": rules.norm(r["cells"].get("Bentuk")), "topik": rules.norm(r["cells"].get("Topik")),
             "issue_key": rules.key_of(r["cells"]) or made.get(r["row_number"]),
             "flags": by_row.get(r["row_number"], [])} for r in rules.content_rows(plan["rows"] or [])]
    greenlights = await conn.fetch(
        """SELECT p.display_name AS by, g.at FROM content_plan_greenlights g JOIN people p ON p.id = g.person_id
           WHERE g.plan_id = $1::uuid ORDER BY g.at DESC LIMIT 10""", plan_id)
    status = store.status_of(plan, gl, made, flags)
    remaining = store.rows_without_issue(plan["rows"] or [], made)
    greenlit = gl is not None and gl["fingerprint"] == plan["fingerprint"]
    return {
        "id": plan["id"], "client": {"id": plan["client_id"], "name": plan["client_name"]},
        "month": plan["month"].isoformat()[:7], "state": plan["state"], "status": status,
        "file_name": plan["file_name"], "problem": plan["problem"], "fingerprint": plan["fingerprint"],
        "file_url": f"https://docs.google.com/spreadsheets/d/{plan['drive_file_id']}/edit" if plan["drive_file_id"] else None,
        "scanned_at": plan["scanned_at"].isoformat(),
        "quota": a["quota"], "blocking": a["blocking"], "warnings": a["warnings"],
        "team": {"field_associate": (a["team"].get("field_associate") or {}).get("display_name"),
                 "content_editor": (a["team"].get("content_editor") or {}).get("display_name")},
        "rows": rows, "rows_without_issue": len(remaining),
        "flags": [{"id": f["id"], "kind": f["kind"], "issue_key": f["issue_key"], "row_number": f["row_number"],
                   "changes": f["changes"] or [], "detected_at": f["detected_at"].isoformat(),
                   "comment_state": f["comment_state"]} for f in flags],
        "greenlights": [{"by": g["by"], "at": g["at"].isoformat()} for g in greenlights],
        "can_greenlight": plan["state"] == "found" and not a["blocking"] and not greenlit and bool(remaining),
        "can_create": greenlit and not a["blocking"] and bool(remaining),
    }


@router.get("/content-plans/{plan_id}")
async def get_plan(plan_id: str, caller: Caller = Depends(current_caller)) -> dict:
    _gate(caller)
    async with db.pool().acquire() as conn:
        return await _plan_detail(conn, plan_id)


@router.post("/content-plans/{plan_id}/rescan", status_code=202)
async def rescan(plan_id: str, caller: Caller = Depends(current_caller)) -> dict:
    _gate(caller)
    async with db.pool().acquire() as conn:
        await store.load_plan(conn, plan_id)
    return {"flow_run_id": await prefect_api.run_deployment(prefect_api.PLAN_WATCH, {"plan_ids": [plan_id]})}


@router.post("/content-plans/scan-month", status_code=202)
async def scan_month(month: str = Query(...), caller: Caller = Depends(current_caller)) -> dict:
    """"Periksa sekarang" on the month list: scan every plan of that month now."""
    _gate(caller)
    m = store.month_of(month)
    return {"flow_run_id": await prefect_api.run_deployment(prefect_api.PLAN_WATCH, {"month": m.isoformat()[:7]})}


@router.post("/content-plans/{plan_id}/greenlight")
async def greenlight(plan_id: str, body: GreenlightIn, caller: Caller = Depends(current_caller)) -> dict:
    _gate(caller)
    async with db.pool().acquire() as conn:
        async with conn.transaction():
            plan = await store.load_plan(conn, plan_id)
            if plan["fingerprint"] != body.fingerprint:
                raise Conflict("Content Plan berubah sejak halaman ini dibuka. Periksa isi terbarunya, lalu beri Greenlight lagi.")
            a = await store.assessment(conn, plan)
            if a["blocking"]:
                raise Invalid(a["blocking"][0]["message"], blocking=a["blocking"])
            await conn.execute(
                """INSERT INTO content_plan_greenlights (plan_id, fingerprint, rows, person_id)
                   VALUES ($1::uuid, $2, $3::jsonb, $4::uuid)""",
                plan_id, plan["fingerprint"], plan["rows"] or [], caller.person_id)
        return await _plan_detail(conn, plan_id)


@router.post("/content-plans/flags/{flag_id}/resolve")
async def resolve_flag(flag_id: str, caller: Caller = Depends(current_caller)) -> dict:
    _gate(caller)
    async with db.pool().acquire() as conn:
        done = await conn.fetchval(
            """UPDATE content_plan_flags SET resolved_at = now(), resolved_by = $2::uuid
               WHERE id = $1::uuid AND resolved_at IS NULL RETURNING plan_id::text""", flag_id, caller.person_id)
        if done is None:
            raise NotFound("Perubahan ini tidak ditemukan atau sudah ditandai selesai.")
        return await _plan_detail(conn, done)


@router.post("/jira-batches", status_code=202)
async def create_batch(body: BatchIn, caller: Caller = Depends(current_caller)) -> dict:
    _gate(caller)
    async with db.pool().acquire() as conn:
        async with conn.transaction():
            batch = await batches.create(conn, body.plan_ids, caller.person_id)
        await batches.start(conn, batch["id"])
        return {"batch": await batches.get(conn, batch["id"])}


@router.get("/jira-batches")
async def list_batches(limit: int = Query(20, ge=1, le=100), caller: Caller = Depends(current_caller)) -> dict:
    _gate(caller)
    async with db.pool().acquire() as conn:
        return {"batches": await batches.recent(conn, limit)}


@router.get("/jira-batches/{batch_id}")
async def get_batch(batch_id: str, caller: Caller = Depends(current_caller)) -> dict:
    _gate(caller)
    async with db.pool().acquire() as conn:
        return await batches.get(conn, batch_id)


@router.get("/harvest/accounts")
async def harvest_accounts(caller: Caller = Depends(current_caller)) -> dict:
    _gate(caller)
    async with db.pool().acquire() as conn:
        return {"accounts": await harvest.accounts(conn)}


@router.post("/harvest/accounts/{account_id}/run", status_code=202)
async def harvest_run(account_id: str, caller: Caller = Depends(current_caller)) -> dict:
    _gate(caller)
    async with db.pool().acquire() as conn:
        if not await harvest.targets(conn, [account_id]):
            raise NotFound("Akun tidak ditemukan di Registry Eskala.")
    run_id = await prefect_api.run_deployment(prefect_api.HARVEST, {"account_ids": [account_id], "trigger": "manual"})
    return {"flow_run_id": run_id}


@router.get("/harvest/accounts/{account_id}/posts")
async def harvest_posts(account_id: str, month: str = Query(...), caller: Caller = Depends(current_caller)) -> dict:
    _gate(caller)
    m = store.month_of(month)
    async with db.pool().acquire() as conn:
        return {"account": await harvest.account(conn, account_id), "month": m.isoformat()[:7],
                "posts": await harvest.posts(conn, account_id, m)}


@router.put("/harvest/posts/{platform}/{content_id}/marks")
async def put_marks(platform: str, content_id: str, body: MarksIn, caller: Caller = Depends(current_caller)) -> dict:
    _gate(caller)
    async with db.pool().acquire() as conn:
        async with conn.transaction():
            return {"marks": await harvest.set_marks(conn, platform, content_id, body.marks, caller.person_id)}
