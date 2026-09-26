"""
Sanctions, issued automatically after a one-day hold (spec 009 US9; ADR-0001; G-29, G-37,
G-38, G-42, G-44).

The hub-sanctions flow calls `tick` every hour; it is idempotent and decides by the calendar:
  * from working day 2, 08:00 WIB: last month's sanctions are worked out once from the ladder,
    and Eskala's Brand Manager gets one Slack message;
  * direct sanctions (§4.3 with its item, H1) are worked out as soon as their Event is judged,
    held until the end of the next working day;
  * a sanction resting on an Event under appeal waits (`on_appeal`) until it is judged again;
  * once its hold window has passed and nobody held it, a sanction is issued; SP1, SP2, SP3
    and Peringatan Pertama dan Terakhir get a numbered letter.
"Proses PHK" is only ever flagged for a person to start, never issued (a CHECK enforces it).
"""
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional

import asyncpg

from .. import notify
from ..errors import Conflict, Invalid, NotFound
from ..settings import env
from ..workdays import WIB, end_of, next_working_day, now_wib, previous_month, working_day
from . import events as event_store
from . import ladder, letters, points

MONTH_STEP_HOUR = 8  # working day 2, 08:00 WIB


async def _history(conn: asyncpg.Connection, person_id: str) -> List[ladder.Issued]:
    rows = await conn.fetch(
        """SELECT level, issued_on, category_code FROM sanctions
           WHERE person_id = $1::uuid AND state = 'issued' AND issued_on IS NOT NULL""", person_id)
    return [ladder.Issued(r["level"], r["issued_on"], r["category_code"]) for r in rows]


def month_points(evs: List[event_store.Event], month: date) -> Dict[str, Dict[str, Any]]:
    """Violation points per person for one month, counting only Events that count."""
    out: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"points": 0, "events": [], "codes": defaultdict(int)})
    for e in evs:
        o = e.outcome
        if e.person_id is None or e.month != month or o.kind != "violation" or not o.counts:
            continue
        if e.judged_at is None or e.judged_at.date() < points.COUNTING_FROM:
            continue
        d = out[e.person_id]
        d["points"] += o.points
        d["events"].append(e)
        if o.category and o.points:
            d["codes"][o.category] += -o.points
    return out


def _main_code(codes: Dict[str, int]) -> Optional[str]:
    return max(codes.items(), key=lambda kv: kv[1])[0] if codes else None


async def _brand_slack(conn: asyncpg.Connection) -> Optional[str]:
    """Eskala's managers' channel (noktah_brands.slack_managerial_env names the webhook's env var)."""
    name = await conn.fetchval("SELECT slack_managerial_env FROM noktah_brands WHERE brand_key = 'eskala'")
    return env(name) if name else None


async def compute_month(conn: asyncpg.Connection, month: date, hold_until: datetime, evs=None) -> int:
    """Work out one month's ladder sanctions (idempotent: one per person per month)."""
    evs = evs if evs is not None else await event_store.load(conn)
    this = month_points(evs, month)
    before = month_points(evs, previous_month(month))
    evaluated_on = (hold_until - timedelta(seconds=1)).astimezone(WIB).date()
    made = 0
    for person_id, d in this.items():
        magnitude = -d["points"]
        code = _main_code(d["codes"])
        verdict = ladder.decide(magnitude, -before.get(person_id, {"points": 0})["points"], code,
                                await _history(conn, person_id), evaluated_on)
        if verdict is None:
            continue
        keys = [e.key for e in d["events"]]
        state = "flagged" if verdict.level == "phk_flag" else (
            "on_appeal" if any(e.status == "Appealed" for e in d["events"]) else "computed")
        done = await conn.fetchval(
            """INSERT INTO sanctions (person_id, level, with_pip, source, period, category_code, event_keys, points,
                                      state, hold_until, note)
               VALUES ($1::uuid, $2, $3, 'ladder', $4, $5, $6::text[], $7, $8, $9, $10)
               ON CONFLICT (person_id, period) WHERE source = 'ladder' DO NOTHING RETURNING 1""",
            person_id, verdict.level, verdict.with_pip, month, code, keys, d["points"], state, hold_until, verdict.rule)
        made += done or 0
    return made


