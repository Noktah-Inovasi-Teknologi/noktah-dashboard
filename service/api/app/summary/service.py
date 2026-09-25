"""
The Ringkasan (Summary, G-27): a short overview written by AI from the CONFIRMED
parts of a Client Card only (current Profil and Guideline values and open
Requests). Never pending values, never raw Intake material.

  - `fingerprint` hashes exactly what the summary is built from; the summary is
    STALE when the fingerprint moved on.
  - refresh happens only when stale AND the last summary is older than 24 h
    ("at most once a day per Client").
  - the AI cap applies: at the cap, nothing is generated and the last summary
    stays visible, marked out of date (`cap_paused`).
  - it is marked "dibuat otomatis", never edited by hand, never a source.
  - a throttled model is retried patiently (SUMMARY_BACKOFF: nobody is waiting),
    and once one Client still fails after that, the run leaves the rest for the
    next hour instead of queueing more calls behind an overloaded provider. Every
    failure is reported with its Client and reason, which the flow alerts on.
"""
import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import asyncpg

from ..ai import budget
from ..ai.openrouter import AiFailure, chat_json
from ..card.values import current_values
from ..errors import AiCapReached
from ..settings import get_settings

PROMPT_VERSION = "summary_v1"
MIN_INTERVAL = timedelta(hours=24)
SUMMARY_BACKOFF = (10, 30, 60)  # seconds before each retry of a 429

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["siapa", "promo_berjalan", "aturan_kunci", "permintaan_terbuka"],
    "properties": {
        "siapa": {"type": "string"},
        "promo_berjalan": {"type": "array", "items": {"type": "string"}},
        "aturan_kunci": {"type": "array", "items": {"type": "string"}},
        "permintaan_terbuka": {"type": "array", "items": {"type": "string"}},
    },
}

SYSTEM = (
    "Anda menulis Ringkasan singkat tentang satu klien agensi konten, dalam Bahasa Indonesia, "
    "HANYA dari data kartu yang diberikan. Jangan menambah fakta. Salin nama, harga, tagline dan "
    "istilah persis seperti tertulis. 'siapa': 1-2 kalimat. 'promo_berjalan': baris promo yang "
    "masih berlaku beserta tanggal berlakunya. 'aturan_kunci': 3-6 aturan konten yang paling sering "
    "penting (sapaan, larangan, hashtag wajib). 'permintaan_terbuka': permintaan terbuka, terbaru dulu. "
    "Kosongkan daftar bila tidak ada datanya."
)


async def _inputs(conn: asyncpg.Connection, client_id: str) -> Dict[str, Any]:
    cur = await current_values(conn, client_id)
    reqs = await conn.fetch(
        """SELECT requested_on, text, status FROM client_requests
           WHERE client_id = $1::uuid AND status IN ('baru', 'diproses') ORDER BY requested_on DESC""", client_id)
    name = await conn.fetchval("SELECT display_name FROM clients WHERE id = $1::uuid", client_id)
    return {
        "klien": name, "profil": cur["profil"], "guideline": cur["guideline"],
        "permintaan_terbuka": [{"tanggal": r["requested_on"].isoformat(), "isi": r["text"], "status": r["status"]}
                               for r in reqs],
    }


