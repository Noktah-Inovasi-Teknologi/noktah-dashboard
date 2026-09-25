"""
Shared Social Content Harvest engine

Implements the collection loop, delivery, pacing, hourly cap, back-off, and
de-duplication that both `social-harvest-recent` and `social-harvest-window`
flows share (FR-016a). This module never raises to its caller — it always
returns a Dict[str, Any] with start_time/end_time/data/summary/error
(constitution I), and sets end_time in a finally block (constitution V).
"""
import asyncio
import hashlib
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlparse

from prefect.logging import get_run_logger

try:
    from ...tasks.social_tasks import (
        RoachNotFoundError,
        RoachRateLimitedError,
        social_account_record_followers,
        social_account_resolve,
        social_capture_record_outcome,
        social_dedup_check,
        social_dedup_delete,
        social_dedup_record,
        social_item_analyze,
        social_item_download,
        social_known_items,
        social_observation_record,
        social_profile_list,
        social_signal_record,
        social_signal_record_metrics,
    )
    from ...tasks.run_tasks import run_record_finish, run_record_start
    from ...tasks.google_tasks import (
        drive_file_delete,
        drive_file_upload,
        drive_folder_ensure,
        sheets_create,
        sheets_rows_append,
        sheets_rows_delete_by_content_id,
        sheets_spreadsheet_ensure,
        sheets_tab_ensure,
        sheets_tab_row_count,
    )
    from ...tasks.utility_tasks import RateWindow, randomized_item_delay
    from ...tasks.extraction_tasks import (
        SpendCeilingExceeded,
        extraction_cost_ceiling_check,
        extraction_quarantine_record,
        extraction_record_store,
    )
except ImportError:
    import sys

    sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
    from tasks.social_tasks import (
        RoachNotFoundError,
        RoachRateLimitedError,
        social_account_record_followers,
        social_account_resolve,
        social_capture_record_outcome,
        social_dedup_check,
        social_dedup_delete,
        social_dedup_record,
        social_item_analyze,
        social_item_download,
        social_known_items,
        social_observation_record,
        social_profile_list,
        social_signal_record,
        social_signal_record_metrics,
    )
    from tasks.run_tasks import run_record_finish, run_record_start
    from tasks.extraction_tasks import (
        SpendCeilingExceeded,
        extraction_cost_ceiling_check,
        extraction_quarantine_record,
        extraction_record_store,
    )
    from tasks.google_tasks import (
        drive_file_delete,
        drive_file_upload,
        drive_folder_ensure,
        sheets_create,
        sheets_rows_append,
        sheets_rows_delete_by_content_id,
        sheets_spreadsheet_ensure,
        sheets_tab_ensure,
        sheets_tab_row_count,
    )
    from tasks.utility_tasks import RateWindow, randomized_item_delay

logger = logging.getLogger(__name__)

MAX_PROFILES = 5
BACKOFF_STEPS_SECONDS = [60, 120, 240]
DETAIL_FOLDER_NAME = "Social Harvest Detail"

# Human-readable Drive folder name per platform (folder level 1).
PLATFORM_DISPLAY = {
    "instagram": "Instagram", "tiktok": "TikTok",
    "x": "X", "threads": "Threads", "facebook": "Facebook",
}

# Content-format subfolder per content_type (folder level 3, under the account).
FORMAT_FOLDER = {
    "video": "Short Video", "story": "Story",
    "image": "Post", "carousel": "Post", "text": "Text",
}

# Per-account quarterly sheet columns (FR-016 delivery redesign).
# `advertisement` is a manual TRUE/FALSE flag (default FALSE) a reviewer sets to
# mark content that was run as a paid ad/boost — Instagram does not expose whether
# a post was advertised, so it cannot be auto-detected (see harvest notes).
DEFAULT_ADVERTISEMENT = "FALSE"
ACCOUNT_HEADER = [
    "id", "username", "platform", "content_id", "content_type", "source_url", "published_at",
    "caption", "hashtags", "views", "likes", "comments", "drive_file_ids",
    "subtitle", "content_flow", "summary", "harvest_name", "harvest_date", "advertisement",
    # Feature 005: appended at the END, never inserted mid-layout. Inserting
    # beside views/likes/comments — where it logically belongs — would shift
    # `advertisement`, the one column a human edits and social-harvest-sync
    # reads back by position on older tabs. Logical grouping is not worth
    # re-indexing reviewer-entered data; the metrics sit together in Postgres,
    # where column order is irrelevant.
    "shares",
]
# Per-run detail sheet = account columns + the account folder id.
# NOTE: because this is derived, appending a column to ACCOUNT_HEADER *inserts*
# it mid-layout here, landing where older detail sheets hold `account_folder_id`.
# That is why `sheet-header-backfill` leaves detail sheets alone. It is safe
# because each run creates its own detail sheet and never rewrites an earlier
# one, and because every read of these sheets resolves columns by name.
DETAIL_HEADER = ACCOUNT_HEADER + ["account_folder_id"]
# 0-based index of content_id within ACCOUNT_HEADER (for row-deletion on retry).
CONTENT_ID_COL = ACCOUNT_HEADER.index("content_id")

DepthSelector = Callable[[List[Dict[str, Any]]], List[Dict[str, Any]]]

# The public counts an observation carries (feature 006). Mirrors
# `velocity_tasks.METRICS` and the engagement columns on `metric_observations`;
# named here so the observation pass spells them once rather than per call site.
OBSERVED_METRICS = ("views", "likes", "comments", "shares")


def _platform_display(platform: str) -> str:
    return PLATFORM_DISPLAY.get(platform, platform.title())


def _format_folder(content_type: str) -> str:
    return FORMAT_FOLDER.get(content_type, "Post")


def _quarter_tab_name(dt: datetime) -> str:
    """Sheet-tab name for the quarter the harvest runs in, e.g. 'Q3 - 2026'."""
    quarter = (dt.month - 1) // 3 + 1
    return f"Q{quarter} - {dt.year}"