async def compute_direct(conn: asyncpg.Connection, now: datetime, evs=None) -> int:
    evs = evs if evs is not None else await event_store.load(conn)
    made = 0
    for e in evs:
        o = e.outcome
        if e.person_id is None or o.kind != "violation" or o.state in ("not_judged", "reference", "on_hold"):
            continue
        if o.category not in ("H1", "H2") or e.judged_at is None:
            continue
        verdict = ladder.direct(o.direct_item, o.category)
        if verdict is None:
            continue  # H2 without its item: shown as "Sanksi langsung, tentukan manual", never automatic
        hold = end_of(next_working_day(now.astimezone(WIB).date()))
        state = "flagged" if verdict.level == "phk_flag" else "computed"
        done = await conn.fetchval(
            """INSERT INTO sanctions (person_id, level, with_pip, source, category_code, event_keys, points, state,
                                      hold_until, note)
               VALUES ($1::uuid, $2, false, 'direct', $3, ARRAY[$4]::text[], $5, $6, $7, $8)
               ON CONFLICT (person_id, (event_keys[1])) WHERE source = 'direct' DO NOTHING RETURNING 1""",
            e.person_id, verdict.level, o.category, e.key, o.points, state, hold, verdict.rule)
        if done:
            made += 1
            await notify.post_slack(await _brand_slack(conn),
                                    f"🔴 Sanksi langsung untuk {e.person_name or 'seseorang'} ({ladder.LABELS[verdict.level]}, "
                                    f"Event {e.key}) siap ditinjau di halaman Insentif Noktah Hub. Sanksi terbit otomatis setelah {hold.astimezone(WIB):%d-%m-%Y %H:%M} WIB kecuali ditahan.")
    return made


async def _appeals(conn: asyncpg.Connection, evs: List[event_store.Event], now: datetime) -> None:
    appealed = {e.key for e in evs if e.status == "Appealed"}
    for s in await conn.fetch(
            "SELECT id, event_keys, state FROM sanctions WHERE state IN ('computed', 'on_appeal', 'issued')"):
        on_appeal = bool(appealed.intersection(s["event_keys"] or []))
        if on_appeal and s["state"] == "computed":
            await conn.execute("UPDATE sanctions SET state = 'on_appeal' WHERE id = $1", s["id"])
        elif on_appeal and s["state"] == "issued":
            # v2.1 §7.2: a sanction is suspended during an appeal
            await conn.execute("UPDATE sanctions SET state = 'on_appeal' WHERE id = $1", s["id"])
        elif not on_appeal and s["state"] == "on_appeal":
            hold = end_of(next_working_day(now.astimezone(WIB).date()))
            await conn.execute("UPDATE sanctions SET state = 'computed', hold_until = $2 WHERE id = $1", s["id"], hold)


async def _issue_due(conn: asyncpg.Connection, now: datetime) -> int:
    rows = await conn.fetch(
        """SELECT id, level FROM sanctions WHERE state = 'computed' AND hold_until <= $1 AND level <> 'phk_flag'
           FOR UPDATE""", now)
    today = now.astimezone(WIB).date()
    for r in rows:
        number = await letters.next_number(conn, today) if r["level"] in ladder.LETTER_LEVELS else None
        await conn.execute(
            """UPDATE sanctions SET state = 'issued', issued_on = $2::date, valid_until = $2::date + 90, letter_number = $3,
                   letter_state = CASE WHEN $3::text IS NULL THEN 'not_needed' ELSE 'pending' END
               WHERE id = $1""", r["id"], today, number)
    return len(rows)


async def _signers(conn: asyncpg.Connection) -> Dict[str, Any]:
    ceo = await conn.fetchval(
        """SELECT p.display_name FROM person_roles r JOIN people p ON p.id = r.person_id
           WHERE r.role = 'owner' AND r.valid_to IS NULL AND p.status = 'active' ORDER BY r.valid_from LIMIT 1""")
    bm = await conn.fetchrow(
        """SELECT p.id::text AS id, p.display_name FROM person_roles r JOIN people p ON p.id = r.person_id
           JOIN noktah_brands b ON b.id = r.noktah_brand_id
           WHERE r.role = 'brand_manager' AND b.brand_key = 'eskala' AND r.valid_to IS NULL LIMIT 1""")
    return {"ceo": ceo or "CEO", "bm_name": bm["display_name"] if bm else "Brand Manager", "bm_id": bm["id"] if bm else None}


