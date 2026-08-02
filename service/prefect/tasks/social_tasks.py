"""
Social Content Harvest tasks for Prefect workflows

Thin wrappers around the roach internal HTTP API (list/download/analyze
primitives) and the harvested_items de-dup ledger. Pacing, the hourly cap,
back-off, and the collection loop live in flows/common/social_harvest.py
(constitution I) — these tasks are single-responsibility API/DB calls that
raise on failure so Prefect's retry mechanism can handle transient errors.
"""
import logging
import os
from typing import Any, Dict, List, Optional

import asyncpg
import httpx
from prefect import task

try:
    from ..blocks.google_credentials import GoogleCredentials, col_letter
    from ..db import db_pool
    from .google_tasks import column_index
except ImportError:
    import sys
    sys.path.append(os.path.dirname(os.path.dirname(__file__)))
    from blocks.google_credentials import GoogleCredentials, col_letter
    from db import db_pool
    from tasks.google_tasks import column_index

logger = logging.getLogger(__name__)

# Truthy spellings a reviewer might type in the sheet's advertisement column.
_ADVERT_TRUE = {"true", "1", "yes", "ya", "y", "t", "x", "✓", "ada", "iklan"}


def _parse_advert(value: Any) -> bool:
    return str(value).strip().lower() in _ADVERT_TRUE


class RoachRateLimitedError(Exception):
    """Roach reported a rate-limit or verification-challenge response."""

    def __init__(self, message: str, code: str = "rate_limited"):
        super().__init__(message)
        self.code = code


class RoachNotFoundError(Exception):
    """Roach reported the profile/content as missing, private, or gone."""


def _roach_client() -> httpx.Client:
    base_url = os.environ["ROACH_API_URL"]
    api_key = os.environ["ROACH_API_KEY"]
    return httpx.Client(base_url=base_url, headers={"X-API-KEY": api_key}, timeout=200.0)


def _raise_for_roach_error(resp: httpx.Response) -> None:
    if resp.status_code == 429:
        body = resp.json()
        raise RoachRateLimitedError(body.get("reason", "rate limited"), code=body.get("code", "rate_limited"))
    if resp.status_code == 404:
        body = resp.json()
        raise RoachNotFoundError(body.get("reason", "not found"))
    resp.raise_for_status()


@task(name="social.profile.list", retries=1, retry_delay_seconds=120)
async def social_profile_list(
    profile_url: str, platform: str, max_items: int = 30, stories_only: bool = False
) -> Dict[str, Any]:
    """
    List every publicly visible content item for a profile via roach.

    Args:
        profile_url: Public profile URL
        platform: "instagram" or "tiktok"
        max_items: How deep the non-video (gallery-dl) listing pages — raise it
            to reach older content for date-range backfills
        stories_only: When True, return only the account's currently-active
            Stories (a fast single-endpoint call for frequent story checks)

    Returns:
        Dict with "profile" metadata and "items" list (may be empty)

    Raises:
        RoachNotFoundError: profile is private/non-existent
        RoachRateLimitedError: rate-limit or verification-challenge response
    """
    with _roach_client() as client:
        # TikTok listing is deliberately paced (sleep between per-post requests
        # to stay under the platform throttle), so deep listings are slow by
        # design — give the roach call a generous read timeout.
        resp = client.post(
            "/list",
            json={
                "profile_url": profile_url, "platform": platform,
                "max_items": max_items, "stories_only": stories_only,
            },
            timeout=1500.0,
        )
        _raise_for_roach_error(resp)
        body = resp.json()
        logger.info(f"Listed {len(body.get('items', []))} items for {profile_url}")
        return body


@task(name="social.item.download", retries=1, retry_delay_seconds=30)
async def social_item_download(
    content_id: str, source_url: str, is_video: bool, content_type: str
) -> Dict[str, Any]:
    """
    Download a single content item via roach.

    Returns:
        Dict with "local_paths" (list of downloaded file paths) and
        "content_type" — the content_type re-derived by roach from the actual
        downloaded files (authoritative; may correct the listing's guess, e.g. a
        single Reel listed as "image"/"carousel" resolves to "video").

    Raises:
        RoachNotFoundError: item is gone (e.g. expired story)
        RoachRateLimitedError: rate-limit or verification-challenge response
    """
    with _roach_client() as client:
        resp = client.post(
            "/download",
            json={
                "content_id": content_id,
                "source_url": source_url,
                "is_video": is_video,
                "content_type": content_type,
            },
        )
        _raise_for_roach_error(resp)
        body = resp.json()
        return {
            "local_paths": body["local_paths"],
            "content_type": body.get("content_type", content_type),
        }


