"""
Social Content Harvest — Time-Window Flow

Harvests and analyzes publicly visible content items from up to five public
Instagram/TikTok profiles within a time window, delivering downloaded content
to Google Drive and the analysis to the database (spec 002-social-content-harvest,
FR-016a; the analysis sheets were retired by spec 009).

The window can be expressed two ways:
  - Relative (default, right for the recurring schedule): items published within
    the last N days — `--days 7`.
  - Absolute (right for a manual backfill): items published between two dates,
    inclusive — `--start-date 2026-06-01 --end-date 2026-06-30`.
If any of --start-date/--end-date is given, the absolute window is used and
--days is ignored.

Usage (inside the container):
    docker exec prefect python flows/social_harvest_window.py --profiles "https://www.instagram.com/name/" --days 7
    docker exec prefect python flows/social_harvest_window.py --profiles "https://www.instagram.com/name/" --start-date 2026-06-01 --end-date 2026-06-30
"""
import argparse
import asyncio
import json
import os
import sys
from datetime import datetime
from typing import List, Optional

from prefect import flow

try:
    from .common.social_harvest import make_date_range_selector, make_time_window_selector, run_harvest
except ImportError:
    sys.path.append(os.path.dirname(__file__))
    from common.social_harvest import make_date_range_selector, make_time_window_selector, run_harvest

try:
    from .common.alerts import alert_hooks
except ImportError:
    sys.path.append(os.path.dirname(os.path.abspath(__file__)))
    from common.alerts import alert_hooks

DEFAULT_DAYS = 7
# Non-video listing depth. A time window can reach back weeks/months, and
# Instagram is listed newest-first, so we page deeper than the recent flow to
# reach older posts in the window. Raise it for longer/older backfills.
DEFAULT_LIST_DEPTH = 150
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "data")


def _validate_date(label: str, value: Optional[str]) -> None:
    if value is None:
        return
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        raise ValueError(f"{label} must be in YYYY-MM-DD format, got {value!r}")


@flow(name="social-harvest-window", description="Harvest items within a relative or absolute time window", **alert_hooks())
async def social_harvest_window_flow(
    profiles: List[str],
    days: Optional[int] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    harvest_name: Optional[str] = None,
    list_depth: int = DEFAULT_LIST_DEPTH,
    credentials_block_name: str = "google-creds",
):
    """
    Harvest items within a time window per profile (FR-016a).

    Args:
        profiles: Public Instagram/TikTok profile URLs (max 5, FR-002)
        days: Relative window size in days (used when start/end are not given; default 7)
        start_date: Absolute window start, 'YYYY-MM-DD' inclusive (UTC)
        end_date: Absolute window end, 'YYYY-MM-DD' inclusive (UTC)
        harvest_name: Human name for this run (the analysis spend label);
            defaults to the Prefect flow-run name (e.g. "graceful-rook") when omitted
        list_depth: How deep to list non-video (Instagram) items; raise it for
            older/longer windows so the date filter has older posts to match
        credentials_block_name: Name of the Google credentials block

    Returns:
        Dict with start_time, end_time, data, summary, and optional error.
    """
    _validate_date("start_date", start_date)
    _validate_date("end_date", end_date)
    if start_date and end_date and start_date > end_date:
        raise ValueError(f"start_date ({start_date}) must not be after end_date ({end_date})")

    if start_date or end_date:
        depth_selector = make_date_range_selector(start_date, end_date)
    else:
        window_days = days if days is not None else DEFAULT_DAYS
        depth_selector = make_time_window_selector(window_days)

    return await run_harvest(
        profiles=profiles,
        depth_selector=depth_selector,
        harvest_name=harvest_name,
        list_depth=list_depth,
        credentials_block_name=credentials_block_name,
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--profiles", nargs="+", required=True, help="Profile URLs (space-separated, max 5)")
    parser.add_argument("--days", type=int, default=None, help=f"Relative window in days (default {DEFAULT_DAYS}; ignored if --start-date/--end-date given)")
    parser.add_argument("--start-date", default=None, help="Absolute window start, YYYY-MM-DD (inclusive, UTC)")
    parser.add_argument("--end-date", default=None, help="Absolute window end, YYYY-MM-DD (inclusive, UTC)")
    parser.add_argument("--harvest-name", default=None, help="Human name for this run (defaults to the Prefect flow-run name)")
    parser.add_argument("--list-depth", type=int, default=DEFAULT_LIST_DEPTH, help=f"Instagram listing depth (default {DEFAULT_LIST_DEPTH}; raise for older backfills)")
    parser.add_argument("--credentials-block-name", default="google-creds")
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    result = asyncio.run(
        social_harvest_window_flow(
            profiles=args.profiles,
            days=args.days,
            start_date=args.start_date,
            end_date=args.end_date,
            harvest_name=args.harvest_name,
            list_depth=args.list_depth,
            credentials_block_name=args.credentials_block_name,
        )
    )

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    output_path = os.path.join(OUTPUT_DIR, f"social_harvest_window_{timestamp}.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(json.dumps(result["summary"], indent=2))
    print(f"Full result saved to {output_path}")
    sys.exit(1 if result.get("error") else 0)