def fingerprint(inputs: Dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(inputs, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def is_empty_card(inputs: Dict[str, Any]) -> bool:
    return not inputs["profil"] and not inputs["guideline"] and not inputs["permintaan_terbuka"]


async def summary_view(conn: asyncpg.Connection, client_id: str) -> Optional[Dict[str, Any]]:
    row = await conn.fetchrow(
        "SELECT body, generated_at, source_fingerprint FROM client_summaries WHERE client_id = $1::uuid AND is_current",
        client_id)
    if row is None:
        return None
    stale = fingerprint(await _inputs(conn, client_id)) != row["source_fingerprint"]
    usage = await budget.usage(conn)
    return {"body": row["body"], "generated_at": row["generated_at"].isoformat(), "stale": stale,
            "cap_paused": stale and usage["paused"]}


def _validate(parsed: Any) -> None:
    if not isinstance(parsed, dict):
        raise ValueError("Jawaban harus objek JSON.")
    missing = [k for k in SCHEMA["required"] if k not in parsed]
    if missing:
        raise ValueError(f"Kunci wajib tidak ada: {missing}")
    if not isinstance(parsed["siapa"], str):
        raise ValueError("'siapa' harus teks.")
    for key in ("promo_berjalan", "aturan_kunci", "permintaan_terbuka"):
        if not isinstance(parsed[key], list) or not all(isinstance(x, str) for x in parsed[key]):
            raise ValueError(f"'{key}' harus daftar teks.")


async def refresh_one(conn: asyncpg.Connection, client_id: str, now: Optional[datetime] = None,
                      failure: Optional[Dict[str, str]] = None) -> str:
    """'refreshed' | 'fresh' | 'too_soon' | 'empty' | 'paused' | 'failed'.

    On 'failed', `failure` (when given) receives the reason and detail.
    """
    now = now or datetime.now(timezone.utc)
    inputs = await _inputs(conn, client_id)
    if is_empty_card(inputs):
        return "empty"
    fp = fingerprint(inputs)
    last = await conn.fetchrow(
        "SELECT source_fingerprint, generated_at FROM client_summaries WHERE client_id = $1::uuid AND is_current",
        client_id)
    if last and last["source_fingerprint"] == fp:
        return "fresh"
    if last and now - last["generated_at"] < MIN_INTERVAL:
        return "too_soon"
    try:
        await budget.check_budget(conn)
    except AiCapReached:
        return "paused"
    settings = get_settings()
    try:
        result = await chat_json(
            api_key=settings.openrouter_api_key, model=settings.summary_model,
            messages=[{"role": "system", "content": SYSTEM},
                      {"role": "user", "content": json.dumps(inputs, ensure_ascii=False, default=str)}],
            schema=SCHEMA, validate=_validate,
            call_site="hub.summary", max_tokens=1500, timeout=settings.ai_timeout_seconds,
            rate_limit_backoff=SUMMARY_BACKOFF)
    except AiFailure as e:
        if failure is not None:
            failure.update(reason=e.reason, detail=e.detail)
        return "failed"
    async with conn.transaction():
        await budget.record(conn, call_site="hub.summary", client_id=client_id, intake_id=None, model=result.model,
                            provider=result.provider, prompt_tokens=result.prompt_tokens,
                            completion_tokens=result.completion_tokens, cost_usd=result.cost_usd)
        await conn.execute("UPDATE client_summaries SET is_current = false WHERE client_id = $1::uuid AND is_current",
                           client_id)
        await conn.execute(
            """INSERT INTO client_summaries (client_id, body, source_fingerprint, model, cost_usd)
               VALUES ($1::uuid, $2, $3, $4, $5)""",
            client_id, result.value, fp, result.model, result.cost_usd)
    return "refreshed"


async def refresh_all(conn: asyncpg.Connection) -> Dict[str, Any]:
    """Counts per outcome, plus `failures` {Client name: reason} (the alert hook lists these)."""
    rows = await conn.fetch(
        "SELECT id::text AS id, display_name FROM clients WHERE COALESCE(status, 'active') <> 'inactive' "
        "ORDER BY display_name")
    counts: Dict[str, int] = {}
    failures: Dict[str, str] = {}
    throttled = False
    for r in rows:
        if throttled:
            counts["deferred"] = counts.get("deferred", 0) + 1
            continue
        failure: Dict[str, str] = {}
        outcome = await refresh_one(conn, r["id"], failure=failure)
        counts[outcome] = counts.get(outcome, 0) + 1
        if outcome == "failed":
            failures[r["display_name"]] = f"{failure.get('reason')}: {failure.get('detail')}"
            throttled = failure.get("detail", "").startswith("HTTP 429")
    return {"refreshed": counts.get("refreshed", 0), "skipped_fresh": counts.get("fresh", 0) + counts.get("too_soon", 0),
            "empty": counts.get("empty", 0), "paused_by_cap": counts.get("paused", 0), "failed": counts.get("failed", 0),
            # Stale or not, these weren't looked at: a provider was still throttling. Next hour tries them.
            "deferred": counts.get("deferred", 0), "failures": failures}