def _resolve_harvest_name(harvest_name: Optional[str]) -> str:
    """Use the explicit name if given, else the Prefect flow-*run* name.

    The run name is the auto-generated 'adjective-animal' label (e.g.
    'graceful-rook') unique to each run, so the detail file and rows are
    traceable back to a specific Prefect run.
    """
    if harvest_name:
        return harvest_name
    try:
        from prefect.runtime import flow_run

        if flow_run.name:
            return flow_run.name
    except Exception:
        pass
    return "Social Harvest"


def _cell(value: Any) -> Any:
    """Coerce a value into something the Sheets API accepts in a single cell.

    The analysis model sometimes returns `flow`/`subtitle` as a JSON array
    rather than a string; Sheets rejects list/dict cells, so flatten them.
    """
    if isinstance(value, (list, tuple)):
        return "\n".join(str(v) for v in value)
    if isinstance(value, dict):
        return str(value)
    return value if value is not None else ""


def _row_core(
    username: str, platform: str, item: Dict[str, Any], analysis: Dict[str, Any],
    drive_file_ids: List[str], harvest_name: str, harvest_date: str,
) -> List[Any]:
    """The shared columns (everything after `id`) for account + detail rows."""
    counts = item.get("public_counts") or {}
    return [_cell(v) for v in (
        username, platform, item["content_id"], item.get("content_type", ""),
        item.get("source_url", ""), item.get("published_at") or "",
        item.get("caption", ""), " ".join(item.get("hashtags") or []),
        counts.get("views", ""), counts.get("likes", ""), counts.get("comments", ""),
        ",".join(drive_file_ids),
        analysis.get("subtitle", ""), analysis.get("flow", ""), analysis.get("summary", ""),
        harvest_name, harvest_date, DEFAULT_ADVERTISEMENT,
        # Trailing, matching ACCOUNT_HEADER. Empty for Instagram (no public
        # share count exists there) — field_availability is what records that
        # this blank is a platform limit rather than a collection miss.
        counts.get("shares", ""),
    )]


# Which supplementary capture pass is responsible for which item, per platform
# and content type. Only passes that actually run are recorded — the primary
# listing is NOT a supplementary capture (its failure aborts the profile and is
# already a run-level fact, so recording it here would double-count).
def _capture_kind_for(platform: str, content_type: str) -> Optional[str]:
    # TikTok deliberately returns None. Its counts come straight off the PRIMARY
    # listing — yt-dlp's flat entries and `_tiktok_counts` on the gallery-dl
    # photo/story paths — and roach has no supplementary TikTok pass at all.
    # Recording `tiktok_stats` would invent a pass name for every TikTok row, in
    # the one table whose entire purpose is provenance. A fabricated pass name is
    # worse than the NULL it replaced, because it makes a positive claim.
    if platform == "instagram":
        # Two per-item Instagram passes now run. The Reels-grid pass is keyed by
        # shortcode and only ever matches video; the feed pass (added after the
        # 2026-08-02 probe) supplies comment counts across all feed types. A
        # carousel is therefore attributed to the feed pass — attributing it to
        # clips would record `no_match` for a pass that was never going to match
        # it, which is noise rather than provenance.
        return "instagram_clip_stats" if content_type == "video" else "instagram_feed_stats"
    return None


def _media_hash(local_paths: List[str]) -> str:
    """Hash the media actually sent to the model — the cache key for FR-039.

    Content identity, not file identity: the paths are temporary and the same
    media re-downloaded lands at a different path every run, so hashing bytes is
    the only thing that makes "unchanged input" answerable.

    Best-effort. A hash we cannot compute must not abort a delivered item, and an
    empty hash simply means this row cannot serve as a cache hit later — it
    degrades the optimisation, never the record.
    """
    digest = hashlib.sha256()
    try:
        for path in sorted(local_paths):
            with open(path, "rb") as fh:
                for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                    digest.update(chunk)
        return f"sha256:{digest.hexdigest()}"
    except OSError:
        return ""


async def _extraction_ceiling_reached(run_logger, username: str, content_id: str) -> bool:
    """Has the monthly EXTRACTION ceiling been reached? (FR-037a)

    Returns True to skip this item's extraction. Collection is unaffected either
    way — the caller still uploads the media, records the metrics and writes the
    sheet row.

    Checked per item rather than once per run because spend accrues DURING the
    run: a single harvest that crosses the ceiling half way should stop there, not
    at the next run's start. The query is one indexed SUM over two small tables,
    which at this system's volume (hundreds of rows a month) costs far less than
    the model call it guards.

    Fails OPEN on an unexpected error. A ceiling that cannot be read is a
    monitoring problem; refusing to extract because of it would turn a database
    hiccup into silent data loss, and the per-run threshold plus the batch flows'
    own checks remain in front of every large spend.
    """
    try:
        await extraction_cost_ceiling_check(projection_usd=None)
        return False
    except SpendCeilingExceeded as e:
        run_logger.error(
            f"{username}/{content_id}: EXTRACTION DEFERRED — {e} "
            f"Media and metrics are still being collected; only the extraction is skipped, "
            f"and it can be backfilled next month.")
        return True
    except Exception as e:
        run_logger.warning(
            f"could not read the extraction spend ceiling ({e}); proceeding with extraction. "
            f"The per-run threshold and the batch flows' own checks still apply.")
        return False


