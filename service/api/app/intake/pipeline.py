"""
The Intake pipeline (research R3) and Proposal decisions (R3 acceptance rules).

run_intake:
  1. cache: the same content for the same Client returns the earlier Intake, with
     no second model call (constitution XI). A row stuck in `processing` for more
     than STUCK_AFTER (the API died mid-call) is failed as `timeout` first;
  2. cap: at the monthly AI cap → AiCapReached (402), nothing stored;
  3. the Intake row (with its raw material, kept 12 months) is committed BEFORE the
     model call, so a crash leaves a visible `processing` row, not nothing — and no
     transaction is held open across a 90-second call;
  4. one model call (prompt.intake_v1), validated, retried once (ai/openrouter.py);
  5. per item: patient data dropped, unknown fields / wrong shapes dropped, values
     completed (object fields merged into the current value), no-op items dropped,
     then the deterministic flags (checks.py);
  6. Proposals stored, the Intake marked ready / nothing_found, spend recorded.
  Failures are classified into `intakes.failure_reason` and never shown as Proposals.

decide: accept / edit / reject one Proposal. Nothing reaches a card before this
(FR-035). A Guideline Proposal from a PM/AE becomes a PENDING value (FR-004/FR-008);
a Request Proposal creates a Request (G-26).
"""
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import asyncpg

from ..ai import budget
from ..ai.openrouter import AiFailure, chat_json
from ..card import values
from ..card.definition import Definition, is_empty, validate_value
from ..errors import Conflict, Invalid, NotFound
from ..requests.routes import RequestIn, create_request
from ..settings import get_settings
from . import checks, prompt
from .sources import Source

logger = logging.getLogger(__name__)
STUCK_AFTER = timedelta(minutes=10)
CHANNEL_BY_KIND = {"image": "whatsapp_group", "gdoc": "lainnya", "pdf_text": "lainnya", "pdf_scanned": "lainnya",
                   "old_note": "lainnya"}


@dataclass
class IntakeOutcome:
    intake_id: str
    cached: bool = False
    dropped: Dict[str, int] = field(default_factory=lambda: {"patient_data": 0, "invalid": 0, "unchanged": 0})


# ── helpers ───────────────────────────────────────────────────────────────────

def _date(value: Any) -> Optional[date]:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


def _text(value: Any) -> Optional[str]:
    return value.strip() if isinstance(value, str) and value.strip() else None


def complete_value(shape: str, current: Any, proposed: Any) -> Any:
    """Object fields: the model sends only the changed sub-fields; merge them in."""
    if shape == "object" and isinstance(proposed, dict) and isinstance(current, dict):
        return {**current, **proposed}
    return proposed


def subfield_address(d: Definition, target: Any, key: Any, item: Dict[str, Any]) -> tuple:
    """Accept `field.subfield` (e.g. `tentang_usaha.visi_misi`) as the model sometimes writes
    it: the Proposal targets the field, with only that sub-field in its value. Measured
    2026-09-25: a correct vision/mission extraction was dropped as an unknown field."""
    if not isinstance(key, str) or "." not in key or target not in ("profil", "guideline"):
        return key, item
    field, _, sub = key.partition(".")
    if not d.has_field(target, field):
        return key, item
    spec = d.field(target, field)
    if spec["shape"] != "object" or sub not in {s["key"] for s in spec.get("subfields", [])}:
        return key, item
    return field, {**item, "field_key": field, "value": {sub: item.get("value")}}


def has_whatsapp_lines(text: Optional[str]) -> bool:
    return any(checks.strip_wa_prefix(line) != line for line in (text or "").split("\n"))


async def _context(conn: asyncpg.Connection, client_id: str, source: Source) -> checks.Context:
    current = await values.current_values(conn, client_id)
    name = await conn.fetchval("SELECT display_name FROM clients WHERE id = $1::uuid", client_id)
    names = [r["display_name"] for r in await conn.fetch("SELECT display_name FROM clients")]
    pic = current["profil"].get("pic") or {}
    naming = current["profil"].get("nama_penulisan") or {}
    return checks.Context(
        client_name=name, source_text=None if source.is_image else source.text, is_image=source.is_image,
        pic_name=pic.get("nama") if isinstance(pic, dict) else None,
        other_client_names=[n for n in names if n != name], generic=checks.generic_tokens(names),
        never_write=[x for x in (naming.get("jangan_ditulis") or []) if isinstance(x, str)]
        if isinstance(naming, dict) else [],
        current=current)


