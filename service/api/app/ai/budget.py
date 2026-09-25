"""
The Hub's AI spend: a ledger of every call, and a monthly ceiling that is ENFORCED
(G-6, constitution XI), not merely reported.

  - `check_budget` runs BEFORE a call: at or over the cap → AiCapReached (402).
    Intake and Summary refresh pause; direct editing never touches this.
  - `record` writes the call's actual cost afterwards.
  - Crossing 80% sends ONE alert per month to #noktah-otomasi (G-6); the
    `hub_alerts_sent` primary key makes a second send impossible.

The cap covers Intake, old notes and Summaries together (call sites hub.intake,
hub.notes, hub.summary), separate from roach/songbird's extraction budget.
"""
import logging
from datetime import datetime, timezone
from typing import Optional

import asyncpg

from ..errors import AiCapReached
from ..notify import post_slack
from ..settings import get_settings

logger = logging.getLogger(__name__)
ALERT_RATIO = 0.8


def month_key(now: Optional[datetime] = None) -> str:
    return (now or datetime.now(timezone.utc)).strftime("%Y-%m")


async def spent_this_month(conn: asyncpg.Connection) -> float:
    value = await conn.fetchval(
        "SELECT COALESCE(sum(cost_usd), 0) FROM ai_ledger WHERE at >= date_trunc('month', now())"
    )
    return float(value or 0)


def over_cap(spent: float, cap: float) -> bool:
    return spent >= cap


async def check_budget(conn: asyncpg.Connection) -> float:
    """Raise AiCapReached when this month's spend is at or over the cap; else return it."""
    cap = get_settings().ai_monthly_cap_usd
    spent = await spent_this_month(conn)
    if over_cap(spent, cap):
        raise AiCapReached(
            f"Batas biaya AI bulan ini (USD {cap:.2f}) sudah tercapai. Intake dijeda; "
            "isian kartu tetap bisa diubah langsung.",
            spent_usd=round(spent, 4), cap_usd=cap,
        )
    return spent


async def record(
    conn: asyncpg.Connection, *, call_site: str, client_id: Optional[str], intake_id: Optional[str],
    model: Optional[str], provider: Optional[str], prompt_tokens: int, completion_tokens: int, cost_usd: float,
) -> None:
    await conn.execute(
        """INSERT INTO ai_ledger (call_site, client_id, intake_id, model, provider,
                                  prompt_tokens, completion_tokens, cost_usd)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8)""",
        call_site, client_id, intake_id, model, provider, prompt_tokens, completion_tokens, cost_usd,
    )
    await maybe_alert(conn)


async def maybe_alert(conn: asyncpg.Connection) -> None:
    cap = get_settings().ai_monthly_cap_usd
    spent = await spent_this_month(conn)
    if spent < cap * ALERT_RATIO:
        return
    inserted = await conn.fetchval(
        """INSERT INTO hub_alerts_sent (month, kind) VALUES ($1, 'ai_80pct')
           ON CONFLICT DO NOTHING RETURNING month""",
        month_key(),
    )
    if inserted:
        await post_slack(
            get_settings().slack_automation_noktah,
            f"⚠️ *Noktah Hub*: biaya AI bulan ini sudah USD {spent:.2f} dari batas USD {cap:.2f} (≥80%). "
            "Saat batas tercapai, Intake dan Ringkasan dijeda.",
        )


async def usage(conn: asyncpg.Connection) -> dict:
    cap = get_settings().ai_monthly_cap_usd
    spent = await spent_this_month(conn)
    return {"month": month_key(), "spent_usd": round(spent, 4), "cap_usd": cap, "paused": over_cap(spent, cap)}
