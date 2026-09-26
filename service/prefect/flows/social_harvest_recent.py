"""
Social Content Harvest — Most-Recent-N Flow

Harvests and analyzes the N most recent publicly visible content items from up
to five public Instagram/TikTok profiles, delivering downloaded content to
Google Drive and the analysis to the database (spec 002-social-content-harvest, FR-016a).

Usage (inside the container):
    docker exec prefect python flows/social_harvest_recent.py --profiles "https://www.tiktok.com/@name" --n 10
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
    from .common.social_harvest import make_recent_n_selector, run_harvest
except ImportError:
    sys.path.append(os.path.dirname(__file__))
    from common.social_harvest import make_recent_n_selector, run_harvest

try:
    from .common.alerts import alert_hooks
except ImportError:
    sys.path.append(os.path.dirname(os.path.abspath(__file__)))
    from common.alerts import alert_hooks

DEFAULT_N = 10
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "data")


@flow(name="social-harvest-recent", description="Harvest the N most recent items per profile", **alert_hooks())
async def social_harvest_recent_flow(
    profiles: List[str],
    n: int = DEFAULT_N,
    harvest_name: Optional[str] = None,
    credentials_block_name: str = "google-creds",
):
    """
    Harvest the N most recent items per profile (default N=10, FR-016a).

    Args:
        profiles: Public Instagram/TikTok profile URLs (max 5, FR-002)
        n: Most-recent items to keep per profile
        harvest_name: Human name for this run (the analysis spend label);
            defaults to the Prefect flow-run name (e.g. "graceful-rook") when omitted
        credentials_block_name: Name of the Google credentials block

    Returns:
        Dict with start_time, end_time, data, summary, and optional error.
    """
    return await run_harvest(
        profiles=profiles,
        depth_selector=make_recent_n_selector(n),
        harvest_name=harvest_name,
        list_depth=max(n, 30),
        credentials_block_name=credentials_block_name,
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profiles", nargs="+", required=True, help="Profile URLs (space-separated, max 5)")
    parser.add_argument("--n", type=int, default=DEFAULT_N, help=f"Most-recent items per profile (default {DEFAULT_N})")
    parser.add_argument("--harvest-name", default=None, help="Human name for this run (defaults to the Prefect flow-run name)")
    parser.add_argument("--credentials-block-name", default="google-creds")
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    result = asyncio.run(
        social_harvest_recent_flow(
            profiles=args.profiles, n=args.n,
            harvest_name=args.harvest_name, credentials_block_name=args.credentials_block_name,
        )
    )

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    output_path = os.path.join(OUTPUT_DIR, f"social_harvest_recent_{timestamp}.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(json.dumps(result["summary"], indent=2))
    print(f"Full result saved to {output_path}")
    sys.exit(1 if result.get("error") else 0)
