"""
Database backup: dump, verify, and decide which old copies to remove.

`pg_dump`/`pg_restore` come from the `postgresql-client` package in the Prefect
image (Debian trixie ships client 17, which dumps our Postgres 15 server; a
newer pg_dump can always read an older server, never the reverse).

The password is passed through PGPASSWORD in the subprocess environment, never on
the command line, where `ps` and Prefect's own logging would show it.
"""
import os
import re
import subprocess
from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.parse import unquote, urlparse

from prefect import task

# A new backup less than this fraction of the previous one's size is treated as a
# problem, not a routine night. It usually means data was lost upstream, and
# rotating out the older, larger copies would then delete the only good ones.
SHRINK_ALARM_RATIO = 0.5


def backup_file_name(database: str, when: datetime) -> str:
    """`noktah_dashboard_2026-09-25_0200.dump`. Sorts by name in time order."""
    return f"{database}_{when.strftime('%Y-%m-%d_%H%M')}.dump"


def is_backup_of(name: str, database: str) -> bool:
    """Exact pattern, not a prefix: `noktah_dashboard_dev_…` must not rotate with `noktah_dashboard`."""
    return re.fullmatch(rf"{re.escape(database)}_\d{{4}}-\d{{2}}-\d{{2}}_\d{{4}}\.dump", name) is not None


def files_to_delete(files: List[Dict[str, Any]], database: str, keep: int) -> List[Dict[str, Any]]:
    """Backups of `database` beyond the newest `keep`, oldest last. Other files are never touched."""
    ours = sorted((f for f in files if is_backup_of(f.get("name", ""), database)),
                  key=lambda f: f["name"], reverse=True)
    return ours[keep:]


def shrink_problem(new_size: int, previous_size: Optional[int]) -> Optional[str]:
    """A message when the new backup is suspiciously smaller than the last one, else None."""
    if not previous_size or new_size >= previous_size * SHRINK_ALARM_RATIO:
        return None
    return (
        f"Backup baru {new_size / 1e6:.1f} MB, jauh lebih kecil dari sebelumnya "
        f"({previous_size / 1e6:.1f} MB). Mungkin ada data yang hilang: backup lama tidak dihapus."
    )


def _pg_env(dsn: str) -> Dict[str, str]:
    """libpq connection env vars from a postgresql:// URL, so nothing secret goes in argv."""
    u = urlparse(dsn)
    env = dict(os.environ)
    env.update({
        "PGHOST": u.hostname or "localhost",
        "PGPORT": str(u.port or 5432),
        "PGUSER": unquote(u.username or ""),
        "PGPASSWORD": unquote(u.password or ""),
    })
    return env


@task(name="db.backup.dump", retries=1, retry_delay_seconds=60)
def db_backup_dump(dsn: str, database: str, out_path: str) -> int:
    """pg_dump in custom format (compressed, restorable table by table). Returns the file size."""
    proc = subprocess.run(
        ["pg_dump", "--format=custom", "--dbname", database, "--file", out_path],
        env=_pg_env(dsn), capture_output=True, text=True, timeout=1800,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"pg_dump failed ({proc.returncode}): {proc.stderr.strip()[:500]}")
    return os.path.getsize(out_path)


@task(name="db.backup.verify")
def db_backup_verify(path: str) -> Dict[str, int]:
    """Read the archive's table of contents back. A dump that can't be listed can't be restored.

    Returns counts of tables and table-data entries; raises if either is zero.
    """
    proc = subprocess.run(["pg_restore", "--list", path], capture_output=True, text=True, timeout=600)
    if proc.returncode != 0:
        raise RuntimeError(f"pg_restore --list failed: {proc.stderr.strip()[:500]}")
    lines = [l for l in proc.stdout.splitlines() if l and not l.startswith(";")]
    tables = sum(1 for l in lines if " TABLE " in l and " TABLE DATA " not in l)
    table_data = sum(1 for l in lines if " TABLE DATA " in l)
    if tables == 0 or table_data == 0:
        raise RuntimeError(f"Backup looks empty: {tables} tables, {table_data} table-data entries")
    return {"tables": tables, "table_data": table_data}
