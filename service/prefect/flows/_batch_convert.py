"""
Batched content-plan -> Jira conversion for the clients marked `active`.

The CLI on content_plan_spreadsheet_to_jira_issue.py only offers --single (one
client), so this drives the same flows directly with a client list, in batches.

  python flows/_batch_convert.py --month "September 2026"            # validate only
  python flows/_batch_convert.py --month "September 2026" --create   # creates issues

--create makes real Jira issues and there is no undo, and the conversion has no
duplicate protection: running it twice for the same month creates every issue
twice. Check what already exists first.
"""
import argparse
import asyncio
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from content_plan_spreadsheet_to_jira_issue import (  # noqa: E402
    read_content_plan_flow,
    convert_content_plan_to_jira_content_flow,
    bulk_create_jira_issues_per_client_flow,
)

BATCH_SIZE = 5


def chunk(items, size):
    return [items[i:i + size] for i in range(0, len(items), size)]


async def main(target_month, create, status_wanted, batch_size, only):
    roster = await read_content_plan_flow()
    if "error" in roster:
        print(f"ERROR reading Clients sheet: {roster['error']}")
        return 1

    clients = [
        str(c.get("Name", "")).strip()
        for c in roster.get("data", [])
        if str(c.get("Status", "")).strip().lower() == status_wanted
    ]
    if only:
        wanted = {o.strip().lower() for o in only.split(",")}
        clients = [c for c in clients if c.lower() in wanted]

    if not clients:
        print(f"No clients with Status='{status_wanted}'")
        return 1

    batches = chunk(clients, batch_size)
    mode = "CREATE (real Jira issues)" if create else "VALIDATE ONLY (nothing created)"
    print(f"\nMonth : {target_month}")
    print(f"Mode  : {mode}")
    print(f"Status: {status_wanted}  ->  {len(clients)} clients in {len(batches)} batch(es)\n")
    for n, b in enumerate(batches, 1):
        print(f"  batch {n}: {', '.join(b)}")

    totals = {"converted": 0, "valid": 0, "created": 0, "rejections_prevented": 0, "failed": []}

    for n, batch in enumerate(batches, 1):
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S") + f"_b{n}"
        print(f"\n{'=' * 76}\nBATCH {n}/{len(batches)}: {', '.join(batch)}\n{'=' * 76}")

        conv = await convert_content_plan_to_jira_content_flow(
            target_month=target_month, client_names=batch, timestamp=stamp
        )
        if "error" in conv:
            print(f"  convert failed: {conv['error']}")
            totals["failed"].append(f"batch {n}: convert: {conv['error']}")
            continue

        files = conv.get("output_files", {}).get("client_files", [])
        totals["converted"] += conv.get("summary", {}).get("total_content_created", 0)
        for f in files:
            print(f"  converted {f['content_count']:>3}  {f['client_name']}")

        if not files:
            print("  nothing to submit for this batch")
            continue

        result = await bulk_create_jira_issues_per_client_flow(
            client_files=files, max_issues=45, validate_only=not create, timestamp=stamp
        )
        summary = result.get("summary", {})
        totals["rejections_prevented"] += summary.get("rows_rejection_prevented", 0)

        for cr in result.get("client_results", []):
            v = cr.get("validation", {})
            name = cr["client_name"]
            if cr.get("status") == "error":
                print(f"  ERROR      {name}: {str(cr.get('error'))[:60]}")
                totals["failed"].append(f"{name}: {cr.get('error')}")
                continue
            totals["valid"] += v.get("final_count", 0)
            made = cr.get("issues_created", 0)
            totals["created"] += made
            flag = ""
            if v.get("rejections_prevented_count"):
                flag = f"  [{v['rejections_prevented_count']} repaired from rejection]"
            if v.get("invalid_count"):
                flag += f"  [{v['invalid_count']} INVALID]"
            verb = f"created {made:>3}" if create else f"valid   {v.get('final_count', 0):>3}"
            print(f"  {verb}  {name}{flag}")

    print(f"\n{'=' * 76}")
    print(f"converted            : {totals['converted']}")
    print(f"passed validation    : {totals['valid']}")
    if create:
        print(f"CREATED IN JIRA      : {totals['created']}")
    print(f"rejections prevented : {totals['rejections_prevented']}")
    if totals["failed"]:
        print("failures:")
        for f in totals["failed"]:
            print(f"  {f}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--month", default=None)
    p.add_argument("--create", action="store_true", help="actually create Jira issues")
    p.add_argument("--status", default="active")
    p.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    p.add_argument("--only", default=None, help="comma-separated subset of client names")
    a = p.parse_args()
    sys.exit(asyncio.run(main(a.month, a.create, a.status.lower(), a.batch_size, a.only)))
