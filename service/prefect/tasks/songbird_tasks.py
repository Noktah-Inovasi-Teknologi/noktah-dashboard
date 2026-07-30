"""
Songbird content-generation tasks (feature 003-songbird-content-generation).

Single-responsibility data-access tasks that gather the "hit" signals songbird
generates from: the client's own knowledge (knowledge_records) and the ranked
top-performing harvested posts (harvested_signals). Both raise on failure so
Prefect's retry mechanism handles transient DB errors (constitution I).

Generation itself (prompt assembly + OpenRouter) lives in the engine
(flows/common/songbird.py) using tasks/openrouter_tasks.py; delivery reuses the
existing Google Sheet/Drive tasks in tasks/google_tasks.py.
"""
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

import asyncpg
from prefect import task

try:
    from ..blocks.google_credentials import GoogleCredentials
    from ..hashmap import profile_handle
    from .songbird_ranking import RECENCY_HALF_LIFE_DAYS, rank_performers
except ImportError:
    # For running as standalone script
    import sys
    sys.path.append(os.path.dirname(os.path.dirname(__file__)))
    from blocks.google_credentials import GoogleCredentials
    from hashmap import profile_handle
    from tasks.songbird_ranking import RECENCY_HALF_LIFE_DAYS, rank_performers

logger = logging.getLogger(__name__)

# Per-client content configuration lives in the operator-maintained Clients
# worksheet (the same workbook the content-plan → Jira flow reads). Overridable
# via env so the exact spreadsheet/tab/column can be tuned without code changes.
CLIENTS_SPREADSHEET_ID = os.environ.get(
    "SONGBIRD_CLIENTS_SPREADSHEET_ID", "1-aV46TIn4m_zs3vtCNeS_Bvl3Tt-tgg09uuG_NqgNNY"
)
CLIENTS_TAB = os.environ.get("SONGBIRD_CLIENTS_TAB", "Clients")
CLIENTS_NAME_COLUMN = os.environ.get("SONGBIRD_CLIENTS_NAME_COLUMN", "Name")

# Monthly amounts are configured *per content type*, one column each. These header
# names double as the content-plan `Bentuk` vocabulary — they match the values the
# live content plans already use ("Post" / "Story" / "Short Video"), so a generated
# row drops straight into the content-plan → Jira flow with no translation.
CONTENT_TYPE_COLUMNS = [
    c.strip()
    for c in os.environ.get("SONGBIRD_CONTENT_TYPE_COLUMNS", "Post,Story,Short Video").split(",")
    if c.strip()
]

# Columns holding the client's OWN profile URLs. These become the "own" handles for
# top-performer ranking (FR-008) — no separate config needed, the sheet already has them.
OWN_ACCOUNT_COLUMNS = [
    c.strip()
    for c in os.environ.get("SONGBIRD_OWN_ACCOUNT_COLUMNS", "Instagram,TikTok").split(",")
    if c.strip()
]

# Drive folder holding a client's real content plans — drafts are delivered here so
# reviewers find them where they already look, instead of in a separate silo.
CONTENT_PLAN_FOLDER_COLUMN = os.environ.get(
    "SONGBIRD_CONTENT_PLAN_FOLDER_COLUMN", "Content Plan Folder ID"
)


def _normalize_key(name: str) -> str:
    """Best-effort client_key candidate: lowercased, collapsed whitespace."""
    return re.sub(r"\s+", " ", name.strip().lower())


def _name_tokens(name: str) -> set:
    """Word tokens of a client name, for identity comparison."""
    return {t for t in re.split(r"[^\w]+", _normalize_key(name)) if t}


def _is_same_client(requested: str, candidate: str) -> bool:
    """
    Decide whether a knowledge-base client is the client we asked for.

    Trigram similarity alone is unsafe for this roster: nearly every client is named
    "Klinik Mata …" or "Klinik Utama …", so the shared prefix dominates the score.
    Measured, "klinik mata smec bitung" scored 0.467 against BOTH "klinik mata bireuen"
    and "klinik mata sampang" — a tie decided arbitrarily, which grounded a Bitung plan
    in Bireuen's knowledge base.

    The reliable signal is tokens, not characters: one name must be a token-subset of
    the other, i.e. the KB uses a shorter or longer form of the same name.

        "lasik asyik" ⊆ "lasik asyik by smec tebet"   -> same client
        "klinik utama gasa" == "klinik utama GASA"     -> same client
        "klinik mata bireuen" vs "klinik mata smec bitung"
            -> neither is a subset ('bireuen'/'bitung' are distinctive) -> different
    """
    a, b = _name_tokens(requested), _name_tokens(candidate)
    if not a or not b:
        return False
    return a <= b or b <= a