async def _record_structured_extraction(
    platform: str, content_id: str, content_type: str, analysis: Dict[str, Any],
    content_hash: str, account_id: Optional[str], run_id: Optional[str],
) -> None:
    """Store the structured extraction, or its quarantine record (feature 007).

    Runs ALONGSIDE the existing prose write, never instead of it — this feature
    is additive, and the reviewer-facing sheets and `harvested_signals` keep their
    current shape and content.

    The three outcomes are recorded on different tables, deliberately:

      success     -> content_extractions + extraction_beats (+ attributes)
      quarantined -> extraction_quarantine, with the raw output and both
                     attempts' errors. NEVER content_extractions.
      failed      -> extraction_quarantine, classified. A model/provider/media
                     failure that never produced a validatable response is still
                     an attempt whose outcome must be recorded — omitting it is
                     the silent drop Constitution V calls the most dangerous
                     failure mode in this system.
    """
    status = analysis.get("status")
    provenance = analysis.get("provenance") or {}
    usage = analysis.get("usage") or {}

    if status == "success":
        await extraction_record_store(
            platform=platform, content_id=content_id, content_type=content_type,
            analysis=analysis, provenance=provenance, usage=usage,
            content_hash=content_hash, purpose="production",
            account_id=account_id, run_id=run_id,
        )
        return

    # `failure_kind` comes from roach where it classified the failure; the
    # fallback is only for a pre-feature roach that does not send one, and
    # 'provider_error' is the honest reading of "the call failed and we were not
    # told how" — NOT a generic bucket for anything unclassified.
    failure_kind = analysis.get("failure_kind") or "provider_error"
    await extraction_quarantine_record(
        platform=platform, content_id=content_id, failure_kind=failure_kind,
        analysis=analysis, provenance=provenance, usage=usage,
        purpose="production", run_id=run_id,
    )


async def _record_capture_outcomes(
    platform: str, content_id: str, content_type: str, counts: Dict[str, Any],
    account_id: Optional[str], run_id: Optional[str],
) -> None:
    """Record what each supplementary pass produced for one item (FR-003).

    The distinction that carries the feature (FR-003b):

      success   the pass ran and returned a value for this item
      no_match  the pass ran FINE and returned nothing for THIS item — the live
                case of a carousel absent from the Reels-keyed clips response.
                Without this, a perfectly normal non-Reel post is indistinguishable
                from an enrichment error, which is the ambiguity feature 005 exists
                to remove.

    An outright pass failure is recorded by the caller that owns the pass, with a
    classified reason; it is not inferable from the item payload here.
    """
    kind = _capture_kind_for(platform, content_type)
    if kind is None:
        return
    # Evidence the responsible pass produced something for this item. For video
    # that is views/comments from the clips grid; for non-video it is the comment
    # count the feed pass supplies (non-video has no views by platform limit, so
    # requiring one would mark every carousel `no_match` forever).
    #
    # KNOWN LIMITATION: this INFERS the outcome from which counts came back, so
    # it can only ever produce `success` or `no_match`. A pass that failed
    # outright — expired cookie, 429, parse error — returns {} from roach and is
    # recorded here as `no_match`, i.e. "ran fine, this item wasn't in it".
    # Only roach can tell those apart; it distinguishes them internally and then
    # flattens them to {}. Closing this needs roach to report a per-pass verdict
    # alongside `request_stats`, which is a roach-side change beyond this
    # feature's scope. Until then `failed`/`not_attempted` are unreachable on the
    # item path (the account-level follower capture does emit them).
    keys = ("views", "comments") if content_type == "video" else ("comments",)
    produced = any(counts.get(k) is not None for k in keys)
    await social_capture_record_outcome(
        platform=platform,
        content_id=content_id,
        capture_kind=kind,
        outcome="success" if produced else "no_match",
        account_id=account_id,
        run_id=run_id,
    )


async def _observe_listed_items(
    platform: str,
    username: str,
    all_items: List[Dict[str, Any]],
    account_id: Optional[str],
    run_id: Optional[str],
    summary: Dict[str, Any],
    logger_,
) -> None:
    """
    Record one metric observation for EVERY item the listing returned (feature 006).

    This is where engagement stops being frozen at first sighting. The counts are
    already in `all_items` — they arrived in a response the harvest was making
    anyway — and until now they were discarded for any item the de-duplication
    gate skipped. Marginal platform requests: ZERO (FR-003, FR-026).

    ## Why `all_items` and not the depth-selected `items`

    The depth selector is a CLIENT-SIDE filter applied after the response
    arrives. The monthly deployments use `days: 31`, so iterating the selected
    subset would observe only content published in the last month — and an item
    needs at least two observations to yield any velocity at all, three for an
    acceleration verdict. Restricting to the window would therefore discard
    exactly the counts this feature exists to capture (FR-004), leaving the
    feature technically working and practically useless.

    ## What it deliberately does not do

    No download, no analysis, no model call (FR-001, FR-025). It does not touch
    the de-duplication gate, which keeps skipping already-successful items
    exactly as before (FR-002).

    Best-effort throughout: a harvest that delivered content must never be failed
    by an observation write, matching how `social.signal.record` is treated.
    """
    for item in all_items:
        content_id = item.get("content_id")
        if not content_id:
            continue
        counts = item.get("public_counts") or {}
        # Extracted once and splatted into both writes below. Spelling the four
        # names out twice in one function means a fifth metric needs two edits
        # thirty lines apart, and a typo in either is invisible because both
        # callees accept the same keywords.
        metrics = {name: counts.get(name) for name in OBSERVED_METRICS}
        try:
            observation_id = await social_observation_record(
                platform=platform,
                content_id=content_id,
                account_id=account_id,
                run_id=run_id,
                **metrics,
            )
            if observation_id is None:
                # The listing carried no usable counts for this item. Recorded as
                # `no_match` — the pass ran fine, this item just had nothing in
                # it — never as a failure, and never as an empty observation row.
                summary["observations_skipped_no_counts"] += 1
                await social_capture_record_outcome(
                    platform=platform, content_id=content_id,
                    capture_kind="metric_refresh", outcome="no_match",
                    account_id=account_id, run_id=run_id,
                )
                continue

            summary["observations_recorded"] += 1
            await social_capture_record_outcome(
                platform=platform, content_id=content_id,
                capture_kind="metric_refresh", outcome="success",
                account_id=account_id, run_id=run_id,
            )
            # Keep the latest-value convenience current (FR-010). Metric columns
            # ONLY — never the analysis or reviewer columns (FR-010a).
            await social_signal_record_metrics(
                platform=platform,
                content_id=content_id,
                **metrics,
            )
        except Exception as e:
            summary["observations_failed"] += 1
            logger_.warning(f"{username}/{content_id}: observation write failed (non-fatal): {e}")


