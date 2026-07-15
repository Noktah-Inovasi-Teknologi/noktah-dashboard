"""
Songbird — Monthly Content Plan Flow

Generates a client's monthly content plan: a per-client-configured number of
content ideas grounded in the client's knowledge base and biased toward hits
learned from own + competitor harvested content, with publish dates distributed
across the target month. Delivers a rationale-annotated draft (default) or appends
straight into the live content-plan worksheet (feature 003-songbird-content-generation).

Usage (inside the container):
    docker exec prefect python flows/songbird_monthly_plan.py --client "Ecky Dental Center" --month "Agustus 2026"
    docker exec prefect python flows/songbird_monthly_plan.py --client "Ecky Dental Center" --month "Agustus 2026" --target live
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


@flow(name="songbird-monthly-plan", description="Generate a client's monthly content plan")
async def songbird_monthly_plan_flow(
    client: str,
    month: str,
    target: str = "draft",
    quantity: Optional[int] = None,
    platform: str = "",
    audience: str = "",
    goal: str = "",
    tone: str = "",
    content_pillars: Optional[List[str]] = None,
    signal_window_days: int = 180,
    live_spreadsheet_id: Optional[str] = None,
    live_tab: Optional[str] = None,
    credentials_block_name: str = "google-creds",
):
    """
    Generate a monthly content plan for one client.

    Args:
        client: Client name (grounds KB lookup, handle resolution, config quantity).
        month: Target month "Month YYYY" (e.g. "Agustus 2026"); dates spread across it.
        target: "draft" (default, reviewable Sheet) or "live" (append to content-plan sheet).
        quantity: Override the per-client configured monthly quantity (None ⇒ read config).
        platform, audience, goal, tone, content_pillars: marketing parameters.
        signal_window_days: Rolling recency window for top performers (default 180).
        live_spreadsheet_id, live_tab: live content-plan worksheet target (target="live").
        credentials_block_name: Google credentials block name.

    Returns:
        Dict with start_time, end_time, data, summary, and optional error.
    """
    return await run_generation(
        client=client,
        quantity=quantity,
        distribute_dates=True,
        resolve_quantity_from_config=(quantity is None),
        month=month,
        target=target,
        platform=platform,
        audience=audience,
        goal=goal,
        tone=tone,
        content_pillars=content_pillars,
        signal_window_days=signal_window_days,
        live_spreadsheet_id=live_spreadsheet_id,
        live_tab=live_tab,
        credentials_block_name=credentials_block_name,
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client", required=True, help="Client name")
    parser.add_argument("--month", required=True, help='Target month, e.g. "Agustus 2026"')
    parser.add_argument("--target", default="draft", choices=["draft", "live"])
    parser.add_argument("--quantity", type=int, default=None, help="Override per-client configured quantity")
    parser.add_argument("--platform", default="")
    parser.add_argument("--audience", default="")
    parser.add_argument("--goal", default="")
    parser.add_argument("--tone", default="")
    parser.add_argument("--content-pillars", nargs="*", default=None)
    parser.add_argument("--signal-window-days", type=int, default=180)
    parser.add_argument("--live-spreadsheet-id", default=None)
    parser.add_argument("--live-tab", default=None)
    parser.add_argument("--credentials-block-name", default="google-creds")
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    result = asyncio.run(
        songbird_monthly_plan_flow(
            client=args.client, month=args.month, target=args.target, quantity=args.quantity,
            platform=args.platform, audience=args.audience, goal=args.goal, tone=args.tone,
            content_pillars=args.content_pillars, signal_window_days=args.signal_window_days,
            live_spreadsheet_id=args.live_spreadsheet_id, live_tab=args.live_tab,
            credentials_block_name=args.credentials_block_name,
        )
    )

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    output_path = os.path.join(OUTPUT_DIR, f"songbird_monthly_plan_{timestamp}.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(json.dumps(result["summary"], indent=2, ensure_ascii=False))
    print(f"Full result saved to {output_path}")
    sys.exit(1 if result.get("error") else 0)
