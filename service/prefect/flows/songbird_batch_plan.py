"""
Songbird — Batch Content Plan Flow

Generates content plans for one client or many in a single run, so an operator can
cover a month's roster without triggering a deployment per client.

The `clients` parameter takes either form:

    "Klinik Utama Gresik"
    "Klinik Utama Gresik, Klinik Mata Sampang, Klinik Utama Sumenep"

Unscheduled by design — trigger it manually (or from the Prefect UI "Run" form).
Each client is generated independently: one client failing (no config row, no
signal, a model error) is recorded and the batch continues, because a partial set
of plans is far more useful than none.

Usage (inside the container):
    docker exec prefect python flows/songbird_batch_plan.py --clients "Klinik Utama Gresik"
    docker exec prefect python flows/songbird_batch_plan.py \
        --clients "Klinik Utama Gresik, Klinik Mata Sampang" --month "Agustus 2026"
"""
import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Union

from prefect import flow
from prefect.logging import get_run_logger

try:
    from .common.songbird import run_generation
    from ..tasks.utility_tasks import get_date
except ImportError:
    sys.path.append(os.path.dirname(os.path.dirname(__file__)))
    sys.path.append(os.path.dirname(__file__))
    from common.songbird import run_generation
    from tasks.utility_tasks import get_date

try:
    from .common.alerts import alert_hooks
except ImportError:
    sys.path.append(os.path.dirname(os.path.abspath(__file__)))
    from common.alerts import alert_hooks

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "data")

# Client names never contain these, so any of them may separate entries. Newlines are
# included because the Prefect UI's text input makes a pasted column easy to produce.
_SEPARATORS = (",", ";", "\n")


def parse_clients(clients: Union[str, List[str], None]) -> List[str]:
    """
    Normalize the `clients` parameter into a de-duplicated list of names.

    Accepts a single name, a delimited string, or an already-split list, so the same
    flow works from the CLI, the Prefect UI form, and a `--param` JSON array.

    >>> parse_clients("Klinik Utama Gresik")
    ['Klinik Utama Gresik']
    >>> parse_clients("Klinik Utama Gresik, Klinik Mata Sampang")
    ['Klinik Utama Gresik', 'Klinik Mata Sampang']
    """
    if clients is None:
        return []
    raw = clients if isinstance(clients, list) else [clients]

    parts: List[str] = []
    for entry in raw:
        text = str(entry or "")
        for separator in _SEPARATORS[1:]:
            text = text.replace(separator, _SEPARATORS[0])
        parts.extend(text.split(_SEPARATORS[0]))

    seen: Dict[str, None] = {}
    for part in parts:
        name = " ".join(part.split())          # collapse stray whitespace
        if name and name.casefold() not in {k.casefold() for k in seen}:
            seen[name] = None
    return list(seen)


