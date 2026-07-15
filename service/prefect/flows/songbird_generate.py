"""
Songbird — On-Demand Content Generation Flow

Manually generates N standalone content ideas for a client between monthly cycles
(a reactive post, an event, a campaign moment). Same grounding + hit-bias as the
monthly plan, but the output is standalone and draft-only: no calendar dates are
auto-assigned and nothing is written to the live content-plan worksheet
(feature 003-songbird-content-generation).

Usage (inside the container):
    docker exec prefect python flows/songbird_generate.py --client "Ecky Dental Center" --quantity 3 --platform instagram
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
    from .common.songbird import run_generation
except ImportError:
    sys.path.append(os.path.dirname(__file__))
    from common.songbird import run_generation

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "data")


@flow(name="songbird-generate", description="Generate standalone on-demand content ideas")
async def songbird_generate_flow(
    client: str,
    quantity: int = 3,
    platform: str = "",
    audience: str = "",
    goal: str = "",
    tone: str = "",
    content_pillars: Optional[List[str]] = None,
    signal_window_days: int = 180,
    credentials_block_name: str = "google-creds",
):
    """
    Generate `quantity` standalone content ideas on demand (draft-only, no dates).

    Args:
        client: Client name (grounds KB lookup, handle resolution).
        quantity: Number of standalone ideas to generate.
        platform, audience, goal, tone, content_pillars: marketing parameters.
        signal_window_days: Rolling recency window for top performers (default 180).
        credentials_block_name: Google credentials block name.

    Returns:
        Dict with start_time, end_time, data, summary, and optional error.
    """
    return await run_generation(
        client=client,
        quantity=quantity,
        distribute_dates=False,
        resolve_quantity_from_config=False,
        target="draft",
        platform=platform,
        audience=audience,
        goal=goal,
        tone=tone,
        content_pillars=content_pillars,
        signal_window_days=signal_window_days,
        credentials_block_name=credentials_block_name,
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client", required=True, help="Client name")
    parser.add_argument("--quantity", type=int, default=3, help="Number of ideas (default 3)")
    parser.add_argument("--platform", default="")
    parser.add_argument("--audience", default="")
    parser.add_argument("--goal", default="")
    parser.add_argument("--tone", default="")
    parser.add_argument("--content-pillars", nargs="*", default=None)
    parser.add_argument("--signal-window-days", type=int, default=180)
    parser.add_argument("--credentials-block-name", default="google-creds")
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    result = asyncio.run(
        songbird_generate_flow(
            client=args.client, quantity=args.quantity,
            platform=args.platform, audience=args.audience, goal=args.goal, tone=args.tone,
            content_pillars=args.content_pillars, signal_window_days=args.signal_window_days,
            credentials_block_name=args.credentials_block_name,
        )
    )

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    output_path = os.path.join(OUTPUT_DIR, f"songbird_generate_{timestamp}.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(json.dumps(result["summary"], indent=2, ensure_ascii=False))
    print(f"Full result saved to {output_path}")
    sys.exit(1 if result.get("error") else 0)
