"""
The old AnythingLLM notes → Intakes (US6, research R7, FR-040, FR-041, G-13, G-17).

Every current `knowledge_records` row becomes one Intake of kind `old_note`:
  - matched to a Client by its `client_id` (set by spine-backfill), else by a UNIQUE
    token-subset match on its client name. No match, or more than one, → the
    Intake waits with no Client in "belum ada klien"; the Client is never guessed;
  - matched notes run the normal Intake pipeline (call site `hub.notes`), so their
    Proposals are reviewed and accepted like any other. English stays English,
    flagged `not_bahasa`, never translated;
  - idempotent by record id (unique index on `source_ref` for old notes): a re-run
    skips what's done, and a run stopped by the AI cap resumes where it left off.

A dry run makes ZERO model calls and reports the notes in scope, how they would
match, and the projected spend (constitution: costed batches).
"""
import asyncio
from typing import Any, Dict, List, Optional

import asyncpg

from ..card.definition import Definition
from ..errors import AiCapReached
from ..registry import names
from . import pipeline
from .sources import Source, _hash, normalize_text

BATCH = 10
# Spacing between model calls in a batch, and the wait before retrying a note once after
# a provider error. Measured 2026-09-25: 7 of 10 back-to-back pilot calls got an upstream
# "temporarily rate-limited" 429 from OpenRouter's provider, after the call's own retries.
PACE_SECONDS = 4
PROVIDER_RETRY_WAIT = 60
# Per-note estimate when no measured Intake cost exists yet (research R4: a text
# Intake ≈ $0.001–0.002; old notes are short, so the upper end is conservative).
FALLBACK_COST_PER_NOTE = 0.002


def note_text(subject: str, information: str) -> str:
    return normalize_text(f"{subject}\n\n{information}")


async def _pending(conn: asyncpg.Connection, limit: Optional[int]) -> List[asyncpg.Record]:
    """Notes with no Intake yet, plus notes whose Intake FAILED (a provider hiccup must
    not mark a note done forever). `failed_intake_id` is reused on the retry, since the
    one-Intake-per-note index forbids a second row."""
    return await conn.fetch(
        """SELECT k.id::text AS id, k.client_name, k.subject, k.information, k.client_id::text AS client_id,
                  i.id::text AS failed_intake_id
           FROM knowledge_records k
           LEFT JOIN intakes i ON i.kind = 'old_note' AND i.source_ref = k.id::text
           WHERE k.superseded_by IS NULL AND (i.id IS NULL OR i.status = 'failed')
           ORDER BY k.client_name, k.timestamp
           LIMIT $1""", limit or 100000)


async def _matcher(conn: asyncpg.Connection):
    clients = {r["id"]: r["display_name"] for r in await conn.fetch(
        "SELECT id::text AS id, display_name FROM clients")}
    aliases = {r["alias_key"]: r["cid"] for r in await conn.fetch(
        "SELECT alias_key, client_id::text AS cid FROM client_aliases")}

    def match(note: asyncpg.Record) -> Optional[str]:
        if note["client_id"] and note["client_id"] in clients:
            return note["client_id"]
        key = names.normalize_client_key(note["client_name"])
        if key in aliases:
            return aliases[key]
        hits = [cid for cid, n in clients.items() if names.is_token_subset_match(note["client_name"], n)]
        return hits[0] if len(hits) == 1 else None
    return match


async def projected_cost_per_note(conn: asyncpg.Connection) -> tuple[float, str]:
    row = await conn.fetchrow(
        """SELECT avg(l.cost_usd) AS avg, count(*) AS n FROM ai_ledger l JOIN intakes i ON i.id = l.intake_id
           WHERE l.call_site IN ('hub.intake', 'hub.notes') AND i.kind IN ('text', 'old_note', 'gdoc', 'pdf_text')""")
    if row and row["n"] and row["n"] >= 3:
        return float(row["avg"]), f"rata-rata {row['n']} Intake teks terukur"
    return FALLBACK_COST_PER_NOTE, "perkiraan research R4 (belum ada Intake teks terukur)"


async def progress(conn: asyncpg.Connection) -> Dict[str, int]:
    row = await conn.fetchrow(
        """SELECT count(*) AS processed,
                  count(*) FILTER (WHERE client_id IS NOT NULL) AS matched,
                  count(*) FILTER (WHERE status = 'unmatched') AS unmatched,
                  count(*) FILTER (WHERE status = 'discarded') AS discarded
           FROM intakes WHERE kind = 'old_note'""")
    remaining = await conn.fetchval(
        """SELECT count(*) FROM knowledge_records k WHERE k.superseded_by IS NULL
           AND NOT EXISTS (SELECT 1 FROM intakes i WHERE i.kind = 'old_note' AND i.source_ref = k.id::text)""")
    return {"processed": row["processed"], "matched": row["matched"], "unmatched": row["unmatched"],
            "discarded": row["discarded"], "remaining": remaining}