@task(name="social.item.analyze", retries=1, retry_delay_seconds=30)
async def social_item_analyze(
    content_id: str, local_paths: List[str], content_type: str, client: Optional[str] = None
) -> Dict[str, Any]:
    """
    Analyze a downloaded content item via roach.

    Model failures are reported as {"status": "failed", "error": ...} in the
    response body by roach (not raised) so the download is retained and a
    Sheet row is still written (edge case).

    `client` labels whose harvest is spending the tokens. roach is stateless and
    can't know, so it is passed in and echoed into roach's token-usage log —
    otherwise analysis spend can only be seen as one undifferentiated total.
    """
    with _roach_client() as http_client:
        # Analysis can involve model retries/back-off on the roach side, so allow
        # a generous read timeout (the engine also tolerates a failure here).
        resp = http_client.post(
            "/analyze",
            json={
                "content_id": content_id, "local_paths": local_paths,
                "content_type": content_type, "client": client,
            },
            timeout=600.0,
        )
        _raise_for_roach_error(resp)
        return resp.json()["analysis"]


async def _db_pool() -> asyncpg.Pool:
    return await db_pool()


@task(name="social.dedup.check", retries=2, retry_delay_seconds=10)
async def social_dedup_check(platform: str, content_id: str, drive_target: str) -> Optional[Dict[str, Any]]:
    """
    Look up a prior harvest of (platform, content_id, drive_target).

    Returns:
        The existing ledger record as a dict (with `analysis_status` and
        `drive_file_id`), or None if the item has not been harvested for this
        target. A `success` record means skip; a `failed` record means the
        caller should purge it and re-harvest (FR-021 + failed-retry).
    """
    pool = await _db_pool()
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT analysis_status, drive_file_id FROM harvested_items "
                "WHERE platform = $1 AND content_id = $2 AND drive_target = $3",
                platform, content_id, drive_target,
            )
            return dict(row) if row is not None else None
    finally:
        await pool.close()


@task(name="social.dedup.delete", retries=2, retry_delay_seconds=10)
async def social_dedup_delete(platform: str, content_id: str, drive_target: str) -> None:
    """Remove a ledger record so a previously-failed item can be re-harvested fresh."""
    pool = await _db_pool()
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM harvested_items WHERE platform = $1 AND content_id = $2 AND drive_target = $3",
                platform, content_id, drive_target,
            )
    finally:
        await pool.close()