async def _db_pool() -> asyncpg.Pool:
    # Reuse the harvest DSN by default (same Postgres database); a dedicated
    # SONGBIRD_DB_URL may override it.
    dsn = os.environ.get("SONGBIRD_DB_URL") or os.environ["HARVEST_DB_URL"]
    return await asyncpg.create_pool(dsn, min_size=1, max_size=5)


@task(name="songbird.client.context", retries=2, retry_delay_seconds=30)
async def songbird_client_context(client_name: str, limit: int = 40) -> Dict[str, Any]:
    """
    Fetch the client's *current* knowledge records to ground generation.

    Trigram similarity is used only to gather *candidates*; identity is then confirmed
    by `_is_same_client`, because on this roster the shared "Klinik Mata …" prefix makes
    similarity alone match the wrong client. A client with no knowledge base returns no
    records rather than borrowing another client's — grounding a plan in the wrong
    brand's facts is far worse than generating from marketing params alone (FR-011).

    Returns:
        {"client_name": <resolved or input>, "records": [{"subject", "information"}, ...]}
    """
    key = _normalize_key(client_name)
    pool = await _db_pool()
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT client_name, subject, information
                FROM knowledge_records
                WHERE superseded_by IS NULL
                  AND (client_key = $1 OR client_name ILIKE $2 OR similarity(client_key, $1) > 0.3)
                ORDER BY similarity(client_key, $1) DESC, "timestamp" DESC
                LIMIT $3
                """,
                key, f"%{client_name.strip()}%", limit,
            )
    finally:
        await pool.close()

    accepted = [r for r in rows if _is_same_client(client_name, r["client_name"])]
    rejected = {r["client_name"] for r in rows} - {r["client_name"] for r in accepted}
    if rejected:
        logger.warning(
            f"Ignored knowledge records from {sorted(rejected)} — they are a different "
            f"client to '{client_name}' despite a close name"
        )

    records = [{"subject": r["subject"], "information": r["information"]} for r in accepted]
    resolved = accepted[0]["client_name"] if accepted else client_name
    if not records:
        logger.warning(
            f"No knowledge base found for '{client_name}' — generation will rely on "
            f"harvested signal + marketing params only"
        )
    logger.info(f"Loaded {len(records)} current knowledge records for '{client_name}' (resolved '{resolved}')")
    return {"client_name": resolved, "records": records}


@task(name="songbird.signal.top-performers", retries=2, retry_delay_seconds=30)
async def songbird_top_performers(
    handles: List[str],
    limit: int = 10,
    window_days: int = 180,
    *,
    content_mix: Optional[Dict[str, int]] = None,
    exclude_advertisement: bool = True,
    recency_half_life_days: float = RECENCY_HALF_LIFE_DAYS,
    stats: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """
    Select exemplar posts for a set of profile handles, stratified by content type
    and scored relative to each account's own baseline (see `songbird_ranking`).

    Runs in two phases so percentile scoring sees the *whole* window population
    without transferring roach's analysis text (~1-3 KB/row) for every candidate:
    phase 1 fetches ranking columns only, phase 2 hydrates just the selected rows.

    Only content published within the last `window_days` days is considered
    (FR-009a); items with an unknown published_at are included so signal is never
    silently dropped. Handles are matched case-insensitively against
    harvested_signals.profile_key. Returns [] when nothing has been harvested for
    those handles — the engine then falls back to client-knowledge + params (FR-011).

    Args:
        handles: Profile handles (own and/or competitor) to rank across.
        limit: Total exemplars to return across all content types.
        window_days: Rolling recency window in days (default 180, FR-009a).
        content_mix: {content_type: amount} being generated; the exemplar budget is
            apportioned to match, so a plan needing Posts sees Post exemplars.
        exclude_advertisement: Drop reviewer-flagged paid/boosted posts — their reach
            was bought, so it is not evidence that the creative works.
        recency_half_life_days: Half-life of the recency tilt.
        stats: Optional dict updated in place with selection stats for the summary.
    """
    if not handles or limit <= 0:
        return []
    lowered = list(dict.fromkeys(h.strip().lower() for h in handles if h and h.strip()))
    if not lowered:
        return []

    pool = await _db_pool()
    try:
        async with pool.acquire() as conn:
            # Phase 1 — ranking columns for the whole window population.
            # `caption`/`hashtags` are small and are what trend detection needs across
            # the whole population; only the large roach analysis columns
            # (subtitle/content_flow/summary, ~1-3 KB each) are deferred to phase 2.
            candidates = await conn.fetch(
                f"""
                SELECT id, platform, profile_key, content_type, published_at,
                       views, likes, comments, advertisement, caption, hashtags
                FROM harvested_signals
                WHERE lower(profile_key) = ANY($1::text[])
                  AND (published_at IS NULL
                       OR published_at >= now() - make_interval(days => $2))
                  {"AND advertisement = false" if exclude_advertisement else ""}
                """,
                lowered, window_days,
            )
            rows = [dict(r) for r in candidates]

            selected, selection_stats = rank_performers(
                rows, total=limit, content_mix=content_mix,
                window_days=window_days, half_life_days=recency_half_life_days,
            )
            if not selected:
                logger.info(f"No usable signal for {len(lowered)} handle(s) within {window_days}d")
                if stats is not None:
                    stats.update(selection_stats)
                return []

            # Phase 2 — hydrate only what we kept with the fat analysis columns.
            hydrated = await conn.fetch(
                """
                SELECT id, caption, hashtags, subtitle, content_flow, summary
                FROM harvested_signals WHERE id = ANY($1::bigint[])
                """,
                [r["id"] for r in selected],
            )
    finally:
        await pool.close()

    detail = {r["id"]: dict(r) for r in hydrated}
    performers = [{**row, **detail.get(row["id"], {})} for row in selected]

    if stats is not None:
        stats.update(selection_stats)
    logger.info(
        f"Selected {len(performers)} exemplars from {len(rows)} candidates across "
        f"{len(lowered)} handle(s) within {window_days}d "
        f"(buckets: {selection_stats.get('by_bucket')}, coverage: {selection_stats.get('coverage')})"
    )
    return performers


async def _load_client_row(
    client_name: str, credentials_block_name: str
) -> Tuple[Dict[str, Any], List[str]]:
    """
    Find a client's row in the Clients worksheet.

    Shared by every per-client config lookup so row matching (case-insensitive
    exact, then contains) lives in exactly one place.

    Returns:
        (row as a dict, the worksheet's column names)

    Raises:
        ValueError: the sheet is unreadable, the name column is absent, or the
            client has no row — callers MUST fail fast rather than guess (FR-003b).
    """
    google_creds = await GoogleCredentials.load_or_env(credentials_block_name)
    client = google_creds.get_client()
    df = client.to_dataframe(
        spreadsheet_id=CLIENTS_SPREADSHEET_ID, sheet_name=CLIENTS_TAB, header_row=0
    )
    records = df.to_dict("records") if df is not None and not df.empty else []
    if not records:
        raise ValueError(
            f"Clients worksheet '{CLIENTS_TAB}' ({CLIENTS_SPREADSHEET_ID}) is empty or unreadable"
        )

    columns = list(records[0].keys())
    if CLIENTS_NAME_COLUMN not in columns:
        raise ValueError(
            f"Clients worksheet has no client-name column '{CLIENTS_NAME_COLUMN}'; found {columns}. "
            f"Set SONGBIRD_CLIENTS_NAME_COLUMN to match the sheet."
        )

    target = client_name.strip().lower()
    row = next(
        (r for r in records if str(r.get(CLIENTS_NAME_COLUMN, "")).strip().lower() == target),
        None,
    ) or next(
        (r for r in records if target in str(r.get(CLIENTS_NAME_COLUMN, "")).strip().lower()),
        None,
    )
    if row is None:
        raise ValueError(
            f"No row for client '{client_name}' in Clients worksheet column '{CLIENTS_NAME_COLUMN}'"
        )
    return row, columns


@task(name="songbird.config.own-handles", retries=2, retry_delay_seconds=30)
async def songbird_config_own_handles(
    client_name: str, credentials_block_name: str = "google-creds"
) -> List[str]:
    """
    Resolve a client's OWN account handles from the Clients worksheet (FR-008).

    The sheet already carries each client's Instagram/TikTok profile URL, so no new
    configuration is needed — the URLs are normalized to the bare handle that
    social-harvest stores in `harvested_signals.profile_key`.

    This is also what closes songbird's learning loop: generated content is published,
    the next harvest captures it as own content, and it scores in the next ranking.

    Best-effort — returns [] rather than raising if the sheet or columns are missing,
    since a missing own handle degrades grounding (FR-011) but must not fail a run.
    """
    try:
        row, columns = await _load_client_row(client_name, credentials_block_name)
    except Exception as exc:
        logger.warning(f"Could not resolve own handles for '{client_name}': {exc}")
        return []

    present = [c for c in OWN_ACCOUNT_COLUMNS if c in columns]
    if not present:
        logger.warning(
            f"Clients worksheet has none of the own-account columns {OWN_ACCOUNT_COLUMNS}; "
            f"found {columns}. Set SONGBIRD_OWN_ACCOUNT_COLUMNS to match the sheet."
        )
        return []

    handles = [h for h in (profile_handle(str(row.get(c) or "")) for c in present) if h]
    handles = list(dict.fromkeys(handles))
    logger.info(f"Client '{client_name}' own handles: {handles or '(none configured)'}")
    return handles


@task(name="songbird.config.draft-folder", retries=2, retry_delay_seconds=30)
async def songbird_config_draft_folder(
    client_name: str, credentials_block_name: str = "google-creds"
) -> Optional[str]:
    """
    Resolve the Drive folder a client's draft should be delivered into.

    Uses the "Content Plan Folder ID" already maintained in the Clients worksheet, so
    a draft lands beside that client's real content plans rather than in a separate
    songbird silo — no extra configuration to keep in sync.

    Best-effort: returns None when the sheet, column, or value is missing, and the
    caller falls back to SONGBIRD_DRIVE_PARENT_ID.
    """
    try:
        row, columns = await _load_client_row(client_name, credentials_block_name)
    except Exception as exc:
        logger.warning(f"Could not resolve a draft folder for '{client_name}': {exc}")
        return None

    if CONTENT_PLAN_FOLDER_COLUMN not in columns:
        logger.warning(
            f"Clients worksheet has no '{CONTENT_PLAN_FOLDER_COLUMN}' column; found {columns}. "
            f"Set SONGBIRD_CONTENT_PLAN_FOLDER_COLUMN to match the sheet."
        )
        return None

    folder_id = str(row.get(CONTENT_PLAN_FOLDER_COLUMN) or "").strip()
    if not folder_id or folder_id == "-":
        logger.warning(f"Client '{client_name}' has no '{CONTENT_PLAN_FOLDER_COLUMN}' value")
        return None
    logger.info(f"Client '{client_name}' draft folder: {folder_id}")
    return folder_id


@task(name="songbird.config.content-mix", retries=2, retry_delay_seconds=30)
async def songbird_config_content_mix(
    client_name: str, credentials_block_name: str = "google-creds"
) -> Dict[str, int]:
    """
    Read a client's configured monthly content amounts **per content type** from
    the Clients worksheet (FR-003b).

    The sheet holds one column per content type (`Post`, `Story`, `Short Video`),
    so the plan's size *and* its composition both come from config — the total is
    simply their sum. Types with a zero/blank amount are omitted, so a client
    configured for posts only never gets stories generated.

    Matches the client row case-insensitively (exact, then contains) on the
    client-name column. The spreadsheet id, tab, name column, and the content-type
    column list are env-overridable.

    Returns:
        Ordered {content_type: amount} for every type with a positive amount,
        e.g. {"Post": 4, "Story": 4, "Short Video": 4}.

    Raises:
        ValueError: the client row is missing, the expected columns are absent, or
            every amount is zero/blank — the flow MUST fail fast rather than guess
            a count (FR-003b).
    """
    row, columns = await _load_client_row(client_name, credentials_block_name)

    present = [c for c in CONTENT_TYPE_COLUMNS if c in columns]
    if not present:
        raise ValueError(
            f"Clients worksheet has none of the content-type columns {CONTENT_TYPE_COLUMNS}; "
            f"found {columns}. Set SONGBIRD_CONTENT_TYPE_COLUMNS to match the sheet."
        )
    missing = [c for c in CONTENT_TYPE_COLUMNS if c not in columns]
    if missing:
        logger.warning(f"Clients worksheet is missing content-type column(s) {missing}; ignoring them")

    mix: Dict[str, int] = {}
    for column in present:
        amount = _coerce_quantity(row.get(column))
        if amount and amount > 0:
            mix[column] = amount

    if not mix:
        raw = {c: row.get(c) for c in present}
        raise ValueError(
            f"Client '{client_name}' has no positive content amount in any of {present} (got {raw!r})"
        )

    logger.info(
        f"Client '{client_name}' configured monthly content mix: "
        f"{', '.join(f'{k}={v}' for k, v in mix.items())} (total {sum(mix.values())})"
    )
    return mix


def _coerce_quantity(value: Any) -> Optional[int]:
    """Coerce a quantity cell ('12' / 12 / 12.0 / '12 konten') to a positive int, or None."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return int(value)
    match = re.search(r"\d+", str(value))
    return int(match.group(0)) if match else None