@flow(name="songbird-batch-plan", description="Generate content plans for one or many clients", **alert_hooks())
async def songbird_batch_plan_flow(
    clients: str = "",
    month: Optional[str] = None,
    target: str = "draft",
    quantity: Optional[int] = None,
    content_mix: Optional[Dict[str, int]] = None,
    platform: str = "",
    audience: str = "",
    goal: str = "",
    tone: str = "",
    content_pillars: Optional[List[str]] = None,
    signal_window_days: int = 180,
    signal_half_life_days: float = 90.0,
    exemplar_limit: Optional[int] = None,
    allocation_seed: Optional[int] = None,
    live_spreadsheet_id: Optional[str] = None,
    live_tab: Optional[str] = None,
    credentials_block_name: str = "google-creds",
):
    """
    Generate a monthly content plan for each named client.

    Args:
        clients: One client name, or several separated by commas/semicolons/newlines
            (e.g. "Klinik Utama Gresik, Klinik Mata Sampang").
        month: Target month "Month YYYY"; defaults to next month.
        target: "draft" (default, one reviewable Sheet per client) or "live".
        quantity: Flat total per client, overriding the configured per-type amounts.
        content_mix: Per-type amounts applied to every client in the batch; by default
            each client's own Clients-worksheet row is used.
        platform, audience, goal, tone, content_pillars: marketing parameters, applied
            to every client in the batch.
        signal_window_days, signal_half_life_days, exemplar_limit, allocation_seed:
            signal/ranking tuning, passed through unchanged.
        live_spreadsheet_id, live_tab: optional explicit plan sheet/tab, one client only. Default:
            each client's own "Content Plan - {client} - {month}" sheet.
        credentials_block_name: Google credentials block name.

    Returns:
        Dict with start_time, end_time, data (one entry per client), summary, error.
        Never raises (constitution I).
    """
    run_logger = get_run_logger()
    start_time = datetime.now(timezone.utc)
    data: List[Dict[str, Any]] = []
    summary: Dict[str, Any] = {
        "clients_requested": 0,
        "clients_succeeded": 0,
        "clients_failed": 0,
        "ideas_produced": 0,
        "month": month,
        "target": target,
        "failures": {},
    }
    error: Optional[str] = None
    end_time = start_time

    try:
        names = parse_clients(clients)
        summary["clients_requested"] = len(names)
        if not names:
            raise ValueError(
                'No client names given — pass e.g. clients="Klinik Utama Gresik" '
                'or "Klinik Utama Gresik, Klinik Mata Sampang"'
            )
        # One explicit sheet for several clients would put every client's rows
        # into one client's plan. Without it, each client's own plan is found.
        if live_spreadsheet_id and len(names) > 1:
            raise ValueError(
                "live_spreadsheet_id names one client's plan; it cannot be used with several clients. "
                "Leave it empty and each client's own 'Content Plan - {client} - {month}' is used."
            )

        # Content plans are prepared ahead, so an omitted month means next month.
        target_month = month or await get_date(
            format_type="month_year", offset_months=1, language="indonesian"
        )
        summary["month"] = target_month
        run_logger.info(f"Batch plan for {len(names)} client(s), {target_month}: {', '.join(names)}")

        for index, name in enumerate(names, start=1):
            run_logger.info(f"[{index}/{len(names)}] Generating for '{name}'")
            # run_generation never raises, but a config lookup for an unknown client
            # surfaces as its `error` field — record it and keep going so one bad
            # name cannot cost the whole batch.
            result = await run_generation(
                client=name,
                quantity=quantity,
                content_mix=content_mix,
                distribute_dates=True,
                resolve_quantity_from_config=(quantity is None and content_mix is None),
                month=target_month,
                target=target,
                platform=platform,
                audience=audience,
                goal=goal,
                tone=tone,
                content_pillars=content_pillars,
                signal_window_days=signal_window_days,
                signal_half_life_days=signal_half_life_days,
                exemplar_limit=exemplar_limit,
                allocation_seed=allocation_seed,
                live_spreadsheet_id=live_spreadsheet_id,
                live_tab=live_tab,
                credentials_block_name=credentials_block_name,
            )
            client_summary = result.get("summary") or {}
            entry = {
                "client": name,
                "error": result.get("error"),
                "ideas_produced": client_summary.get("ideas_produced", 0),
                "deliverable_id": client_summary.get("deliverable_id"),
                "summary": client_summary,
            }
            data.append(entry)

            if result.get("error"):
                summary["clients_failed"] += 1
                summary["failures"][name] = result["error"]
                run_logger.warning(f"'{name}' failed: {result['error']}")
            else:
                summary["clients_succeeded"] += 1
                summary["ideas_produced"] += entry["ideas_produced"]
                run_logger.info(
                    f"'{name}': {entry['ideas_produced']} ideas → {entry['deliverable_id']}"
                )

        run_logger.info(
            f"Batch complete: {summary['clients_succeeded']} succeeded, "
            f"{summary['clients_failed']} failed, {summary['ideas_produced']} ideas total"
        )
        # A batch where every client failed is a failed run, not a quiet success.
        if summary["clients_succeeded"] == 0:
            error = f"All {len(names)} client(s) failed; see summary.failures"

    except Exception as e:
        run_logger.error(f"Songbird batch plan failed: {e}")
        error = str(e)
    finally:
        end_time = datetime.now(timezone.utc)

    return {
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
        "data": data,
        "summary": summary,
        "error": error,
    }


def _parse_content_mix(raw: Optional[str]) -> Optional[Dict[str, int]]:
    """Parse 'Post=4,Story=4,Short Video=4' into {content_type: amount}."""
    if not raw:
        return None
    mix: Dict[str, int] = {}
    for part in raw.split(","):
        if "=" not in part:
            raise ValueError(f"Invalid --content-mix entry {part!r}; expected 'Type=N'")
        name, _, amount = part.partition("=")
        mix[name.strip()] = int(amount.strip())
    return mix


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--clients", required=True,
        help='One client name, or several comma-separated: "Klinik Utama Gresik, Klinik Mata Sampang"',
    )
    parser.add_argument("--month", default=None, help='Target month, e.g. "Agustus 2026" (default: next month)')
    parser.add_argument("--target", default="draft", choices=["draft", "live"])
    parser.add_argument("--quantity", type=int, default=None,
                        help="Flat total per client (model picks each content type)")
    parser.add_argument("--content-mix", default=None,
                        help='Per-type amounts for every client, e.g. "Post=4,Story=4,Short Video=4"')
    parser.add_argument("--platform", default="")
    parser.add_argument("--audience", default="")
    parser.add_argument("--goal", default="")
    parser.add_argument("--tone", default="")
    parser.add_argument("--content-pillars", nargs="*", default=None)
    parser.add_argument("--signal-window-days", type=int, default=180)
    parser.add_argument("--signal-half-life-days", type=float, default=90.0)
    parser.add_argument("--exemplar-limit", type=int, default=None)
    parser.add_argument("--allocation-seed", type=int, default=None)
    parser.add_argument("--live-spreadsheet-id", default=None)
    parser.add_argument("--live-tab", default=None)
    parser.add_argument("--credentials-block-name", default="google-creds")
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    result = asyncio.run(
        songbird_batch_plan_flow(
            clients=args.clients, month=args.month, target=args.target,
            quantity=args.quantity, content_mix=_parse_content_mix(args.content_mix),
            platform=args.platform, audience=args.audience, goal=args.goal, tone=args.tone,
            content_pillars=args.content_pillars,
            signal_window_days=args.signal_window_days,
            signal_half_life_days=args.signal_half_life_days,
            exemplar_limit=args.exemplar_limit, allocation_seed=args.allocation_seed,
            live_spreadsheet_id=args.live_spreadsheet_id, live_tab=args.live_tab,
            credentials_block_name=args.credentials_block_name,
        )
    )

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    output_path = os.path.join(OUTPUT_DIR, f"songbird_batch_plan_{timestamp}.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(json.dumps(result["summary"], indent=2, ensure_ascii=False))
    print(f"Full result saved to {output_path}")
    sys.exit(1 if result.get("error") else 0)