def _coerce_count(value: Any) -> Optional[int]:
    """Coerce a public-count cell ('' / '1.2K' / int / None) to an int, or None."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip().replace(",", "").upper()
    try:
        if text.endswith("K"):
            return int(float(text[:-1]) * 1_000)
        if text.endswith("M"):
            return int(float(text[:-1]) * 1_000_000)
        return int(float(text))
    except ValueError:
        return None


@task(name="social.signal.record", retries=2, retry_delay_seconds=10)
async def social_signal_record(
    platform: str,
    profile_key: str,
    content_id: str,
    content_type: str,
    published_at: Optional[str],
    caption: Optional[str],
    hashtags: Optional[str],
    views: Any,
    likes: Any,
    comments: Any,
    subtitle: Optional[str],
    content_flow: Optional[str],
    summary: Optional[str],
    account_id: Optional[str] = None,
    run_id: Optional[str] = None,
    shares: Any = None,
) -> None:
    """
    Mirror a delivered item's engagement + analysis into the harvested_signals
    store so songbird can rank top performers by engagement via SQL (feature 003).

    `account_id`/`run_id` (feature 004-relational-spine) are optional so this
    task behaves identically with the new columns absent or unpopulated —
    the caller only has an account_id once `social.account.resolve` has
    resolved the item, which is why the write path skips (rather than calls
    this with a null account_id) when a handle is unregistered (FR-016a).

    Idempotent per (platform, content_id): a re-harvest upserts the freshest
    metrics/analysis rather than duplicating the row.

    `shares` (feature 005) is the public share/repost count. roach has always
    parsed it on both TikTok paths — it was simply discarded here. Instagram
    publishes no share count anywhere, so it arrives as None and is recorded as
    absent; `field_availability` is what says WHY it is absent.
    """
    pool = await _db_pool()
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO harvested_signals
                    (platform, profile_key, content_id, content_type, published_at,
                     caption, hashtags, views, likes, comments, subtitle, content_flow, summary,
                     account_id, run_id, shares)
                VALUES ($1, $2, $3, $4, $5::text::timestamptz, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16)
                ON CONFLICT (platform, content_id) DO UPDATE SET
                    profile_key = EXCLUDED.profile_key,
                    content_type = EXCLUDED.content_type,
                    published_at = EXCLUDED.published_at,
                    caption = EXCLUDED.caption,
                    hashtags = EXCLUDED.hashtags,
                    views = EXCLUDED.views,
                    likes = EXCLUDED.likes,
                    comments = EXCLUDED.comments,
                    -- COALESCE, unlike the metrics above: a platform that stops
                    -- returning a share count must not erase one we already
                    -- observed. Absence is not evidence of zero.
                    shares = COALESCE(EXCLUDED.shares, harvested_signals.shares),
                    subtitle = EXCLUDED.subtitle,
                    content_flow = EXCLUDED.content_flow,
                    summary = EXCLUDED.summary,
                    account_id = COALESCE(EXCLUDED.account_id, harvested_signals.account_id),
                    run_id = COALESCE(EXCLUDED.run_id, harvested_signals.run_id),
                    harvested_at = now()
                """,
                platform, profile_key, content_id, content_type, published_at or None,
                caption, hashtags,
                _coerce_count(views), _coerce_count(likes), _coerce_count(comments),
                subtitle, content_flow, summary, account_id, run_id,
                _coerce_count(shares),
            )
    finally:
        await pool.close()


# Closed vocabularies for capture outcomes (feature 005-signal-field-coverage).
# Mirrored by CHECK constraints in migration 007 — validated here for a readable
# error at the call site, and there so a bad value cannot be stored at all.
CAPTURE_KINDS = frozenset({
    "instagram_clip_stats",
    "instagram_feed_stats",
    "instagram_profile_info",
    "tiktok_stats",
})
CAPTURE_OUTCOMES = frozenset({"success", "no_match", "failed", "not_attempted"})
CAPTURE_FAILURE_REASONS = frozenset({
    "not_found", "private", "deleted", "blocked",
    "parse_failure", "timeout", "unexpected_structure",
})


