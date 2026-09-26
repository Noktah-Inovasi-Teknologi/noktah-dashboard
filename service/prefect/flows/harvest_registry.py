"""
Harvest Registry: the monthly harvest of every Eskala Client's own and competitor accounts,
driven by the Noktah Hub Registry (spec 009 US3, FR-030…FR-036, research R12).

Replaces the 22 `harvest-monthly-*` deployments (one hard-coded profile each):

1. `harvest/targets` lists the accounts (each once, even when several Clients share it),
   each with `first_harvest`.
2. One account at a time: `run_harvest([url], last N days, list_depth=150)`, where N is 90
   for an account never harvested before and 31 otherwise. Own and competitor accounts get
   the same window, so songbird's within-account ranking compares like with like.
3. Each account's outcome is appended to `harvest_attempts` (collected, nothing_new,
   blocked, not_found, failed, skipped).
4. 30 minutes between accounts (none after the last, none for a single account): pacing
   is what keeps the shared egress IP from being throttled.

"Jalankan sekarang" in the Hub starts this deployment with `account_ids=[id]`,
`trigger="manual"`.

`--validate-only`: lists the targets and the window each would get; harvests nothing.

Usage (inside the container):
    docker exec prefect python flows/harvest_registry.py --validate-only
    docker exec prefect python flows/harvest_registry.py --account-ids <uuid> --trigger manual
"""
import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from prefect import flow
from prefect.logging import get_run_logger

try:
    from ..tasks.harvest_attempt_tasks import harvest_attempt_record
    from ..tasks.hub_tasks import hub_internal_call
except ImportError:
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from tasks.harvest_attempt_tasks import harvest_attempt_record
    from tasks.hub_tasks import hub_internal_call

try:
    from .common.alerts import alert_hooks
    from .common.social_harvest import make_time_window_selector, run_harvest
except ImportError:
    sys.path.append(os.path.dirname(os.path.abspath(__file__)))
    from common.alerts import alert_hooks
    from common.social_harvest import make_time_window_selector, run_harvest

MONTHLY_DAYS, FIRST_DAYS = 31, 90
# The listing depth the monthly deployments used (social_harvest_window.DEFAULT_LIST_DEPTH):
# deep enough that every stored item stays re-observable (feature 006).
LIST_DEPTH = 150
SPACING_SECONDS = 1800


def window_days(target: Dict[str, Any]) -> int:
    if target.get("window_days"):
        return int(target["window_days"])
    return FIRST_DAYS if target.get("first_harvest") else MONTHLY_DAYS


def attempt_outcome(result: Dict[str, Any]) -> Tuple[str, int, Optional[str]]:
    """(outcome, posts collected, reason) of one account's `run_harvest` result."""
    summary = result.get("summary") or {}
    posts = int(summary.get("items_collected") or 0)
    failed_items = int(summary.get("items_failed") or 0)
    if result.get("error"):
        return "failed", posts, str(result["error"])[:500]
    if summary.get("profiles_blocked"):
        return "blocked", posts, (f"Diblokir sementara oleh platform setelah {posts} post terkumpul."
                                  if posts else "Diblokir sementara oleh platform.")
    if summary.get("profiles_not_found"):
        return "not_found", posts, "Akun tidak ditemukan atau privat."
    if summary.get("profiles_unresolved"):
        return "skipped", posts, "Akun belum terdaftar atau nonaktif di roster."
    extra = f"{failed_items} post gagal diproses." if failed_items else None
    return ("collected" if posts else "nothing_new"), posts, extra


def _flow_run_id() -> Optional[str]:
    try:
        from prefect.runtime import flow_run
        return str(flow_run.id) if flow_run.id else None
    except Exception:  # noqa: BLE001
        return None


@flow(name="harvest-registry", description="Harvest every Eskala Client's own and competitor accounts",
      **alert_hooks("eskala"))
