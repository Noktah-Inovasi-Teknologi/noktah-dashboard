"""
Read-only readiness check for the monthly content-plan -> Jira conversion.

Answers "which clients are ready" without creating anything: for each client it
reports whether a content plan exists for the target month, and (unless
--files-only) how many rows it holds and how those rows would fare against
Jira's formatting rules.

Creates no Jira issues and writes nothing back to any sheet.
"""
import argparse
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from content_plan_spreadsheet_to_jira_issue import (  # noqa: E402
    read_content_plan_flow,
    search_content_plan_files_flow,
    read_content_plan_data_flow,
)
from tasks import jira_adf as adf  # noqa: E402
from tasks import utility_tasks  # noqa: E402
from tasks.utility_tasks import convert_content_plan_row_to_jira_issue  # noqa: E402

# The converter calls get_run_logger(), which needs a live Prefect task context.
# Calling .fn outside one raises MissingContextError -- which would otherwise be
# swallowed by the per-row except below and counted as a formatting fault.
utility_tasks.get_run_logger = lambda: logging.getLogger("readiness")


async def main(target_month, files_only):
    # The Clients worksheet's own Status column -- Active / Pending / etc. The
    # conversion flow does NOT filter on it: every client in the sheet is
    # searched and converted regardless, so a Pending client with a plan would
    # get Jira issues today.
    roster = await read_content_plan_flow()
    status = {
        str(c.get("Name", "")).strip(): (str(c.get("Status", "")).strip() or "—")
        for c in roster.get("data", [])
    }

    search = await search_content_plan_files_flow(target_month=target_month)
    if "error" in search:
        print(f"ERROR: {search['error']}")
        return 1

    month = search["summary"]["search_month"]
    found = {c["client_name"]: c["content_plan_id"] for c in search["output"]}
    print(f"\nTarget month: {month}")
    print(f"Clients in sheet: {len(found)}   with a content plan: "
          f"{search['summary']['clients_with_content_plans']}\n")

    missing = [n for n, cid in found.items() if not cid]
    if files_only:
        for name, cid in found.items():
            print(f"  {'OK  ' if cid else 'NONE'}  {status.get(name, '?'):<10} {name}")
        return 0

    data = await read_content_plan_data_flow(
        target_month=target_month, min_delay_seconds=1, max_delay_seconds=2
    )
    plans = {p["client_name"]: p for p in data.get("content_plans", [])}

    print(f"{'CLIENT':<32} {'STATUS':<9} {'ROWS':>5} {'DATED':>6} {'FATAL':>6}  NOTE")
    print("-" * 88)
    ready = 0
    crashed = []
    for name in found:
        plan = plans.get(name, {})
        st = status.get(name, "?")
        if not found[name]:
            print(f"{name:<32} {st:<9} {'-':>5} {'-':>6} {'-':>6}  "
                  f"no content plan for {month}")
            continue
        if "error" in plan or "data" not in plan:
            print(f"{name:<32} {st:<9} {'-':>5} {'-':>6} {'-':>6}  "
                  f"unreadable: {str(plan.get('error'))[:30]}")
            continue

        rows = [r for r in plan["data"] if str(r.get("Topik", "")).strip()]
        dated = sum(1 for r in rows if str(r.get("Tanggal", "")).strip())
        fatal = 0
        for row in rows:
            try:
                convert_content_plan_row_to_jira_issue.fn(row=row, client_name=name)
            except Exception as exc:  # a conversion crash is not a formatting fault
                crashed.append(f"{name}: {type(exc).__name__}: {exc}")
                continue
            # What Jira WOULD reject, judged on the raw cell rather than on the
            # already-sanitised payload -- which by construction has no faults.
            if adf.find_fatal_problems({"fields": {"summary": str(row.get("Topik", ""))}}):
                fatal += 1

        note = "ready" if rows and dated == len(rows) else ""
        if not rows:
            note = "plan exists but has no topics"
        elif dated < len(rows):
            note = f"{len(rows) - dated} row(s) with no date"
        if rows and dated == len(rows):
            ready += 1
        print(f"{name:<32} {st:<9} {len(rows):>5} {dated:>6} {fatal:>6}  {note}")

    print("-" * 88)
    by_status = {}
    for name in found:
        st = status.get(name, "?")
        entry = by_status.setdefault(st, {"total": 0, "with_plan": 0})
        entry["total"] += 1
        entry["with_plan"] += 1 if found[name] else 0
    for st, e in sorted(by_status.items()):
        print(f"{st:<12} {e['with_plan']}/{e['total']} have a plan for {month}")
    print(f"{ready} of {len(found)} clients fully ready for {month}")
    if missing:
        print(f"No plan yet: {', '.join(missing)}")
    if crashed:
        print("\nConversion errors (not formatting):")
        for c in crashed[:10]:
            print(f"  {c}")
    print("\nFATAL = rows whose Topik would have been REJECTED by Jira "
          "(newline / >255 chars); repaired automatically on the real run.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--month", default=None)
    parser.add_argument("--files-only", action="store_true")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.month, args.files_only)))