@task(name="social.capture.record-outcome", retries=0)
async def social_capture_record_outcome(
    platform: str,
    content_id: str,
    capture_kind: str,
    outcome: str,
    reason: Optional[str] = None,
    account_id: Optional[str] = None,
    run_id: Optional[str] = None,
) -> None:
    """
    Record whether one supplementary capture produced a value for one item.

    This is what makes an empty engagement value legible. Before feature 005,
    "Instagram never publishes this for carousels" and "the enrichment call
    failed this run" were both stored as NULL, so a collection regression could
    not be told apart from a platform limit.

    APPEND-ONLY (FR-003a). Insert only — no upsert. A re-harvest that re-attempts
    a capture adds a row; it never overwrites the earlier attempt. Constitution
    VII makes this load-bearing rather than stylistic: velocity is derivable only
    from an append-only history.

    `outcome` semantics, and the one that gets missed:
        success        the pass ran and returned a value for this item
        no_match       the pass ran FINE but returned nothing for THIS item —
                       e.g. a carousel absent from the Reels-keyed clips response.
                       Distinct from `failed`; conflating them would leave the
                       corpus exactly as ambiguous as before, with more tables.
        failed         the pass itself broke; `reason` is REQUIRED and classified
        not_attempted  skipped for this item (no cookie session, pass disabled)

    `retries=0`: a local database write whose failure should surface once, and it
    sits on the collection path where constitution II's retry mandate is carved
    out anyway.
    """
    if capture_kind not in CAPTURE_KINDS:
        raise ValueError(f"unknown capture_kind {capture_kind!r}; expected one of {sorted(CAPTURE_KINDS)}")
    if outcome not in CAPTURE_OUTCOMES:
        raise ValueError(f"unknown outcome {outcome!r}; expected one of {sorted(CAPTURE_OUTCOMES)}")
    if outcome == "failed" and reason not in CAPTURE_FAILURE_REASONS:
        # Never defaulted. "Unknown failure" is a classification decision for a
        # human reading a traceback, not something to COALESCE into existence.
        raise ValueError(
            f"outcome='failed' requires a classified reason from {sorted(CAPTURE_FAILURE_REASONS)}, "
            f"got {reason!r}"
        )
    if outcome != "failed":
        reason = None

    pool = await _db_pool()
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO capture_outcomes
                    (platform, content_id, capture_kind, outcome, reason, account_id, run_id)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                """,
                platform, content_id, capture_kind, outcome, reason, account_id, run_id,
            )
    finally:
        await pool.close()


@task(name="social.signal.accounts", retries=2, retry_delay_seconds=10)
async def social_signal_distinct_accounts() -> List[Dict[str, str]]:
    """Distinct (platform, profile_key) with recorded signal — the accounts whose
    Account Social Harvest sheet is the advertisement source of truth (sync flow)."""
    pool = await _db_pool()
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT DISTINCT platform, profile_key FROM harvested_signals ORDER BY platform, profile_key"
            )
            return [{"platform": r["platform"], "profile_key": r["profile_key"]} for r in rows]
    finally:
        await pool.close()


@task(name="social.signal.apply-advertisement", retries=2, retry_delay_seconds=10)
async def social_signal_apply_advertisement(updates: List[List[Any]]) -> int:
    """Set harvested_signals.advertisement from the canonical account sheets.

    Args:
        updates: rows of [platform, content_id, bool]. Only rows whose stored value
            actually differs are written.

    Returns:
        Number of DB rows changed.
    """
    if not updates:
        return 0
    pool = await _db_pool()
    try:
        async with pool.acquire() as conn:
            changed = 0
            for platform, content_id, adv in updates:
                res = await conn.execute(
                    "UPDATE harvested_signals SET advertisement = $3 "
                    "WHERE platform = $1 AND content_id = $2 AND advertisement IS DISTINCT FROM $3",
                    platform, str(content_id), bool(adv),
                )
                changed += int(res.split()[-1]) if isinstance(res, str) and res.startswith("UPDATE") else 0
            return changed
    finally:
        await pool.close()


@task(name="social.detail.sync-advertisement", retries=2, retry_delay_seconds=30)
async def social_detail_sync_advertisement(
    spreadsheet_id: str, canonical: Dict[str, bool], credentials_block_name: str = "google-creds"
) -> int:
    """Correct a per-run detail sheet's `advertisement` column to match the canonical
    (account-sheet) values, so detail sheets stay consistent with the source of truth.

    Args:
        spreadsheet_id: A "Social Harvest Detail" workbook id.
        canonical: {"<platform>|<content_id>": bool} from the account sheets.

    Returns:
        Number of cells corrected (0 if the sheet predates the column or already matches).
    """
    google_creds = await GoogleCredentials.load_or_env(credentials_block_name)
    client = google_creds.get_client()
    svc = client.sheets_service
    info = client.get_spreadsheet_info(spreadsheet_id)
    tabs = [s["title"] for s in info.get("sheets", [])]
    if not tabs:
        return 0
    tab = tabs[0]
    values = svc.spreadsheets().values().get(spreadsheetId=spreadsheet_id, range=tab).execute().get("values", [])
    if not values:
        return 0
    header = values[0]
    if not all(c in header for c in ("platform", "content_id", "advertisement")):
        return 0  # sheet predates the advertisement column
    # Resolved BY NAME, never by fixed position (FR-016b). Feature 005 appends a
    # trailing `shares` column, and tabs written before and after that change
    # coexist until the backfill completes — a positional read would write the
    # advertisement flag onto the wrong column on one of them.
    pi, ci, ai = (column_index(header, c) for c in ("platform", "content_id", "advertisement"))
    a_col = col_letter(ai)
    data = []
    for r_idx, row in enumerate(values[1:], start=2):
        if len(row) <= max(pi, ci):
            continue
        key = f"{str(row[pi]).strip()}|{str(row[ci]).strip()}"
        if key not in canonical:
            continue
        want = "TRUE" if canonical[key] else "FALSE"
        cur = row[ai].strip().upper() if len(row) > ai and row[ai] else ""
        if cur != want:
            data.append({"range": f"'{tab}'!{a_col}{r_idx}", "values": [[want]]})
    if data:
        svc.spreadsheets().values().batchUpdate(
            spreadsheetId=spreadsheet_id, body={"valueInputOption": "RAW", "data": data}
        ).execute()
    return len(data)


@task(name="social.account.resolve", retries=2, retry_delay_seconds=10)
async def social_account_resolve(platform: str, handle: str) -> Dict[str, Any]:
    """
    Resolve a collected handle to an account (feature 004-relational-spine).

    Per specs/004-relational-spine/contracts/account-resolution.md, exactly one
    of three outcomes:
      - "resolved":     handle found, account active -> write the item
      - "unregistered": no account_handles row -> skip, never create one (FR-016b)
      - "inactive":     handle found, account deactivated -> skip

    Lookup is case-insensitive and matches BOTH current and former handles
    (FR-011c), so a rename never orphans a historical `profile_key` value.
    """
    handle_key = (handle or "").strip().lower()
    pool = await _db_pool()
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT a.id AS account_id, a.is_active
                FROM account_handles ah
                JOIN accounts a ON a.id = ah.account_id
                WHERE ah.platform = $1 AND ah.handle_key = $2
                """,
                platform, handle_key,
            )
    finally:
        await pool.close()

    if row is None:
        return {"outcome": "unregistered", "account_id": None}
    if not row["is_active"]:
        return {"outcome": "inactive", "account_id": row["account_id"]}
    return {"outcome": "resolved", "account_id": row["account_id"]}