async def letters_to_write(conn: asyncpg.Connection) -> List[Dict[str, Any]]:
    rows = await conn.fetch(
        """SELECT s.id::text AS id, s.person_id::text AS person_id, s.level, s.with_pip, s.source, s.period,
                  s.event_keys, s.points, s.issued_on, s.valid_until, s.letter_number, s.note, p.display_name
           FROM sanctions s JOIN people p ON p.id = s.person_id
           WHERE s.state = 'issued' AND s.letter_state IN ('pending', 'failed') AND s.letter_number IS NOT NULL""")
    if not rows:
        return []
    signers = await _signers(conn)
    all_events = {e.key: e for e in await event_store.load(conn)}
    out = []
    for r in rows:
        role = await conn.fetchval(
            """SELECT role FROM person_roles WHERE person_id = $1::uuid AND valid_to IS NULL
               ORDER BY valid_from LIMIT 1""", r["person_id"])
        evs = [all_events[k] for k in (r["event_keys"] or []) if k in all_events]
        html = letters.render(
            level=r["level"], number=r["letter_number"], name=r["display_name"],
            role=letters.ROLE_LABELS.get(role or "", role or "-"), issued_on=r["issued_on"],
            valid_until=r["valid_until"], source=r["source"],
            events=[{"key": e.key, "date": letters.tanggal(e.created_at.astimezone(WIB).date()),
                     "category": e.outcome.category, "summary": e.summary, "points": e.outcome.points} for e in evs],
            points=abs(r["points"] or 0), period=r["period"], rule=r["note"] or "",
            direct_item=evs[0].outcome.direct_item if evs else None, with_pip=r["with_pip"],
            ceo=signers["ceo"], brand_manager=signers["bm_name"],
            recipient_is_brand_manager=r["person_id"] == signers["bm_id"])
        out.append({"sanction_id": r["id"], "file_name": letters.file_name(r["letter_number"], r["display_name"],
                                                                          r["level"]), "html": html})
    return out


