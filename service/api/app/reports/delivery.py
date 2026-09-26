"""
Laporan → Delivery (spec 009 US5; FR-051, G-4): per Eskala Client per month, planned vs
issues created vs Published, Late, published late and cancelled.

  Published       the issue reached "Published, Need Review" or "Done"
  Late            its publication date has passed and it isn't Published; Shelved is never Late
  published late  Published, but on a day after its publication date (WIB), from Jira's history
  cancelled       Shelved
  on hold         On Hold: paused by a manager, never Late (G-52)
"""
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

import asyncpg

from ..workdays import WIB
from . import content, stations
from ..automation import plans as plan_rules


def _empty(client_id: Optional[str], name: str) -> Dict[str, Any]:
    return {"client": {"id": client_id, "name": name}, "planned": None, "created": 0, "published": 0,
            "late": 0, "published_late": 0, "cancelled": 0, "on_hold": 0, "late_items": []}


async def report(conn: asyncpg.Connection, month: date, now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(WIB).date()
    clients = await conn.fetch(
        """SELECT c.id::text AS id, c.display_name FROM clients c JOIN noktah_brands b ON b.id = c.noktah_brand_id
           WHERE b.brand_key = 'eskala' AND c.status = 'active' ORDER BY c.display_name""")
    out: Dict[Optional[str], Dict[str, Any]] = {c["id"]: _empty(c["id"], c["display_name"]) for c in clients}
    for p in await conn.fetch("SELECT client_id::text AS client_id, rows FROM content_plans WHERE month = $1 AND state = 'found'",
                              month):
        if p["client_id"] in out:
            out[p["client_id"]]["planned"] = len(plan_rules.content_rows(p["rows"] or []))

    for c in await content.load(conn, month):
        entry = out.get(c.client_id)
        if entry is None:
            entry = out.setdefault(c.client_id, _empty(c.client_id, c.client_name or "Tanpa klien (komponen tidak dikenal)"))
        entry["created"] += 1
        if stations.is_cancelled(c.status):
            entry["cancelled"] += 1
            continue
        if stations.is_on_hold(c.status):
            entry["on_hold"] += 1
            continue
        if stations.is_published(c.status):
            entry["published"] += 1
            at = c.published_at()
            if at is not None and c.publication is not None and at.astimezone(WIB).date() > c.publication:
                entry["published_late"] += 1
            continue
        if c.publication is not None and c.publication < today:
            entry["late"] += 1
            station = stations.station_of(c.status)
            entry["late_items"].append({
                "key": c.key, "topik": c.summary, "publication_date": c.publication.isoformat(), "status": c.status,
                "station": station, "station_label": stations.STATIONS[station]["label"] if station else None,
                "days_late": (today - c.publication).days})
    rows = sorted(out.values(), key=lambda r: (r["client"]["id"] is None, r["client"]["name"]))
    for r in rows:
        r["late_items"].sort(key=lambda i: -i["days_late"])
    totals = {k: sum(r[k] or 0 for r in rows) for k in ("planned", "created", "published", "late", "published_late",
                                                        "cancelled", "on_hold")}
    return {"month": month.isoformat()[:7], "clients": rows, "totals": totals}
