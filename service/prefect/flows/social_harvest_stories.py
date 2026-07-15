"""
Social Content Harvest — Stories-Only Flow

Harvests and analyzes only the currently-active Instagram/TikTok Stories from up
to five public profiles, delivering the downloaded stories + analysis Sheet rows
to Google Drive (spec 002-social-content-harvest).

Stories are ephemeral (they expire ~24h after posting), so this flow is meant to
be run *frequently* to catch them before they vanish — it uses roach's fast,
single-endpoint Stories listing (no full feed/Reels scrape) and collects every
active story (no most-recent-N / time-window bound). Cross-run de-duplication
means re-running while a story is still live won't re-harvest it.

NOTE: This flow is intentionally left UNSCHEDULED for now — trigger it manually
(Prefect UI "Run" or the deployment run command). Add a schedule later once the
desired check cadence is decided.

Usage (inside the container):
    docker exec prefect python flows/social_harvest_stories.py --profiles "https://www.instagram.com/lasikasyik/"
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
    from .common.social_harvest import make_all_selector, run_harvest
except ImportError:
    sys.path.append(os.path.dirname(__file__))
    from common.social_harvest import make_all_selector, run_harvest

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "data")


@flow(name="social-harvest-stories", description="Harvest only currently-active Stories per profile")
async def social_harvest_stories_flow(
    profiles: List[str],
    harvest_name: Optional[str] = None,
    credentials_block_name: str = "google-creds",
):
    """
    Harvest every currently-active Story per profile (Instagram/TikTok).

    Args:
        profiles: Public Instagram/TikTok profile URLs (max 5, FR-002)
        harvest_name: Human name for this run (detail sheet filename + rows);
            defaults to the Prefect flow-run name when omitted
        credentials_block_name: Name of the Google credentials block

    Returns:
        Dict with start_time, end_time, data, summary, and optional error.
    """
    return await run_harvest(
        profiles=profiles,
        depth_selector=make_all_selector(),
        harvest_name=harvest_name,
        stories_only=True,
        credentials_block_name=credentials_block_name,
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profiles", nargs="+", required=True, help="Profile URLs (space-separated, max 5)")
    parser.add_argument("--harvest-name", default=None, help="Human name for this run (defaults to the Prefect flow-run name)")
    parser.add_argument("--credentials-block-name", default="google-creds")
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    result = asyncio.run(
        social_harvest_stories_flow(
            profiles=args.profiles,
            harvest_name=args.harvest_name,
            credentials_block_name=args.credentials_block_name,
        )
    )

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    output_path = os.path.join(OUTPUT_DIR, f"social_harvest_stories_{timestamp}.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(json.dumps(result["summary"], indent=2))
    print(f"Full result saved to {output_path}")
    sys.exit(1 if result.get("error") else 0)