async def find_cached(conn: asyncpg.Connection, client_id: str, content_hash: str) -> Optional[str]:
    row = await conn.fetchrow(
        """SELECT id::text AS id, status, submitted_at FROM intakes
           WHERE client_id = $1::uuid AND content_hash = $2 AND status <> 'failed'""", client_id, content_hash)
    if row is None:
        return None
    if row["status"] == "processing" and datetime.now(timezone.utc) - row["submitted_at"] > STUCK_AFTER:
        await conn.execute(
            "UPDATE intakes SET status = 'failed', failure_reason = 'timeout' WHERE id = $1::uuid", row["id"])
        return None
    return row["id"]


# ── run ───────────────────────────────────────────────────────────────────────

async def run_intake(conn: asyncpg.Connection, d: Definition, *, client_id: str, source: Source,
                     submitted_by: Optional[str], call_site: str = "hub.intake") -> IntakeOutcome:
    """Run one Intake for one Client. Raises AiCapReached at the cap; every other
    failure is recorded on the Intake row and returned."""
    cached = await find_cached(conn, client_id, source.content_hash)
    if cached:
        return IntakeOutcome(cached, cached=True)
    await budget.check_budget(conn)

    try:
        intake_id = await conn.fetchval(
            """INSERT INTO intakes (client_id, kind, raw_text, raw_blob, raw_mime, source_ref, content_hash,
                                    submitted_by, prompt_version)
               VALUES ($1::uuid, $2, $3, $4, $5, $6, $7, $8::uuid, $9) RETURNING id::text""",
            client_id, source.kind, source.text, source.raw_blob, source.raw_mime, source.source_ref,
            source.content_hash, submitted_by, prompt.PROMPT_VERSION)
    except asyncpg.UniqueViolationError:
        # An identical submission won the race; it is the cache hit.
        return IntakeOutcome(await find_cached(conn, client_id, source.content_hash) or "", cached=True)

    outcome = IntakeOutcome(intake_id)
    ctx = await _context(conn, client_id, source)
    settings = get_settings()
    try:
        result = await chat_json(
            api_key=settings.openrouter_api_key, model=settings.intake_model,
            messages=prompt.build_messages(d, client_name=ctx.client_name, current=ctx.current, pic_name=ctx.pic_name,
                                           source=source),
            schema=prompt.SCHEMA,
            validate=prompt.validate_shape, call_site=call_site, max_tokens=prompt.MAX_TOKENS,
            timeout=settings.ai_timeout_seconds)
    except AiFailure as e:
        logger.warning("intake %s failed: %s", intake_id, e)
        async with conn.transaction():
            if e.cost_usd or e.prompt_tokens:
                await budget.record(conn, call_site=call_site, client_id=client_id, intake_id=intake_id,
                                    model=e.model, provider=e.provider, prompt_tokens=e.prompt_tokens,
                                    completion_tokens=e.completion_tokens, cost_usd=e.cost_usd)
            await conn.execute(
                """UPDATE intakes SET status = 'failed', failure_reason = $2, model = $3, provider = $4,
                                      cost_usd = $5 WHERE id = $1::uuid""",
                intake_id, e.reason, e.model, e.provider, e.cost_usd)
        return outcome

    proposals = _proposals(d, result.value.get("items", []), ctx, outcome)
    async with conn.transaction():
        await budget.record(conn, call_site=call_site, client_id=client_id, intake_id=intake_id, model=result.model,
                            provider=result.provider, prompt_tokens=result.prompt_tokens,
                            completion_tokens=result.completion_tokens, cost_usd=result.cost_usd)
        for p in proposals:
            await conn.execute(
                """INSERT INTO intake_proposals (intake_id, target, field_key, proposed_value, excerpt, speaker,
                                                 spoke_at, valid_until, flags)
                   VALUES ($1::uuid, $2, $3, $4, $5, $6, $7, $8, $9::text[])""",
                intake_id, p["target"], p["field_key"], p["value"], p["excerpt"], p["speaker"], p["spoke_at"],
                p["valid_until"], p["flags"])
        await conn.execute(
            """UPDATE intakes SET status = $2, model = $3, provider = $4, cost_usd = $5, no_card_home = $6
               WHERE id = $1::uuid""",
            intake_id, "ready" if proposals else "nothing_found", result.model, result.provider, result.cost_usd,
            [x for x in result.value.get("no_card_home", []) if isinstance(x, str) and x.strip()])
    if any(outcome.dropped.values()):
        # WARNING, not INFO: "nothing found" and "found but dropped" must be told apart in the logs.
        logger.warning("intake %s dropped items: %s", intake_id, outcome.dropped)
    return outcome


