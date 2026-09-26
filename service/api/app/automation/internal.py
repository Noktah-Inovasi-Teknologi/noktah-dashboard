"""/internal/* for the Otomasi flows (hub-plan-watch, hub-jira-create, harvest-registry)."""
from datetime import date
from typing import Any, Dict, List, Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from .. import db
from . import batches, harvest, plans, store, watcher

router = APIRouter()


class TargetsIn(BaseModel):
    month: Optional[str] = None
    plan_ids: Optional[List[str]] = None


class ScanIn(BaseModel):
    client_id: str
    month: str
    state: str
    drive_file_id: Optional[str] = None
    file_name: Optional[str] = None
    tab_name: Optional[str] = None
    rows: List[Dict[str, Any]] = Field(default_factory=list)
    problem: Optional[str] = None


class Results(BaseModel):
    results: List[Dict[str, Any]] = Field(default_factory=list)


class ClaimIn(BaseModel):
    batch_id: str
    peek: bool = False  # validate-only: read what would be created, don't mark the batch running


class ResultIn(BaseModel):
    batch_id: str
    rows: List[Dict[str, Any]] = Field(default_factory=list)
    done: bool = False
    error: Optional[str] = None


class HarvestIn(BaseModel):
    account_ids: Optional[List[str]] = None


def _this_and_next(today: date) -> List[date]:
    first = today.replace(day=1)
    nxt = date(first.year + (first.month == 12), first.month % 12 + 1, 1)
    return [first, nxt]


@router.post("/automation/plan-targets")
async def plan_targets(body: TargetsIn) -> dict:
    """Which plans to scan: every active Eskala Client with a Content Plan folder, for the
    given month (default this month and next), or exactly the plans asked for."""
    async with db.pool().acquire() as conn:
        pairs = []
        if body.plan_ids:
            for pid in body.plan_ids:
                p = await store.load_plan(conn, pid)
                pairs.append((p["client_id"], p["month"]))
            clients = {c["id"]: c for c in await store.eskala_clients(conn, [c for c, _ in pairs])}
            wanted = [(clients[c], m) for c, m in pairs if c in clients]
        else:
            months = [store.month_of(body.month)] if body.month else _this_and_next(date.today())
            wanted = [(c, m) for c in await store.eskala_clients(conn) for m in months]
    return {"targets": [{"client_id": c["id"], "client_name": c["display_name"], "month": m.isoformat()[:7],
                         "folder_id": c["content_plan_folder_id"], "plan_label": plans.month_label(m)}
                        for c, m in wanted]}


@router.post("/content-plans/scan")
async def scan(body: ScanIn) -> dict:
    """One plan as just read. Runs the watcher (FR-020) and returns the comments to post."""
    month = store.month_of(body.month)
    async with db.pool().acquire() as conn:
        async with conn.transaction():
            await store.eskala_client(conn, body.client_id)
            saved = await store.upsert_scan(conn, body.client_id, month, body.model_dump())
            comments = await watcher.pending_comments(conn, saved["plan_id"], body.file_name or "")
    return {"plan_id": saved["plan_id"], "comments": comments}


@router.post("/content-plans/comments")
async def plan_comments(body: Results) -> dict:
    async with db.pool().acquire() as conn:
        await watcher.record_comments(conn, body.results)
    return {}


@router.post("/jira-batches/claim")
async def claim(body: ClaimIn) -> dict:
    async with db.pool().acquire() as conn:
        async with conn.transaction():
            return await batches.claim(conn, body.batch_id, peek=body.peek)


@router.post("/jira-batches/result")
async def result(body: ResultIn) -> dict:
    async with db.pool().acquire() as conn:
        async with conn.transaction():
            await batches.result(conn, body.batch_id, body.rows, body.done, body.error)
    return {}


@router.post("/harvest/targets")
async def harvest_targets(body: HarvestIn) -> dict:
    async with db.pool().acquire() as conn:
        return {"targets": await harvest.targets(conn, body.account_ids)}
