"""
Hub Sanctions: hourly. Lets hub-api compute and issue sanctions on their working days, and
writes the SP letters it hands back as Google Docs (spec 009 US9, research R10/R11, ADR-0001).

1. `sanctions/tick` is idempotent and decides what today is (working day 2: compute last
   month's sanctions; after working day 3: issue those not held or on appeal). It returns
   the letters to write, already rendered to HTML.
2. Each letter is uploaded to Drive as a Google Doc (HTML converted on upload) into
   `HUB_SP_LETTER_FOLDER_ID` (Company > HR > Surat Peringatan). Nothing is sent to anyone.
3. `sanctions/letters` records each letter's file id, or why it could not be written.

`--validate-only`: calls nothing (the tick itself issues sanctions, so it is a write);
prints the configuration only.

Usage (inside the container):
    docker exec prefect python flows/hub_sanctions.py
    docker exec prefect python flows/hub_sanctions.py --validate-only
"""
import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from prefect import flow
from prefect.logging import get_run_logger

try:
    from ..tasks.google_tasks import drive_upload_doc
    from ..tasks.hub_tasks import hub_internal_call
except ImportError:
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from tasks.google_tasks import drive_upload_doc
    from tasks.hub_tasks import hub_internal_call

try:
    from .common.alerts import alert_hooks
except ImportError:
    sys.path.append(os.path.dirname(os.path.abspath(__file__)))
    from common.alerts import alert_hooks

FOLDER_ENV = "HUB_SP_LETTER_FOLDER_ID"


@flow(name="hub-sanctions", description="Compute and issue Eskala sanctions; write the SP letters to Drive",
      **alert_hooks("eskala"))
async def hub_sanctions_flow(validate_only: bool = False,
                             credentials_block_name: str = "google-creds") -> Dict[str, Any]:
    """
    One sanctions tick.

    Args:
        validate_only: Call nothing; report the configuration only.
        credentials_block_name: Google credentials block (Drive).

    Returns:
        {start_time, end_time, data: [letter results], summary, error}. `summary.items_failed`
        counts letters that could not be written, which is what the #eskala-otomasi alert reads.
    """
    log = get_run_logger()
    start = datetime.now(timezone.utc)
    folder_id = os.environ.get(FOLDER_ENV, "").strip()
    summary: Dict[str, Any] = {"validate_only": validate_only, "folder_configured": bool(folder_id),
                               "computed": 0, "issued": 0, "letters": 0, "letters_written": 0, "items_failed": 0}
    data: List[Dict[str, Any]] = []
    error: Optional[str] = None
    if validate_only:
        log.info(f"validate-only: no tick (it issues sanctions). {FOLDER_ENV} "
                 + ("is set." if folder_id else "is NOT set: letters would fail."))
        return {"start_time": start.isoformat(), "end_time": datetime.now(timezone.utc).isoformat(),
                "data": data, "summary": summary, "error": None}
    try:
        tick = hub_internal_call("sanctions/tick", json={})
        summary["computed"] = tick.get("computed") or 0
        summary["issued"] = tick.get("issued") or 0
        letters = tick.get("letters") or []
        summary["letters"] = len(letters)
        for letter in letters:
            sid = letter["sanction_id"]
            if not folder_id:
                data.append({"sanction_id": sid, "ok": False,
                             "error": f"{FOLDER_ENV} belum diisi; surat tidak bisa dibuat."})
                continue
            try:
                file_id = await drive_upload_doc(letter.get("file_name") or f"SP {sid}", letter.get("html") or "",
                                                 folder_id, credentials_block_name=credentials_block_name)
                data.append({"sanction_id": sid, "ok": True, "file_id": file_id})
            except Exception as e:  # noqa: BLE001 - one letter never stops the rest
                log.error(f"letter for sanction {sid} failed: {e}")
                data.append({"sanction_id": sid, "ok": False, "error": str(e)[:500]})
        if data:
            hub_internal_call("sanctions/letters", json={"results": data})
        summary["letters_written"] = sum(1 for r in data if r["ok"])
        summary["items_failed"] = len(data) - summary["letters_written"]
        if summary["items_failed"] and not folder_id:
            summary["failures"] = {"Surat SP": f"{FOLDER_ENV} belum diisi di .env."}
        log.info(f"tick: computed {summary['computed']}, issued {summary['issued']}, "
                 f"letters {summary['letters_written']}/{summary['letters']}")
    except Exception as e:  # noqa: BLE001 - flows report, never raise
        error = f"{type(e).__name__}: {e}"
        log.error(error)
    return {"start_time": start.isoformat(), "end_time": datetime.now(timezone.utc).isoformat(),
            "data": data, "summary": summary, "error": error}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--validate-only", action="store_true", help="call nothing; print the configuration")
    args = parser.parse_args()
    result = asyncio.run(hub_sanctions_flow(validate_only=args.validate_only))
    print(json.dumps({"summary": result["summary"], "letters": result["data"]}, ensure_ascii=False, indent=2))
    print("error:", result["error"])
    sys.exit(1 if result["error"] else 0)
