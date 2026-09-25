"""
DB Backup: nightly copy of the company database to Drive.

The database is becoming the one home for company data (client registry, Client
Cards and their history, harvest history). Before this flow there was no backup
at all: one dead disk on the host PC would have lost all of it.

Each run:
  1. pg_dump the database (custom format: compressed, restorable table by table)
  2. verify the archive by reading its table of contents back
  3. upload it to Company > Backups > Database in Drive (the restricted shared
     drive: a dump holds every client's data, so staff drives are the wrong place)
  4. delete copies beyond the newest KEEP. Skipped when the new copy is less than
     half the previous one's size: that usually means data was lost upstream, and
     rotating would eventually delete every good copy.

Any failure posts to #noktah-otomasi via the alert hook.

Restore (into a scratch database first, never straight over the live one):
    docker exec postgres createdb -U noktah restore_check
    docker cp noktah_dashboard_<date>.dump postgres:/tmp/b.dump
    docker exec postgres pg_restore -U noktah -d restore_check --no-owner /tmp/b.dump
One error is expected and harmless: `unrecognized configuration parameter
"transaction_timeout"`. The dump is made by pg_dump 17 and the server is 15;
only that one SET is skipped. Verified 2026-09-24: a restore from Drive
matched the live row counts in every table checked.

Usage (inside the container):
    docker exec prefect python flows/db_backup.py
    docker exec prefect python flows/db_backup.py --validate-only   # dump + verify, no upload
"""
import argparse
import asyncio
import os
import sys
import tempfile
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

from prefect import flow
from prefect.logging import get_run_logger

try:
    from ..tasks.backup_tasks import (
        backup_file_name, db_backup_dump, db_backup_verify, files_to_delete, is_backup_of, shrink_problem,
    )
    from ..tasks.google_tasks import drive_file_delete, drive_file_upload, drive_folder_ensure, google_filter_files_in_folder
except ImportError:
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from tasks.backup_tasks import (
        backup_file_name, db_backup_dump, db_backup_verify, files_to_delete, is_backup_of, shrink_problem,
    )
    from tasks.google_tasks import drive_file_delete, drive_file_upload, drive_folder_ensure, google_filter_files_in_folder

try:
    from .common.alerts import alert_hooks
except ImportError:
    sys.path.append(os.path.dirname(os.path.abspath(__file__)))
    from common.alerts import alert_hooks

# The restricted "Company" shared drive. Backups live in Company > Backups > Database.
BACKUP_DRIVE_ROOT = os.environ.get("DB_BACKUP_DRIVE_ROOT", "0AEfuI_Iza8IeUk9PVA")
DEFAULT_KEEP = 14
WIB = ZoneInfo("Asia/Jakarta")


@flow(name="db-backup", description="Nightly copy of the company database to Drive", **alert_hooks("noktah"))
async def db_backup_flow(
    database: str = "noktah_dashboard",
    keep: int = DEFAULT_KEEP,
    validate_only: bool = False,
    credentials_block_name: str = "google-creds",
) -> Dict[str, Any]:
    """
    Args:
        database: Database to back up (on the server in HARVEST_DB_URL).
        keep: How many most-recent backups to keep in Drive.
        validate_only: Dump and verify only; no upload, no deletion.
        credentials_block_name: Google credentials block.

    Returns:
        Standard dict: start_time, end_time, data, summary, error. Never raises.
    """
    run_logger = get_run_logger()
    start_time = datetime.now(timezone.utc)
    summary: Dict[str, Any] = {
        "database": database, "file_name": None, "size_bytes": None, "tables": None,
        "drive_file_id": None, "deleted": 0, "kept": None, "validate_only": validate_only,
    }
    error: Optional[str] = None
    local_path: Optional[str] = None

    try:
        if keep < 1:
            raise ValueError(f"keep must be at least 1 (got {keep}); 0 would delete the backup just made")
        dsn = os.environ.get("HARVEST_DB_URL")
        if not dsn:
            raise ValueError("HARVEST_DB_URL is not set; cannot reach the database")

        name = backup_file_name(database, datetime.now(WIB))
        local_path = os.path.join(tempfile.gettempdir(), name)
        summary["file_name"] = name

        size = db_backup_dump(dsn, database, local_path)
        counts = db_backup_verify(local_path)
        summary.update(size_bytes=size, tables=counts["tables"])
        run_logger.info(f"Dumped {database}: {size / 1e6:.1f} MB, {counts['tables']} tables")

        if validate_only:
            return _result(start_time, summary, None)

        backups_id = await drive_folder_ensure("Backups", BACKUP_DRIVE_ROOT, credentials_block_name=credentials_block_name)
        folder_id = await drive_folder_ensure("Database", backups_id, credentials_block_name=credentials_block_name)

        existing = await google_filter_files_in_folder(
            folder_id=folder_id, file_name_pattern=database, credentials_block_name=credentials_block_name,
        )
        previous = sorted((f for f in existing if is_backup_of(f.get("name", ""), database)),
                          key=lambda f: f["name"], reverse=True)
        previous_size = int(previous[0]["size"]) if previous and previous[0].get("size") else None

        summary["drive_file_id"] = await drive_file_upload(
            local_path, folder_id, "application/octet-stream", credentials_block_name=credentials_block_name,
        )
        run_logger.info(f"Uploaded {name} to Drive folder {folder_id}")

        shrunk = shrink_problem(size, previous_size)
        if shrunk:
            # Keep every older copy: they may be the only good ones left.
            raise RuntimeError(shrunk)

        stale = files_to_delete(previous + [{"name": name}], database, keep)
        for f in stale:
            await drive_file_delete(f["id"], credentials_block_name=credentials_block_name)
        summary["deleted"] = len(stale)
        summary["kept"] = min(len(previous) + 1, keep)
        run_logger.info(f"Kept {summary['kept']} backups, deleted {len(stale)}")
    except Exception as e:  # noqa: BLE001 - flows report, never raise
        run_logger.error(f"Backup failed: {e}")
        error = f"{type(e).__name__}: {e}"
    finally:
        if local_path and os.path.exists(local_path):
            os.remove(local_path)

    return _result(start_time, summary, error)


def _result(start_time: datetime, summary: Dict[str, Any], error: Optional[str]) -> Dict[str, Any]:
    return {
        "start_time": start_time.isoformat(),
        "end_time": datetime.now(timezone.utc).isoformat(),
        "data": [],
        "summary": summary,
        "error": error,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Back up the company database to Drive")
    parser.add_argument("--database", default="noktah_dashboard")
    parser.add_argument("--keep", type=int, default=DEFAULT_KEEP)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    result = asyncio.run(db_backup_flow(database=args.database, keep=args.keep, validate_only=args.validate_only))
    print(result["summary"], "| error:", result["error"])