async def tick(conn: asyncpg.Connection, now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    local = now_wib(now)
    evs = await event_store.load(conn)
    direct = await compute_direct(conn, now, evs)
    computed = 0
    wd2 = working_day(local.year, local.month, 2)
    wd2_start = datetime.combine(wd2, time(MONTH_STEP_HOUR, 0), tzinfo=WIB)
    period = previous_month(local.date())
    if now >= wd2_start:
        kind = "sanctions_ready"
        month_key = period.isoformat()[:7]
        already = await conn.fetchval("SELECT 1 FROM hub_alerts_sent WHERE month = $1 AND kind = $2", month_key, kind)
        if not already:
            hold = end_of(working_day(local.year, local.month, 3))
            computed = await compute_month(conn, period, hold, evs)
            await conn.execute("INSERT INTO hub_alerts_sent (month, kind) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                               month_key, kind)
            await notify.post_slack(await _brand_slack(conn),
                                    f"🟠 Sanksi bulan {month_key} siap ditinjau di halaman Insentif Noktah Hub ({computed} orang). "
                                    f"Sanksi terbit otomatis setelah hari kerja ke-3 ({working_day(local.year, local.month, 3):%d-%m-%Y}) kecuali ditahan.")
    await _appeals(conn, evs, now)
    issued = await _issue_due(conn, now)
    return {"direct": direct, "computed": computed, "issued": issued, "letters": await letters_to_write(conn)}


async def record_letters(conn: asyncpg.Connection, results: List[Dict[str, Any]]) -> None:
    for r in results:
        if r.get("ok"):
            await conn.execute("UPDATE sanctions SET letter_state = 'written', letter_file_id = $2 WHERE id = $1::uuid",
                               r["sanction_id"], r.get("file_id"))
        else:
            await conn.execute("UPDATE sanctions SET letter_state = 'failed' WHERE id = $1::uuid", r["sanction_id"])


# ── Manager actions ────────────────────────────────────────────────────────────

async def hold(conn: asyncpg.Connection, sanction_id: str, reason: str, person_id: str,
               now: Optional[datetime] = None) -> None:
    now = now or datetime.now(timezone.utc)
    if not (reason or "").strip():
        raise Invalid("Alasan menahan wajib diisi.")
    row = await conn.fetchrow("SELECT state, hold_until FROM sanctions WHERE id = $1::uuid FOR UPDATE", sanction_id)
    if row is None:
        raise NotFound("Sanksi tidak ditemukan.")
    if row["state"] != "computed" or (row["hold_until"] and row["hold_until"] <= now):
        raise Conflict("Sanksi ini sudah tidak bisa ditahan: batas tahannya lewat atau statusnya sudah berubah.")
    await conn.execute(
        "UPDATE sanctions SET state = 'held', hold_reason = $2, held_by = $3::uuid WHERE id = $1::uuid",
        sanction_id, reason.strip(), person_id)


async def release(conn: asyncpg.Connection, sanction_id: str, now: Optional[datetime] = None) -> None:
    now = now or datetime.now(timezone.utc)
    row = await conn.fetchrow("SELECT state, hold_until FROM sanctions WHERE id = $1::uuid FOR UPDATE", sanction_id)
    if row is None:
        raise NotFound("Sanksi tidak ditemukan.")
    if row["state"] != "held" or (row["hold_until"] and row["hold_until"] <= now):
        raise Conflict("Penahanan sanksi ini sudah tidak bisa dibatalkan: batas tahannya lewat atau statusnya sudah berubah.")
    await conn.execute("UPDATE sanctions SET state = 'computed', hold_reason = NULL, held_by = NULL WHERE id = $1::uuid",
                       sanction_id)


async def record(conn: asyncpg.Connection, *, person_id: str, level: str, issued_on: date,
                 category_code: Optional[str], note: Optional[str], by: str) -> str:
    """A teguran lisan or SP issued outside the Hub (FR-079). It counts from its issue date."""
    if level not in ("teguran_lisan", "sp1", "sp2", "sp3", "peringatan_terakhir"):
        raise Invalid("Tingkat sanksi tidak dikenal.")
    exists = await conn.fetchval("SELECT 1 FROM people WHERE id = $1::uuid", person_id)
    if not exists:
        raise NotFound("Orang tidak ditemukan.")
    return await conn.fetchval(
        """INSERT INTO sanctions (person_id, level, source, category_code, state, issued_on, valid_until, note,
                                  created_by)
           VALUES ($1::uuid, $2, 'recorded', $3, 'issued', $4::date, $4::date + 90, $5, $6::uuid) RETURNING id::text""",
        person_id, level, (category_code or "").strip().upper() or None, issued_on, note, by)


def as_dict(r: asyncpg.Record) -> Dict[str, Any]:
    return {"id": r["id"], "level": r["level"], "level_label": ladder.LABELS[r["level"]], "with_pip": r["with_pip"],
            "source": r["source"], "state": r["state"], "period": r["period"].isoformat()[:7] if r["period"] else None,
            "category_code": r["category_code"], "event_keys": list(r["event_keys"] or []), "points": r["points"],
            "hold_until": r["hold_until"].isoformat() if r["hold_until"] else None, "hold_reason": r["hold_reason"],
            "issued_on": r["issued_on"].isoformat() if r["issued_on"] else None,
            "valid_until": r["valid_until"].isoformat() if r["valid_until"] else None,
            "letter_number": r["letter_number"], "letter_state": r["letter_state"],
            "letter_url": f"https://docs.google.com/document/d/{r['letter_file_id']}/edit" if r["letter_file_id"] else None,
            "note": r["note"]}


SANCTION_COLUMNS = """s.id::text AS id, s.person_id::text AS person_id, s.level, s.with_pip, s.source, s.state,
    s.period, s.category_code, s.event_keys, s.points, s.hold_until, s.hold_reason, s.issued_on, s.valid_until,
    s.letter_number, s.letter_state, s.letter_file_id, s.note"""
