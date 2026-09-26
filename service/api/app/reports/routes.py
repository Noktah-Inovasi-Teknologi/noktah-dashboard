"""/v1 Laporan (spec 009 US5–US7): Delivery, Stations, Performa. Behind "Lihat laporan" in
Eskala (G-3, G-18, G-39). Every response says when the Jira copy was last refreshed (G-40)."""
from typing import Optional

from fastapi import APIRouter, Depends, Query

from .. import db
from ..auth import Caller, current_caller
from ..automation.store import month_of
from ..deps import require
from ..permissions import Action
from . import delivery, jira_store, performance, station_report

router = APIRouter(prefix="/v1")


def _gate(caller: Caller) -> None:
    require(caller, Action.VIEW_REPORTS, "eskala")


@router.get("/reports/delivery")
async def report_delivery(month: str = Query(...), caller: Caller = Depends(current_caller)) -> dict:
    _gate(caller)
    async with db.pool().acquire() as conn:
        return {**await delivery.report(conn, month_of(month)), "refreshed_at": await jira_store.refreshed_at(conn)}


@router.get("/reports/stations")
async def report_stations(month: str = Query(...), caller: Caller = Depends(current_caller)) -> dict:
    _gate(caller)
    async with db.pool().acquire() as conn:
        return {**await station_report.report(conn, month_of(month)),
                "refreshed_at": await jira_store.refreshed_at(conn)}


@router.get("/reports/performance")
async def report_performance(month: str = Query(...), client_id: Optional[str] = None,
                             caller: Caller = Depends(current_caller)) -> dict:
    _gate(caller)
    async with db.pool().acquire() as conn:
        m = month_of(month)
        body = await performance.client_report(conn, client_id, m) if client_id else await performance.overview(conn, m)
        return {**body, "refreshed_at": await jira_store.refreshed_at(conn)}