async def process(conn: asyncpg.Connection, d: Definition, *, limit: Optional[int] = None,
                  dry_run: bool = True) -> Dict[str, Any]:
    notes = await _pending(conn, limit)
    match = await _matcher(conn)
    matched = [(n, match(n)) for n in notes]
    to_run = [(n, cid) for n, cid in matched if cid]
    report: Dict[str, Any] = {"dry_run": dry_run, "in_scope": len(notes), "matched": len(to_run),
                              "unmatched": len(notes) - len(to_run)}
    if dry_run:
        per_note, basis = await projected_cost_per_note(conn)
        report.update({"projected_usd": round(per_note * len(to_run), 4), "projected_basis": basis,
                       "model_calls": 0,
                       "unmatched_names": sorted({n["client_name"] for n, cid in matched if not cid})})
        return report

    counts = {"ready": 0, "nothing_found": 0, "failed": 0, "duplicate": 0, "unmatched": 0}
    paused = False
    for start in range(0, len(matched), BATCH):
        for note, cid in matched[start:start + BATCH]:
            text = note_text(note["subject"], note["information"])
            if cid is None:
                await conn.execute(
                    """INSERT INTO intakes (client_id, kind, raw_text, source_ref, content_hash, status)
                       VALUES (NULL, 'old_note', $1, $2, $3, 'unmatched') ON CONFLICT DO NOTHING""",
                    text, note["id"], _hash("old_note", text.encode()))
                counts["unmatched"] += 1
                continue
            source = Source(kind="old_note", text=text, source_ref=note["id"],
                            content_hash=_hash("old_note", text.encode()))
            try:
                outcome = await pipeline.run_intake(conn, d, client_id=cid, source=source, submitted_by=None,
                                                    call_site="hub.notes", intake_id=note["failed_intake_id"])
                if await _failed_by_provider(conn, outcome.intake_id):
                    await asyncio.sleep(PROVIDER_RETRY_WAIT)
                    outcome = await pipeline.run_intake(conn, d, client_id=cid, source=source, submitted_by=None,
                                                        call_site="hub.notes", intake_id=outcome.intake_id)
            except AiCapReached:
                paused = True
                break
            await asyncio.sleep(PACE_SECONDS)
            if outcome.cached:
                # Same text already processed for this Client (a duplicate note): record this
                # record as done without a second call, so the run stays idempotent.
                await conn.execute(
                    """INSERT INTO intakes (client_id, kind, raw_text, source_ref, content_hash, status)
                       VALUES ($1::uuid, 'old_note', $2, $3, $4, 'discarded') ON CONFLICT DO NOTHING""",
                    cid, text, note["id"], _hash("old_note", (text + note["id"]).encode()))
                counts["duplicate"] += 1
                continue
            status = await conn.fetchval("SELECT status FROM intakes WHERE id = $1::uuid", outcome.intake_id)
            counts[status if status in counts else "failed"] += 1
        if paused:
            break
    spent = await conn.fetchval(
        """SELECT COALESCE(sum(cost_usd), 0) FROM ai_ledger WHERE call_site = 'hub.notes'
           AND at >= now() - interval '1 day'""")
    report.update(counts)
    report.update({"paused_by_cap": paused, "spent_usd_last_24h": round(float(spent), 4),
                   "progress": await progress(conn)})
    return report


async def _failed_by_provider(conn: asyncpg.Connection, intake_id: str) -> bool:
    return await conn.fetchval(
        "SELECT status = 'failed' AND failure_reason IN ('provider_error', 'timeout') FROM intakes WHERE id = $1::uuid",
        intake_id) or False


async def unmatched(conn: asyncpg.Connection) -> List[Dict[str, Any]]:
    rows = await conn.fetch(
        """SELECT i.id::text AS id, k.subject, k.information, k.client_name
           FROM intakes i JOIN knowledge_records k ON k.id::text = i.source_ref
           WHERE i.kind = 'old_note' AND i.status = 'unmatched' ORDER BY k.client_name, k.subject""")
    return [{"id": r["id"], "subject": r["subject"], "text": r["information"], "source_name": r["client_name"]}
            for r in rows]