def _proposals(d: Definition, items: List[Dict[str, Any]], ctx: checks.Context,
               outcome: IntakeOutcome) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for item in items:
        if item.get("is_patient_data") is True:          # FR-039: never stored, not even as a Proposal
            outcome.dropped["patient_data"] += 1
            continue
        target, key = item.get("target"), item.get("field_key")
        excerpt = (item.get("excerpt") or "").strip()
        if target != "request":
            key, item = subfield_address(d, target, key, item)
        if target == "request":
            value = _text(item.get("value")) or excerpt
            key = None
        else:
            if not d.has_field(target, key or ""):
                outcome.dropped["invalid"] += 1
                continue
            spec = d.field(target, key)
            current = ctx.current.get(target, {}).get(key)
            merged = complete_value(spec["shape"], current, item.get("value"))
            try:
                validate_value(d, target, key, merged)
            except ValueError:
                outcome.dropped["invalid"] += 1
                continue
            if is_empty(merged) or checks.canonical(merged) == checks.canonical(current):
                outcome.dropped["unchanged"] += 1
                continue
            # Object fields store ONLY the changed sub-fields; they are merged onto the
            # card's value when accepted, so an edit made in between is never undone.
            value = item.get("value") if spec["shape"] == "object" else merged
        flags = checks.flags_for({**item, "target": target, "field_key": key,
                                  "value": merged if target != "request" else value}, ctx)
        out.append({"target": target, "field_key": key, "value": value, "excerpt": excerpt,
                    "speaker": _text(item.get("speaker")), "spoke_at": _date(item.get("spoke_at")),
                    "valid_until": _date(item.get("valid_until")), "flags": flags})
    return merge_same_field(out)