async def harvest_registry_flow(
    account_ids: Optional[List[str]] = None,
    trigger: str = "monthly",
    validate_only: bool = False,
    credentials_block_name: str = "google-creds",
) -> Dict[str, Any]:
    """
    Harvest the Registry's accounts, one at a time, and record each attempt.

    Args:
        account_ids: Only these accounts (the Hub's "Jalankan sekarang"); default all.
        trigger: "monthly" (the schedule) or "manual".
        validate_only: List targets and windows only; harvest nothing, record nothing.
        credentials_block_name: Google credentials block (Drive media delivery).

    Returns:
        {start_time, end_time, data: [one entry per account], summary, error}. The summary
        carries the harvest's `profiles_blocked` and `items_failed` totals and
        `failures` ({handle: reason}), which is what the #eskala-otomasi alert reads.
    """
    log = get_run_logger()
    start = datetime.now(timezone.utc)
    data: List[Dict[str, Any]] = []
    summary: Dict[str, Any] = {"trigger": trigger, "validate_only": validate_only, "targets": 0, "attempted": 0,
                               "collected": 0, "nothing_new": 0, "blocked": 0, "not_found": 0, "failed": 0,
                               "skipped": 0, "posts_collected": 0, "profiles_blocked": 0, "items_failed": 0}
    failures: Dict[str, str] = {}
    error: Optional[str] = None
    try:
        if trigger not in ("monthly", "manual"):
            raise ValueError(f"trigger must be 'monthly' or 'manual', got {trigger!r}")
        req: Dict[str, Any] = {"account_ids": list(account_ids)} if account_ids else {}
        targets = hub_internal_call("harvest/targets", json=req).get("targets") or []
        summary["targets"] = len(targets)
        flow_run_id = _flow_run_id()

        for i, target in enumerate(targets):
            days = window_days(target)
            label = f"{target.get('platform')}:{target.get('handle')}"
            entry: Dict[str, Any] = {"account_id": target["account_id"], "handle": target.get("handle"),
                                     "platform": target.get("platform"), "url": target.get("url"),
                                     "clients": target.get("clients") or [], "window_days": days,
                                     "first_harvest": bool(target.get("first_harvest"))}
            if validate_only:
                log.info(f"{label}: would harvest the last {days} days ({', '.join(entry['clients'])})")
                data.append(entry)
                continue

            started = datetime.now(timezone.utc)
            try:
                result = await run_harvest(profiles=[target["url"]], depth_selector=make_time_window_selector(days),
                                           harvest_name=None, list_depth=LIST_DEPTH,
                                           credentials_block_name=credentials_block_name)
            except Exception as e:  # noqa: BLE001 - run_harvest never raises, but be sure
                result = {"summary": {}, "error": f"{type(e).__name__}: {e}"}
            ended = datetime.now(timezone.utc)
            outcome, posts, reason = attempt_outcome(result)
            hsum = result.get("summary") or {}
            summary["attempted"] += 1
            summary[outcome] += 1
            summary["posts_collected"] += posts
            summary["profiles_blocked"] += int(hsum.get("profiles_blocked") or 0)
            summary["items_failed"] += int(hsum.get("items_failed") or 0)
            if outcome in ("failed", "not_found", "skipped"):
                failures[label] = reason or outcome
            entry.update({"outcome": outcome, "posts_collected": posts, "reason": reason})
            log.info(f"{label}: {outcome}, {posts} post(s)" + (f" ({reason})" if reason else ""))
            try:
                await harvest_attempt_record(account_id=target["account_id"], trigger=trigger, window_days=days,
                                             started_at=started, ended_at=ended, outcome=outcome,
                                             posts_collected=posts, reason=reason, flow_run_id=flow_run_id)
            except Exception as e:  # noqa: BLE001 - the harvest happened; say the record is missing
                failures[f"{label} (catatan)"] = f"harvest_attempts tidak tercatat: {e}"
                log.error(f"{label}: could not record the attempt: {e}")
            data.append(entry)

            if len(targets) > 1 and i < len(targets) - 1:
                log.info(f"waiting {SPACING_SECONDS // 60} minutes before the next account")
                await asyncio.sleep(SPACING_SECONDS)
    except Exception as e:  # noqa: BLE001 - flows report, never raise
        error = f"{type(e).__name__}: {e}"
        log.error(error)
    if failures:
        summary["failures"] = failures
    return {"start_time": start.isoformat(), "end_time": datetime.now(timezone.utc).isoformat(),
            "data": data, "summary": summary, "error": error}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--account-ids", default=None, help="comma-separated account ids (default: all)")
    parser.add_argument("--trigger", default="monthly", choices=["monthly", "manual"])
    parser.add_argument("--validate-only", action="store_true", help="list targets and windows; harvest nothing")
    args = parser.parse_args()
    ids = [a.strip() for a in args.account_ids.split(",") if a.strip()] if args.account_ids else None
    result = asyncio.run(harvest_registry_flow(account_ids=ids, trigger=args.trigger,
                                               validate_only=args.validate_only))
    print(json.dumps({"summary": result["summary"], "accounts": result["data"]}, ensure_ascii=False, indent=2))
    print("error:", result["error"])
    sys.exit(1 if result["error"] else 0)
