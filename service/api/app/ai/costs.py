"""
What AI costs, per case and month (the Hub's Biaya AI page). Read-only.

Each case's spend is read where its calls are recorded (shared/noktah_ai/README.md):
  summary, intake, intake_image  ai_ledger (Intake split by the Intake's kind;
                                 the one-time old-notes run, hub.notes, counts as intake)
  image, video                   content_extractions (media_path) and extraction_quarantine
                                 (a quarantined attempt is billed too; typed by the post)
  generation                     ai_usage (songbird, from migration 013 on)
Months are calendar months in UTC, as the Hub's monthly cap counts them.

Only the Owner and Brand Managers may see it: spend is company-wide, across every
Noktah Brand and service.
"""
from datetime import datetime, timezone
from typing import Any, Dict, List

import asyncpg
from noktah_ai import rotation

from ..auth import Caller
from ..permissions import is_owner
from ..settings import get_settings
from . import budget

MONTHS = 12

_SPEND = """
WITH usage AS (
    SELECT l.at,
           CASE WHEN l.call_site = 'hub.summary' THEN 'summary'
                WHEN i.kind IN ('image', 'pdf_scanned') THEN 'intake_image'
                ELSE 'intake' END AS ai_case,
           l.cost_usd
    FROM ai_ledger l LEFT JOIN intakes i ON i.id = l.intake_id
    UNION ALL
    SELECT e.extracted_at, e.media_path, e.cost_usd FROM content_extractions e WHERE e.cost_usd IS NOT NULL
    UNION ALL
    SELECT q.quarantined_at, CASE WHEN s.content_type = 'video' THEN 'video' ELSE 'image' END, q.cost_usd
    FROM extraction_quarantine q
    LEFT JOIN harvested_signals s ON s.platform = q.platform AND s.content_id = q.content_id
    WHERE q.cost_usd IS NOT NULL
    UNION ALL
    SELECT u.at, u.ai_case, u.cost_usd FROM ai_usage u
)
SELECT ai_case, to_char(at AT TIME ZONE 'UTC', 'YYYY-MM') AS month,
       count(*) AS calls, COALESCE(sum(cost_usd), 0) AS cost_usd
FROM usage
WHERE at >= date_trunc('month', now(), 'UTC') - make_interval(months => $1)
GROUP BY 1, 2
"""


def may_view(caller: Caller) -> bool:
    return is_owner(caller.access) or any(a.role == "brand_manager" for a in caller.assignments)


def month_keys(now: datetime, count: int = MONTHS) -> List[str]:
    """The last `count` calendar months, oldest first, ending with `now`'s month."""
    y, m = now.year, now.month
    keys = []
    for _ in range(count):
        keys.append(f"{y:04d}-{m:02d}")
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    return keys[::-1]


async def monthly(conn: asyncpg.Connection, now: datetime | None = None) -> Dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    months = month_keys(now)
    rows = await conn.fetch(_SPEND, MONTHS - 1)
    spend: Dict[str, Dict[str, Dict[str, float]]] = {}
    for r in rows:
        spend.setdefault(r["ai_case"], {})[r["month"]] = {"calls": int(r["calls"]),
                                                         "cost_usd": round(float(r["cost_usd"]), 6)}
    this_month, last_month = months[-1], months[-2]
    cases = []
    for key, info in rotation.cases().items():
        by_month = {m: spend.get(key, {}).get(m, {"calls": 0, "cost_usd": 0.0}) for m in months}
        cases.append({
            "key": key, **info,
            # hub-api's own cases show the model in use now; roach's and Prefect's live in their processes.
            "current": rotation.for_case(key).current() if info["used_by"] == "Hub" else None,
            "months": by_month,
            "this_month": by_month[this_month]["cost_usd"],
            "last_month": by_month[last_month]["cost_usd"],
            "total": round(sum(v["cost_usd"] for v in by_month.values()), 6),
        })
    totals = {m: round(sum(c["months"][m]["cost_usd"] for c in cases), 6) for m in months}
    return {"months": months, "cases": cases, "totals": totals,
            "hub_cap": {"cap_usd": get_settings().ai_monthly_cap_usd,
                        "spent_usd": round(await budget.spent_this_month(conn), 6)}}