async def _record_listing_failure(
    profile_url: str, platform: str, reason: str,
    run_id: Optional[str], logger_,
) -> None:
    """
    Record that a whole profile's metric refresh could not be attempted (FR-020).

    Without this, a run blocked by a throttle is indistinguishable from one
    where nothing had changed: both leave the same silence. The item-level
    absence classification cannot run either (there is no listing to compare
    against), so the failure is recorded once per profile, keyed to a synthetic
    `acct:` content id — the same convention the follower capture already uses.

    `failed` rather than `not_attempted`: the attempt was made and it broke.
    """
    handle = _resolve_handle(profile_url, {})
    try:
        await social_capture_record_outcome(
            platform=platform, content_id=f"acct:{handle}",
            capture_kind="metric_refresh", outcome="failed",
            reason=reason, run_id=run_id,
        )
    except Exception as e:
        logger_.warning(f"{handle}: listing-failure outcome write failed (non-fatal): {e}")


async def _classify_absent_items(
    platform: str,
    username: str,
    all_items: List[Dict[str, Any]],
    account_id: Optional[str],
    run_id: Optional[str],
    summary: Dict[str, Any],
    logger_,
) -> None:
    """
    Record why each previously-known item was NOT in this listing (FR-018/FR-019).

    An item we have observed before and did not see this run has stopped
    accruing history, and the reason matters: a series that ends because the
    item slid past the listing's reach is a fact about how far we can see, while
    one that ends because the item vanished is a fact about the item. Recording
    both as a bare absence would leave 252 measured rows (research R2) looking
    like a collection regression.

    ## The discriminator

    `T` = `published_at` of the OLDEST item the listing actually returned.

      * published before `T`  -> provably beyond reach -> `aged_out_of_listing`
      * published after `T`   -> should have been returned and was not
                                 -> `absent_within_reach`

    Using the oldest *returned* item, rather than a rank against `list_depth`,
    is what keeps this correct when an account has fewer than 30 posts or the
    listing returns a short page.

    ## What it deliberately will not claim

    `absent_within_reach` is NOT recorded as `deleted`. It is consistent with
    removal, but a short page or a listing hiccup produces exactly the same
    observation, and asserting deletion from an absence would state something
    never observed (Principle VI). A definitive signal exists — roach's
    not-found on the download path — and only that justifies `deleted`.
    """
    listed_ids = {i.get("content_id") for i in all_items if i.get("content_id")}
    published = [ts for i in all_items if (ts := _published_ts(i)) is not None]
    if not listed_ids or not published:
        # Nothing came back at all. That is a listing failure, not an item-level
        # absence, and inferring per-item reasons from it would attribute a
        # profile-wide problem to every item individually.
        return

    oldest_returned = min(published)

    pool_rows = await social_known_items(platform, username)
    for row in pool_rows:
        content_id = row["content_id"]
        if content_id in listed_ids:
            continue
        published_at = row["published_at"]
        if published_at is not None and published_at.timestamp() < oldest_returned:
            reason = "aged_out_of_listing"
            summary["items_aged_out"] += 1
        else:
            # Also covers published_at IS NULL: without a publication time the
            # item cannot be placed relative to the boundary, so the weaker,
            # honest classification is the correct one.
            reason = "absent_within_reach"
            summary["items_absent_within_reach"] += 1
        try:
            await social_capture_record_outcome(
                platform=platform, content_id=content_id,
                capture_kind="metric_refresh", outcome="not_attempted",
                reason=reason, account_id=account_id, run_id=run_id,
            )
        except Exception as e:
            logger_.warning(f"{username}/{content_id}: absence-outcome write failed (non-fatal): {e}")

    if summary["items_aged_out"]:
        logger_.info(
            f"{username}: {summary['items_aged_out']} previously-known item(s) are now beyond "
            "listing reach and will not gain further observations "
            "(extending listing depth is out of scope — see spec 006)"
        )