def merge_same_field(proposals: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """One Proposal per object field per Intake. Separate ones would each be built on the
    same base, so accepting the second would undo the first (measured 2026-09-25: one
    old note gave three Tentang usaha Proposals)."""
    out: List[Dict[str, Any]] = []
    by_field: Dict[tuple, Dict[str, Any]] = {}
    for p in proposals:
        key = (p["target"], p["field_key"])
        if p["target"] == "request" or not isinstance(p["value"], dict):
            out.append(p)
            continue
        first = by_field.get(key)
        if first is None:
            by_field[key] = p
            out.append(p)
            continue
        first["value"] = {**first["value"], **p["value"]}
        if p["excerpt"] and p["excerpt"] not in first["excerpt"]:
            first["excerpt"] = f"{first['excerpt']} … {p['excerpt']}"
        first["flags"] = [f for f in checks.FLAG_ORDER if f in set(first["flags"]) | set(p["flags"])]
        for k in ("speaker", "spoke_at", "valid_until"):
            first[k] = first[k] or p[k]
    return out


def on_current(proposed: Any, current: Any) -> Any:
    """An object Proposal's sub-fields laid onto the card's value as it is now."""
    if isinstance(proposed, dict) and isinstance(current, dict):
        return {**current, **proposed}
    return proposed


# ── reading ───────────────────────────────────────────────────────────────────

def proposal_view(r: asyncpg.Record, current: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    cur = current.get(r["target"], {}).get(r["field_key"]) if r["target"] != "request" else None
    return {
        "id": r["id"], "target": r["target"], "field_key": r["field_key"], "current_value": cur,
        "proposed_value": on_current(r["proposed_value"], cur), "excerpt": r["excerpt"], "speaker": r["speaker"],
        "spoke_at": r["spoke_at"].isoformat() if r["spoke_at"] else None,
        "valid_until": r["valid_until"].isoformat() if r["valid_until"] else None,
        "flags": list(r["flags"]), "outcome": r["outcome"], "final_value": r["final_value"], "ticks": r["ticks"],
        "decided_by": r["decided_by_name"], "decided_at": r["decided_at"].isoformat() if r["decided_at"] else None,
    }


_PROPOSALS = """SELECT p.id::text AS id, p.target, p.field_key, p.proposed_value, p.excerpt, p.speaker, p.spoke_at,
                       p.valid_until, p.flags, p.outcome, p.final_value, p.ticks, p.decided_at,
                       dp.display_name AS decided_by_name
                FROM intake_proposals p LEFT JOIN people dp ON dp.id = p.decided_by"""


async def intake_view(conn: asyncpg.Connection, intake_id: str) -> Dict[str, Any]:
    r = await conn.fetchrow(
        """SELECT i.id::text AS id, i.client_id::text AS client_id, c.display_name AS client_name, i.kind, i.status,
                  i.failure_reason, sp.display_name AS submitted_by, i.submitted_at, i.source_ref, i.no_card_home,
                  (i.raw_purged_at IS NULL AND (i.raw_text IS NOT NULL OR i.raw_blob IS NOT NULL)) AS raw_available,
                  i.cost_usd
           FROM intakes i LEFT JOIN clients c ON c.id = i.client_id LEFT JOIN people sp ON sp.id = i.submitted_by
           WHERE i.id = $1::uuid""", intake_id)
    if r is None:
        raise NotFound("Intake tidak ditemukan.")
    current = await values.current_values(conn, r["client_id"]) if r["client_id"] else {}
    rows = await conn.fetch(_PROPOSALS + " WHERE p.intake_id = $1::uuid ORDER BY p.target DESC, p.field_key", intake_id)
    return {
        "id": r["id"], "client": {"id": r["client_id"], "name": r["client_name"]} if r["client_id"] else None,
        "kind": r["kind"], "status": r["status"], "failure_reason": r["failure_reason"],
        "submitted_by": r["submitted_by"], "submitted_at": r["submitted_at"].isoformat(), "source_ref": r["source_ref"],
        "raw_available": r["raw_available"], "no_card_home": r["no_card_home"] or [],
        "proposals": [proposal_view(p, current) for p in rows],
    }


# ── deciding ──────────────────────────────────────────────────────────────────

async def load_proposal(conn: asyncpg.Connection, proposal_id: str) -> asyncpg.Record:
    row = await conn.fetchrow(
        """SELECT p.id::text AS id, p.target, p.field_key, p.proposed_value, p.excerpt, p.speaker, p.spoke_at,
                  p.valid_until, p.flags, p.outcome, i.id::text AS intake_id, i.client_id::text AS client_id,
                  i.kind, i.raw_text
           FROM intake_proposals p JOIN intakes i ON i.id = p.intake_id WHERE p.id = $1::uuid""", proposal_id)
    if row is None or row["client_id"] is None:
        raise NotFound("Usulan tidak ditemukan.")
    return row


async def decide(conn: asyncpg.Connection, d: Definition, *, proposal: asyncpg.Record, outcome: str,
                 final_value: Any, ticks: Dict[str, Any], card_version: Optional[int], person_id: str,
                 pending: bool, person_name: str) -> Dict[str, Any]:
    """Apply one decision inside a transaction. Returns {outcome, card?, request?}."""
    locked = await conn.fetchval(
        "SELECT outcome FROM intake_proposals WHERE id = $1::uuid FOR UPDATE", proposal["id"])
    if locked != "pending":
        raise Conflict("Usulan ini sudah diputuskan.", outcome=locked)
    try:
        checks.check_decision(proposal["flags"], outcome, ticks, proposal["proposed_value"], final_value)
    except ValueError as e:
        raise Invalid(str(e)) from e
    clean_ticks = {k: ticks.get(k) is True for k in ("image_checked", "pic_confirmed") if k in ticks}

    result: Dict[str, Any] = {}
    saved: Any = None
    if outcome != "reject":
        saved = final_value
        if outcome == "accept":
            live = (await values.current_values(conn, proposal["client_id"])).get(proposal["target"], {})
            saved = on_current(proposal["proposed_value"], live.get(proposal["field_key"]))
        if proposal["target"] == "request":
            if not isinstance(saved, str) or not saved.strip():
                raise Invalid("Isi permintaan wajib berupa teks.")
            ctx_pic = (await values.current_values(conn, proposal["client_id"]))["profil"].get("pic") or {}
            channel = "whatsapp_group" if has_whatsapp_lines(proposal["raw_text"]) else \
                CHANNEL_BY_KIND.get(proposal["kind"], "lainnya")
            result["request"] = await create_request(
                conn, client_id=proposal["client_id"], person_id=person_id, from_proposal_id=proposal["id"],
                body=RequestIn(requested_on=proposal["spoke_at"] or date.today(), text=saved,
                               requested_by=proposal["speaker"],
                               is_pic=checks.speaker_is_pic(proposal["speaker"],
                                                            ctx_pic.get("nama") if isinstance(ctx_pic, dict) else None),
                               channel=channel))
        else:
            result["card"] = await values.write(
                conn, d, client_id=proposal["client_id"], part=proposal["target"], field=proposal["field_key"],
                value=saved, valid_until=proposal["valid_until"],
                source={"who": proposal["speaker"] or person_name, "where": f"Intake ({proposal['kind']})",
                        "when": (proposal["spoke_at"] or date.today()).isoformat()},
                person_id=person_id, card_version=card_version, pending=pending, from_proposal_id=proposal["id"])
    stored_outcome = {"accept": "accepted", "edit": "edited", "reject": "rejected"}[outcome]
    await conn.execute(
        """UPDATE intake_proposals SET outcome = $2, final_value = $3, ticks = $4, decided_by = $5::uuid,
                                       decided_at = now() WHERE id = $1::uuid""",
        proposal["id"], stored_outcome, saved, clean_ticks, person_id)
    result["outcome"] = stored_outcome
    return result