# Instagram follower_count works again as of 2026-08-01 (research R3, Resolution).
# roach's `web_profile_info` route is still broken upstream (Meta-side 400,
# "ig_business_category_subvertical has been deleted"), but roach now falls back
# to Instagram's persisted GraphQL profile query when that yields no count —
# verified live on `lasikasyik`, which went from no count to follower_count=1342.
# The fallback lives in roach (`_instagram_profile_info_graphql`), so nothing
# here changed: this task still just records whatever count arrives, and records
# nothing when none does. TikTok is unaffected and has always been reliable.
@task(name="social.account.record-followers", retries=2, retry_delay_seconds=10)
async def social_account_record_followers(
    account_id: str, follower_count: Optional[int], run_id: Optional[str] = None
) -> bool:
    """
    Append-only follower observation (feature 004-relational-spine, FR-013a).

    Writes a row ONLY when the platform actually returned a count — there is
    no placeholder/estimated row and no update-in-place; a missed capture
    results in no row, never a zero (constitution VI/VII).

    Returns:
        True if an observation was written, False if there was nothing to record.
    """
    if follower_count is None:
        return False
    pool = await _db_pool()
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO account_follower_observations (account_id, follower_count, run_id) VALUES ($1, $2, $3)",
                account_id, follower_count, run_id,
            )
        return True
    finally:
        await pool.close()


@task(name="social.dedup.record", retries=2, retry_delay_seconds=10)
async def social_dedup_record(
    platform: str,
    profile_key: str,
    content_id: str,
    drive_target: str,
    content_type: str,
    drive_file_id: Optional[str] = None,
    analysis_status: str = "pending",
    account_id: Optional[str] = None,
    run_id: Optional[str] = None,
) -> None:
    """Record a successfully delivered item in the de-dup ledger (FR-021).

    `account_id`/`run_id` (feature 004-relational-spine) are optional for the
    same reason as social_signal_record's — see its docstring.
    """
    pool = await _db_pool()
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO harvested_items
                    (platform, profile_key, content_id, drive_target, content_type, drive_file_id,
                     analysis_status, account_id, run_id)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                ON CONFLICT (platform, content_id, drive_target) DO NOTHING
                """,
                platform, profile_key, content_id, drive_target, content_type, drive_file_id,
                analysis_status, account_id, run_id,
            )
    finally:
        await pool.close()