def make_recent_n_selector(n: int) -> DepthSelector:
    """Keep the newest N items by published_at, reverse-chronological (FR-016a)."""

    def _select(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        ordered = sorted(items, key=lambda i: i.get("published_at") or "", reverse=True)
        return ordered[:n]

    return _select


def make_all_selector() -> DepthSelector:
    """Keep every listed item, newest first. Used by the stories-only flow —
    active Stories are already a small, inherently-recent set (they expire ~24h),
    so there is no depth bound to apply."""

    def _select(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return sorted(items, key=lambda i: i.get("published_at") or "", reverse=True)

    return _select


def _published_ts(item: Dict[str, Any]) -> float | None:
    """Parse an item's published_at (ISO-8601 'Z') to a UTC epoch, or None."""
    published_at = item.get("published_at")
    if not published_at:
        return None
    try:
        return datetime.strptime(published_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


def _date_to_ts(date_str: str, end_of_day: bool) -> float:
    """Parse a 'YYYY-MM-DD' date (UTC) to an epoch; end_of_day pins it to 23:59:59."""
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    if end_of_day:
        dt = dt.replace(hour=23, minute=59, second=59)
    return dt.timestamp()


def make_time_window_selector(days: int) -> DepthSelector:
    """Keep items published within the last `days` days, relative to now (FR-016a).

    Relative window — right for recurring schedules (each run covers the most
    recent `days`).
    """

    def _select(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        cutoff = datetime.now(timezone.utc).timestamp() - days * 86400
        selected = [i for i in items if (ts := _published_ts(i)) is not None and ts >= cutoff]
        return sorted(selected, key=lambda i: i.get("published_at") or "", reverse=True)

    return _select


def make_date_range_selector(start_date: Optional[str], end_date: Optional[str]) -> DepthSelector:
    """Keep items published within [start_date, end_date] inclusive (FR-016a).

    Absolute window — right for manual backfills (e.g. "everything in June").
    Dates are 'YYYY-MM-DD' (UTC); either bound may be None for an open end.
    end_date is inclusive to the end of that day.
    """
    start_ts = _date_to_ts(start_date, end_of_day=False) if start_date else None
    end_ts = _date_to_ts(end_date, end_of_day=True) if end_date else None

    def _select(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        selected = []
        for item in items:
            ts = _published_ts(item)
            if ts is None:
                continue
            if start_ts is not None and ts < start_ts:
                continue
            if end_ts is not None and ts > end_ts:
                continue
            selected.append(item)
        return sorted(selected, key=lambda i: i.get("published_at") or "", reverse=True)

    return _select


def _resolve_platform(profile_url: str) -> str:
    host = urlparse(profile_url).netloc.lower()
    if "instagram.com" in host:
        return "instagram"
    if "tiktok.com" in host:
        return "tiktok"
    raise ValueError(f"Unsupported profile URL (not Instagram/TikTok): {profile_url}")


def _resolve_handle(profile_url: str, profile_meta: Dict[str, Any]) -> str:
    handle = profile_meta.get("handle")
    if handle:
        return str(handle)
    return urlparse(profile_url).path.strip("/").split("/")[-1] or profile_url


async def run_harvest(
    profiles: List[str],
    depth_selector: DepthSelector,
    harvest_name: Optional[str] = None,
    list_depth: int = 30,
    stories_only: bool = False,
    credentials_block_name: str = "google-creds",
) -> Dict[str, Any]:
    """
    Run one harvest batch across up to five public profiles.

    Delivery layout under HARVEST_DRIVE_PARENT_ID:
      {Platform}/{username}/{Format}/...media...            (folders auto-created)
      {Platform}/{username}/"{username} - Social Harvest"    (per-account workbook,
                                                              one tab per quarter)
      "Social Harvest Detail"/"{harvest_name} - {date}"      (per-run detail sheet)

    De-duplication is per account (platform+username+content_id): a prior
    `success` is skipped; a prior `failed` is purged (its media, sheet rows, and
    ledger record) and re-harvested (FR-021 + failed-retry).

    Args:
        profiles: Public Instagram/TikTok profile URLs (truncated to 5, FR-002)
        depth_selector: Function bounding items per profile (recent-N / window / date-range)
        harvest_name: Human name for this run (used in the detail sheet filename + rows)
        list_depth: How deep to list non-video (gallery-dl) items per profile;
            raise it so date-range backfills can reach older content
        credentials_block_name: Name of the Google credentials block

    Returns:
        Dict with start_time, end_time, data, summary, and optional error.
        Never raises (constitution I).
    """
    run_logger = get_run_logger()
    harvest_name = _resolve_harvest_name(harvest_name)
    start_time = datetime.now(timezone.utc)
    harvest_date = start_time.strftime("%Y-%m-%d")
    quarter_tab = _quarter_tab_name(start_time)
    data: List[Dict[str, Any]] = []
    summary = {
        "profiles_processed": 0,
        "items_collected": 0,
        "items_skipped_dedup": 0,
        "items_retried_failed": 0,
        "items_failed": 0,
        "profiles_blocked": 0,
        # Relational Spine (feature 004): classified per contracts/account-resolution.md.
        # Kept distinct from each other and from an ordinary "nothing new" zero —
        # nothing gates a harvest on roster-sync (FR-022a), so this pair is the
        # only place a stale/missing registration surfaces (FR-016c, FR-023a).
        "items_skipped_unregistered": 0,
        "items_skipped_inactive": 0,
        # Follower capture (FR-013): the Principle VII deviation's mitigation —
        # a miss is recorded HERE, not as a placeholder observation row, since
        # nothing gates the run on whether a count came back (FR-013b).
        "follower_capture_missed": [],
        # Longitudinal Metric Capture (feature 006). Counted separately from
        # items_collected: an observation is not a collection. These are the
        # numbers that show engagement is no longer frozen at first sighting,
        # and they should be non-zero on a run where items_collected is zero.
        "observations_recorded": 0,
        "observations_skipped_no_counts": 0,
        "observations_failed": 0,
        "items_aged_out": 0,
        "items_absent_within_reach": 0,
        "account_folder_ids": {},
        "detail_sheet_id": None,
    }
    error: Optional[str] = None
    parent_id = os.environ["HARVEST_DRIVE_PARENT_ID"]
    rate_window = RateWindow(limit=100, window_seconds=3600.0)
    staged_files: List[str] = []
    detail_row_id = 1

    if len(profiles) > MAX_PROFILES:
        run_logger.warning(f"Run specified {len(profiles)} profiles, truncating to {MAX_PROFILES} (FR-002)")
        profiles = profiles[:MAX_PROFILES]

    # Relational Spine (feature 004): a durable run identifier, best-effort —
    # a run-record failure must never abort a harvest that would otherwise
    # succeed (constitution V). client_id is left null: one run can span
    # several profiles belonging to different (or no) clients.
    run_record_id: Optional[str] = None
    try:
        run_record_id = await run_record_start(kind="collection", flow_name="social-harvest")
    except Exception as e:
        run_logger.warning(f"run-record start failed (non-fatal): {e}")

    try:
        # Per-run detail sheet (new file per run) under the "Social Harvest Detail" folder.
        detail_folder_id = await drive_folder_ensure(DETAIL_FOLDER_NAME, parent_id, credentials_block_name=credentials_block_name)
        detail_title = f"{harvest_name} - {harvest_date}"
        detail_sheet_id = await sheets_create(detail_title, detail_folder_id, header_row=DETAIL_HEADER, credentials_block_name=credentials_block_name)
        summary["detail_sheet_id"] = detail_sheet_id
        run_logger.info(f"Created detail sheet '{detail_title}' -> {detail_sheet_id}")

        for profile_url in profiles:
            try:
                platform = _resolve_platform(profile_url)
            except ValueError as e:
                run_logger.warning(str(e))
                continue

            run_logger.info(f"Starting profile {profile_url} ({platform})")

            try:
                listing = await social_profile_list(profile_url, platform, list_depth, stories_only=stories_only)
            except RoachNotFoundError as e:
                run_logger.warning(f"Profile skipped (private/non-existent): {profile_url} — {e}")
                await _record_listing_failure(profile_url, platform, "not_found", run_record_id, run_logger)
                continue
            except RoachRateLimitedError as e:
                run_logger.warning(f"Profile listing rate-limited/challenged: {profile_url} — {e}; deferring profile")
                summary["profiles_blocked"] += 1
                await _record_listing_failure(profile_url, platform, "blocked", run_record_id, run_logger)
                continue

            profile_meta = listing["profile"]
            username = _resolve_handle(profile_url, profile_meta)
            # FR-024: record what this profile's listing actually cost, so the
            # baseline is measured rather than estimated — and so any future
            # change's marginal volume can be stated against a real number.
            # Read off a run that was happening anyway; costs no extra requests.
            stats = listing.get("request_stats")
            if stats:
                summary.setdefault("request_stats", {})[username] = stats
                run_logger.info(f"{username}: listing passes {stats}")

            all_items = listing.get("items", [])
            items = depth_selector(all_items)
            run_logger.info(f"{username}: {len(all_items)} items available, {len(items)} selected for this run")

            # Relational Spine (feature 004): resolve the account ONCE per profile
            # (the outcome is the same for every item in it) before any per-item
            # download/analyze work, so an unregistered or deactivated handle
            # never spends a download on content that will be discarded anyway.
            # The write path never creates an account (FR-016b) — it only skips,
            # classified, and continues to the next profile (FR-016a/c).
            resolution = await social_account_resolve(platform, username)
            if resolution["outcome"] != "resolved":
                skip_key = "items_skipped_unregistered" if resolution["outcome"] == "unregistered" else "items_skipped_inactive"
                summary[skip_key] += len(items)
                run_logger.warning(
                    f"{username}: account {resolution['outcome']} on {platform} — skipping "
                    f"{len(items)} item(s) ("
                    + ("add the handle to the Clients/Hashmaps sheet and run roster-sync"
                       if resolution["outcome"] == "unregistered"
                       else "remove the corresponding harvest-monthly-* deployment")
                    + ")"
                )
                summary["profiles_processed"] += 1
                continue
            account_id = resolution["account_id"]

            # Longitudinal Metric Capture (feature 006): observe EVERY listed
            # item's current counts before the per-item loop decides what to
            # download. Positioned here deliberately —
            #   * AFTER account resolution, so account_id is known;
            #   * BEFORE the `if not items` guard below, so a profile whose
            #     items all fall outside this run's window is still observed
            #     (that is the common case for a days:31 deployment listing an
            #     account's most recent ~30 posts);
            #   * BEFORE the download loop, so it runs for items the
            #     de-duplication gate will skip — which is the entire point.
            await _observe_listed_items(
                platform=platform, username=username, all_items=all_items,
                account_id=account_id, run_id=run_record_id,
                summary=summary, logger_=run_logger,
            )
            # And say why each previously-known item that did NOT come back has
            # stopped accruing history (FR-018/FR-019). Best-effort: a
            # bookkeeping write must never fail a harvest that delivered.
            try:
                await _classify_absent_items(
                    platform=platform, username=username, all_items=all_items,
                    account_id=account_id, run_id=run_record_id,
                    summary=summary, logger_=run_logger,
                )
            except Exception as e:
                run_logger.warning(f"{username}: absence classification failed (non-fatal): {e}")

            # Follower capture (FR-013a/b): best-effort, never able to fail the
            # run. `public_metadata` is per-profile (not per-item), so this is
            # one call per profile. TikTok returns a count today; Instagram
            # does not (research R3) — that is the expected, correct state,
            # not a bug, and is why a miss is recorded rather than treated as
            # an error.
            #
            # Feature 005 (FR-018): the outcome is ALSO written to
            # capture_outcomes. Until now a miss existed only in this run's
            # summary, so "does this account have no observation for August, or
            # was it never attempted?" could not be answered by query — only by
            # reading run logs. The observation table itself stays observations-
            # only (constitution VII): a miss never becomes a zero row there.
            follower_outcome, follower_reason = "success", None
            try:
                follower_count = (profile_meta.get("public_metadata") or {}).get("follower_count")
                wrote = await social_account_record_followers(account_id=account_id, follower_count=follower_count, run_id=run_record_id)
                if not wrote:
                    follower_outcome, follower_reason = "no_match", None
                    summary["follower_capture_missed"].append(
                        {"platform": platform, "handle": username, "reason": "not_returned"}
                    )
            except Exception as e:
                run_logger.warning(f"{username}: follower capture failed (non-fatal): {e}")
                follower_outcome, follower_reason = "failed", "unexpected_structure"
                summary["follower_capture_missed"].append(
                    {"platform": platform, "handle": username, "reason": "error"}
                )
            if platform == "instagram":
                try:
                    await social_capture_record_outcome(
                        platform=platform,
                        content_id=f"acct:{username}",
                        capture_kind="instagram_profile_info",
                        outcome=follower_outcome,
                        reason=follower_reason,
                        account_id=account_id,
                        run_id=run_record_id,
                    )
                except Exception as e:
                    run_logger.warning(f"{username}: follower capture-outcome write failed (non-fatal): {e}")

            # Folder hierarchy: {Platform}/{username}
            platform_folder_id = await drive_folder_ensure(_platform_display(platform), parent_id, credentials_block_name=credentials_block_name)
            account_folder_id = await drive_folder_ensure(username, platform_folder_id, credentials_block_name=credentials_block_name)
            summary["account_folder_ids"][username] = account_folder_id
            drive_target = account_folder_id

            # Per-account workbook + current-quarter tab.
            account_sheet_id = await sheets_spreadsheet_ensure(f"{username} - Social Harvest", account_folder_id, credentials_block_name=credentials_block_name)
            await sheets_tab_ensure(account_sheet_id, quarter_tab, ACCOUNT_HEADER, credentials_block_name=credentials_block_name)
            account_row_id = await sheets_tab_row_count(account_sheet_id, quarter_tab, credentials_block_name=credentials_block_name) + 1
            format_folder_cache: Dict[str, str] = {}

            if not items:
                run_logger.info(f"{username}: zero content collected; empty folder created")
                summary["profiles_processed"] += 1
                continue

            profile_blocked = False
            for idx, item in enumerate(items):
                content_id = item["content_id"]
                content_type = item["content_type"]

                record = await social_dedup_check(platform, content_id, drive_target)
                if record and record.get("analysis_status") == "success":
                    run_logger.info(f"{username}/{content_id}: already harvested (success), skipping (FR-021)")
                    summary["items_skipped_dedup"] += 1
                    continue
                if record:
                    # Prior FAILED (or pending) harvest — purge it, then re-harvest.
                    run_logger.info(f"{username}/{content_id}: prior harvest was '{record.get('analysis_status')}', purging and re-harvesting")
                    for old_file_id in (record.get("drive_file_id") or "").split(","):
                        if old_file_id.strip():
                            try:
                                await drive_file_delete(old_file_id.strip(), credentials_block_name=credentials_block_name)
                            except Exception as e:
                                run_logger.warning(f"Could not delete old file {old_file_id}: {e}")
                    try:
                        await sheets_rows_delete_by_content_id(account_sheet_id, content_id, CONTENT_ID_COL, credentials_block_name=credentials_block_name)
                    except Exception as e:
                        run_logger.warning(f"Could not delete old sheet row for {content_id}: {e}")
                    await social_dedup_delete(platform, content_id, drive_target)
                    summary["items_retried_failed"] += 1

                await rate_window.wait_for_slot()

                local_paths: Optional[List[str]] = None
                for attempt in range(2):
                    try:
                        download = await social_item_download(
                            content_id=content_id,
                            source_url=item["source_url"],
                            is_video=item["is_video"],
                            content_type=content_type,
                        )
                        local_paths = download["local_paths"]
                        # Authoritative content_type from the downloaded files —
                        # corrects the listing's guess (e.g. a Reel listed as
                        # "image"/"carousel" resolves to "video"). Drives the
                        # format folder, analysis model, and the sheet row.
                        content_type = download.get("content_type", content_type)
                        item["content_type"] = content_type
                        break
                    except RoachNotFoundError as e:
                        run_logger.warning(f"{username}/{content_id}: content gone, skipping — {e}")
                        break
                    except RoachRateLimitedError as e:
                        backoff = BACKOFF_STEPS_SECONDS[min(attempt, len(BACKOFF_STEPS_SECONDS) - 1)]
                        run_logger.warning(
                            f"{username}: rate-limit/challenge on {content_id} ({e}); "
                            f"backing off {backoff}s (attempt {attempt + 1})"
                        )
                        await asyncio.sleep(backoff)
                        if attempt == 1:
                            run_logger.warning(f"{username}: still blocked after back-off; deferring remaining items")
                            profile_blocked = True

                if profile_blocked:
                    summary["profiles_blocked"] += 1
                    break
                if local_paths is None:
                    summary["items_failed"] += 1
                    is_last = idx == len(items) - 1
                    await randomized_item_delay(is_last)
                    continue

                staged_files.extend(local_paths)
                rate_window.record()

                # Feature 007 (FR-039): hash the media ACTUALLY SENT to the model,
                # while the files still exist. They are unlinked immediately after
                # the Drive upload below, so this cannot be deferred to the write.
                content_hash = _media_hash(local_paths)

                # Feature 007 (FR-037a): the monthly extraction ceiling is a HARD
                # STOP, and it is checked before ANY costed run — which includes
                # this one. The forward harvest is the *continuous* spender; a
                # ceiling covering only the batch operations would leave the
                # largest recurring spend ungated, and Constitution XI requires it
                # be enforced rather than merely reported.
                #
                # Reaching it skips EXTRACTION ONLY. Media still uploads, metrics
                # are still recorded, the sheet row is still written. Halting
                # collection to save extraction spend would trade an irreplaceable
                # observation for a replaceable one — the media is gone in 24h for
                # a story and the counts are only observable now, whereas the
                # extraction can be backfilled next month for a fraction of a cent.
                if await _extraction_ceiling_reached(run_logger, username, content_id):
                    analysis = {
                        "status": "failed", "error": "monthly extraction spend ceiling reached",
                        "failure_kind": "spend_ceiling_reached",
                        "subtitle": "", "flow": "", "summary": "",
                        "beats": [], "attributes": [], "usage": None, "provenance": {},
                    }
                    summary["items_extraction_deferred"] = summary.get("items_extraction_deferred", 0) + 1
                else:
                    try:
                        analysis = await social_item_analyze(content_id, local_paths, content_type, client=harvest_name)
                    except Exception as e:
                        # A failed analyze (timeout, provider error, …) must never abort the
                        # run — record the item as failed so it's retained + retried next run.
                        analysis = {"subtitle": "", "flow": "", "summary": "", "status": "failed",
                                    "error": str(e), "failure_kind": "provider_error"}
                if analysis.get("status") == "failed":
                    run_logger.warning(f"{username}/{content_id}: analysis failed — {analysis.get('error')}")
                elif analysis.get("status") == "quarantined":
                    # Feature 007: the model produced output that failed validation
                    # TWICE. Nothing is stored as an extraction — but the item is
                    # still delivered below, exactly as a failed analysis always
                    # has been (FR-022). Extraction failure never reduces
                    # collection: the media is irreplaceable, the extraction is not.
                    run_logger.warning(
                        f"{username}/{content_id}: extraction quarantined "
                        f"({analysis.get('failure_kind')}) — media and metrics still delivered")

                # Upload media into the content-format subfolder.
                format_name = _format_folder(content_type)
                if format_name not in format_folder_cache:
                    format_folder_cache[format_name] = await drive_folder_ensure(format_name, account_folder_id, credentials_block_name=credentials_block_name)
                format_folder_id = format_folder_cache[format_name]

                drive_file_ids = []
                for local_path in local_paths:
                    try:
                        file_id = await drive_file_upload(local_path, format_folder_id, credentials_block_name=credentials_block_name)
                        drive_file_ids.append(file_id)
                    finally:
                        try:
                            Path(local_path).unlink(missing_ok=True)
                            if local_path in staged_files:
                                staged_files.remove(local_path)
                        except OSError:
                            pass

                core = _row_core(username, platform, item, analysis, drive_file_ids, harvest_name, harvest_date)
                await sheets_rows_append(account_sheet_id, [[account_row_id] + core], sheet_name=quarter_tab, credentials_block_name=credentials_block_name)
                account_row_id += 1
                await sheets_rows_append(detail_sheet_id, [[detail_row_id] + core + [account_folder_id]], credentials_block_name=credentials_block_name)
                detail_row_id += 1

                await social_dedup_record(
                    platform=platform,
                    profile_key=username,
                    content_id=content_id,
                    drive_target=drive_target,
                    content_type=content_type,
                    drive_file_id=",".join(drive_file_ids) or None,
                    analysis_status=analysis.get("status", "pending"),
                    account_id=account_id,
                    run_id=run_record_id,
                )

                # Mirror engagement + analysis into the songbird "what hits"
                # signal store (feature 003). Best-effort: a signal-write failure
                # must never abort a harvest that already delivered the item.
                counts = item.get("public_counts") or {}
                try:
                    await social_signal_record(
                        platform=platform,
                        profile_key=username,
                        content_id=content_id,
                        content_type=content_type,
                        published_at=item.get("published_at") or None,
                        caption=item.get("caption", ""),
                        hashtags=" ".join(item.get("hashtags") or []),
                        views=counts.get("views"),
                        likes=counts.get("likes"),
                        comments=counts.get("comments"),
                        # Feature 005. roach has always parsed this on both
                        # TikTok paths; it was discarded at this boundary.
                        shares=counts.get("shares"),
                        subtitle=_cell(analysis.get("subtitle", "")),
                        content_flow=_cell(analysis.get("flow", "")),
                        summary=_cell(analysis.get("summary", "")),
                        account_id=account_id,
                        run_id=run_record_id,
                    )
                except Exception as e:
                    run_logger.warning(f"{username}/{content_id}: signal-store write failed (non-fatal): {e}")

                # Feature 007: store the STRUCTURED extraction alongside today's
                # prose write. The forward path writes BOTH; backfill writes only
                # the new tables (research.md R10) — re-extraction produces a new
                # subtitle, and harvested_signals holds exactly one row per item,
                # so writing one back there would overwrite an irreplaceable
                # transcript.
                #
                # Best-effort, for the same reason as the signal write above: this
                # item's media has already reached Drive and its metrics are
                # already recorded. Failing the harvest now would trade a
                # collected observation for an extraction that can be retried.
                try:
                    await _record_structured_extraction(
                        platform=platform, content_id=content_id, content_type=content_type,
                        analysis=analysis, content_hash=content_hash,
                        account_id=account_id, run_id=run_record_id,
                    )
                except Exception as e:
                    run_logger.warning(
                        f"{username}/{content_id}: structured-extraction write failed (non-fatal): {e}")

                # Capture provenance (feature 005, FR-003): record whether each
                # supplementary pass actually produced a value for THIS item, so
                # an empty count is resolvable to exactly one cause. Best-effort
                # for the same reason as the signal write — and a missing outcome
                # degrades to "provenance unknown", which is the safe reading.
                try:
                    await _record_capture_outcomes(
                        platform=platform, content_id=content_id, content_type=content_type,
                        counts=counts, account_id=account_id, run_id=run_record_id,
                    )
                except Exception as e:
                    run_logger.warning(f"{username}/{content_id}: capture-outcome write failed (non-fatal): {e}")

                data.append({"username": username, "content_id": content_id, "drive_file_ids": drive_file_ids, "analysis_status": analysis.get("status")})
                summary["items_collected"] += 1
                run_logger.info(
                    f"{username}/{content_id}: delivered ({analysis.get('status')}) to {format_name}. "
                    f"Running total: {summary['items_collected']} items"
                )

                is_last = idx == len(items) - 1
                delay = await randomized_item_delay(is_last)
                if delay:
                    run_logger.info(f"Waited {delay:.1f}s before next item")

            summary["profiles_processed"] += 1

    except Exception as e:
        run_logger.error(f"Harvest run failed: {e}")
        error = str(e)
    finally:
        for local_path in staged_files:
            try:
                Path(local_path).unlink(missing_ok=True)
            except OSError:
                pass
        end_time = datetime.now(timezone.utc)

    # Problems (error, blocked profiles, failed items) are reported to Slack by
    # the flow's alert hook (flows/common/alerts.py), which reads this return value.

    if run_record_id:
        try:
            await run_record_finish(run_id=run_record_id, status="failed" if error else "completed", summary=summary)
        except Exception as e:
            run_logger.warning(f"run-record finish failed (non-fatal): {e}")

    return {
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
        "data": data,
        "summary": summary,
        "error": error,
    }
