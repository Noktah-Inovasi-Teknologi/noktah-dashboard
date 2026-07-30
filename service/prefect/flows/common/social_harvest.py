"""
Shared Social Content Harvest engine

Implements the collection loop, delivery, pacing, hourly cap, back-off, and
de-duplication that both `social-harvest-recent` and `social-harvest-window`
flows share (FR-016a). This module never raises to its caller — it always
returns a Dict[str, Any] with start_time/end_time/data/summary/error
(constitution I), and sets end_time in a finally block (constitution V).
"""
import asyncio
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
        social_dedup_check,
        social_dedup_delete,
        social_dedup_record,
        social_item_analyze,
        social_item_download,
        social_profile_list,
        social_signal_record,
    )
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
except ImportError:
    import sys

    sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
    from tasks.social_tasks import (
        RoachNotFoundError,
        RoachRateLimitedError,
        social_dedup_check,
        social_dedup_delete,
        social_dedup_record,
        social_item_analyze,
        social_item_download,
        social_profile_list,
        social_signal_record,
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
]
# Per-run detail sheet = account columns + the account folder id.
DETAIL_HEADER = ACCOUNT_HEADER + ["account_folder_id"]
# 0-based index of content_id within ACCOUNT_HEADER (for row-deletion on retry).
CONTENT_ID_COL = ACCOUNT_HEADER.index("content_id")

DepthSelector = Callable[[List[Dict[str, Any]]], List[Dict[str, Any]]]


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
    )]


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


async def _send_completion_notification(run_id: str, summary: Dict[str, Any], error: Optional[str]) -> None:
    """Send a completion notification via a configurable Prefect notification block (FR-018)."""
    block_name = os.environ.get("HARVEST_NOTIFICATION_BLOCK")
    if not block_name:
        logger.info("HARVEST_NOTIFICATION_BLOCK not configured; skipping completion notification")
        return
    try:
        from prefect.blocks.notifications import AppriseNotificationBlock

        block = await AppriseNotificationBlock.load(block_name)
        status = "FAILED" if error else "COMPLETED"
        message = (
            f"Social harvest run {run_id} {status}. "
            f"Profiles: {summary.get('profiles_processed')}, "
            f"Items: {summary.get('items_collected')}, "
            f"Failed: {summary.get('items_failed')}, "
            f"Blocked profiles: {summary.get('profiles_blocked')}."
        )
        if error:
            message += f" Error: {error}"
        # When several profiles were blocked in one run, the shared egress IP is
        # likely throttled — prompt the operator to rotate it (toggle the mobile
        # hotspot's data / airplane mode to acquire a fresh carrier IP).
        blocked = summary.get("profiles_blocked") or 0
        threshold = int(os.environ.get("HARVEST_ROTATE_IP_THRESHOLD", "2"))
        if blocked >= threshold:
            message += " ⚠ Egress likely throttled — rotate the hotspot IP (toggle mobile data / airplane mode)."
        await block.notify(message)
    except Exception as e:
        logger.warning(f"Failed to send completion notification via block '{block_name}': {e}")


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
                continue
            except RoachRateLimitedError as e:
                run_logger.warning(f"Profile listing rate-limited/challenged: {profile_url} — {e}; deferring profile")
                summary["profiles_blocked"] += 1
                continue

            profile_meta = listing["profile"]
            username = _resolve_handle(profile_url, profile_meta)
            all_items = listing.get("items", [])
            items = depth_selector(all_items)
            run_logger.info(f"{username}: {len(all_items)} items available, {len(items)} selected for this run")

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

                try:
                    analysis = await social_item_analyze(content_id, local_paths, content_type, client=harvest_name)
                except Exception as e:
                    # A failed analyze (timeout, provider error, …) must never abort the
                    # run — record the item as failed so it's retained + retried next run.
                    analysis = {"subtitle": "", "flow": "", "summary": "", "status": "failed", "error": str(e)}
                if analysis.get("status") == "failed":
                    run_logger.warning(f"{username}/{content_id}: analysis failed — {analysis.get('error')}")

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
                        subtitle=_cell(analysis.get("subtitle", "")),
                        content_flow=_cell(analysis.get("flow", "")),
                        summary=_cell(analysis.get("summary", "")),
                    )
                except Exception as e:
                    run_logger.warning(f"{username}/{content_id}: signal-store write failed (non-fatal): {e}")

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

    run_id = f"{harvest_name}-{start_time.strftime('%Y%m%dT%H%M%S')}"
    await _send_completion_notification(run_id, summary, error)

    return {
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
        "data": data,
        "summary": summary,
        "error": error,
    }
