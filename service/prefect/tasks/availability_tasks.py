"""
Field availability determination tasks (feature 005-signal-field-coverage).

Answers "can this field ever be known?" for a (platform, content_type, field)
triple. The companion question — "was it known this time?" — is answered by
capture outcomes in social_tasks.py; the two together are what let a consumer
resolve an empty engagement value to exactly one cause.

The authoritative source is `config/field_availability.yaml`, a
version-controlled file (FR-001a) reconciled into the `field_availability`
table by `availability.determination.sync` (FR-001b). Determinations are
engineering findings with evidence behind them, so they belong somewhere a
change is reviewable as a diff — not in opaque stored state.
"""
import hashlib
import logging
import os
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from prefect import task

try:
    from ..db import db_pool, maybe_transaction
except ImportError:
    import sys
    sys.path.append(os.path.dirname(os.path.dirname(__file__)))
    from db import db_pool, maybe_transaction

logger = logging.getLogger(__name__)


async def _db_pool(db_env: Optional[str] = None):
    """The module-local pool seam every task module here exposes.

    Tests monkeypatch `_db_pool`, not `db_pool` — patching the shared helper
    directly forced the common fixture to accept arbitrary arguments, which
    stopped it catching an arity mistake in any of the six modules using it.
    """
    return await db_pool(db_env)

# The CLOSED status vocabulary (FR-002/FR-002a). Mirrored by a CHECK constraint
# in migration 007 — validated here for a readable error, and there so a bad
# value is impossible to store even if this task is bypassed.
VALID_STATUSES = frozenset({
    "available",
    "unavailable_platform_limit",
    "not_collected_by_decision",
    "inconclusive",
    "undetermined",
})

VALID_PLATFORMS = frozenset({"instagram", "tiktok"})

# The per-item metric axis of the availability matrix. Single definition: it is
# also the set of engagement columns on `harvested_signals`. Adding a metric and
# forgetting this list would make coverage report "complete" while the new field
# had no determination anywhere — a false all-clear on the feature's own success
# criterion, which is the exact class of error this feature exists to remove.
METRIC_FIELDS = ("likes", "comments", "views", "shares")

REQUIRED_FIELDS = ("platform", "content_type", "field", "status", "reason", "determined_on")

# Default location. `config/`, NOT `data/` — .gitignore ignores any directory
# named `data`, which would leave the source untracked and defeat FR-001a.
#
# Two layouts must both work: the repo checkout on the host
# (<repo>/config/field_availability.yaml, where this file is
# service/prefect/tasks/…) and the container, where only service/prefect is
# mounted at /app and the file is bind-mounted to /app/config/. Probing rather
# than assuming avoids an IndexError on parents[3] inside the container.
_HERE = Path(__file__).resolve()
_CANDIDATE_SOURCE_PATHS = tuple(
    base / "config" / "field_availability.yaml"
    # parents[1] is /app in the container; parents[3] is the repo root on the
    # host. The slice yields () rather than raising when the tree is shallower.
    for base in (_HERE.parents[1], *_HERE.parents[3:4])
)


def _default_source_path() -> Path:
    # Falls back to the first candidate so a "not found" error names a real path.
    return next((c for c in _CANDIDATE_SOURCE_PATHS if c.exists()), _CANDIDATE_SOURCE_PATHS[0])


class DeterminationSourceError(ValueError):
    """The YAML source is malformed. Raised before anything is written."""


def _source_path(path: Optional[str] = None) -> Path:
    explicit = path or os.environ.get("FIELD_AVAILABILITY_PATH")
    return Path(explicit) if explicit else _default_source_path()


def load_determinations(path: Optional[str] = None) -> tuple[List[Dict[str, Any]], str]:
    """
    Parse and validate the YAML source. Pure — no I/O beyond the read.

    Returns `(determinations, source_version)` where `source_version` is a
    content hash, recorded on every synced row so a determination can be traced
    back to the exact file revision that produced it.

    Raises `DeterminationSourceError` on ANY problem. Rejecting the whole file
    rather than importing the valid subset is deliberate: a determination that
    silently reverted to a stale value is worse than a sync that visibly failed,
    because the stale value still looks authoritative to every consumer.
    """
    src = _source_path(path)
    raw = src.read_text(encoding="utf-8")
    source_version = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    try:
        doc = yaml.safe_load(raw) or {}
    except yaml.YAMLError as e:
        raise DeterminationSourceError(f"{src}: not valid YAML: {e}") from e

    if not isinstance(doc, dict):
        raise DeterminationSourceError(f"{src}: top level must be a mapping")

    entries = doc.get("determinations")
    if entries is None:
        raise DeterminationSourceError(f"{src}: missing 'determinations' key")
    if not isinstance(entries, list):
        raise DeterminationSourceError(f"{src}: 'determinations' must be a list")

    seen: set[tuple[str, str, str]] = set()
    out: List[Dict[str, Any]] = []

    for i, e in enumerate(entries):
        where = f"{src}: determinations[{i}]"
        if not isinstance(e, dict):
            raise DeterminationSourceError(f"{where}: must be a mapping")

        for key in REQUIRED_FIELDS:
            if e.get(key) is None or (isinstance(e[key], str) and not e[key].strip()):
                raise DeterminationSourceError(f"{where}: missing required field '{key}'")

        platform = str(e["platform"]).strip()
        content_type = str(e["content_type"]).strip()
        field_name = str(e["field"]).strip()
        status = str(e["status"]).strip()

        if platform not in VALID_PLATFORMS:
            raise DeterminationSourceError(
                f"{where}: unknown platform {platform!r} (expected one of {sorted(VALID_PLATFORMS)})"
            )
        if status not in VALID_STATUSES:
            raise DeterminationSourceError(
                f"{where}: unknown status {status!r}. The vocabulary is closed (FR-002a); "
                f"expected one of {sorted(VALID_STATUSES)}"
            )

        # Evidence is required for every status except `undetermined` — the one
        # status that means "we haven't looked yet", and so has nothing to cite.
        if status != "undetermined" and not str(e.get("evidence") or "").strip():
            raise DeterminationSourceError(
                f"{where}: status {status!r} requires 'evidence'; only 'undetermined' may omit it"
            )

        key = (platform, content_type, field_name)
        if key in seen:
            raise DeterminationSourceError(f"{where}: duplicate determination for {key}")
        seen.add(key)

        determined_on = e["determined_on"]
        if not isinstance(determined_on, date):
            raise DeterminationSourceError(
                f"{where}: 'determined_on' must be a date (YYYY-MM-DD), got {determined_on!r}"
            )

        out.append({
            "platform": platform,
            "content_type": content_type,
            "field_name": field_name,
            "status": status,
            "reason": " ".join(str(e["reason"]).split()),
            "evidence": " ".join(str(e["evidence"]).split()) if e.get("evidence") else None,
            "determined_on": determined_on,
            "source_version": source_version,
        })

    return out, source_version


@task(name="availability.determination.sync", retries=0)
async def availability_determination_sync(
    path: Optional[str] = None, validate_only: bool = False, db_env: Optional[str] = None
) -> Dict[str, Any]:
    """
    Reconcile the YAML source into `field_availability` (FR-001b).

    Idempotent: re-running against an unchanged file reports every row
    `unchanged` and writes nothing. Rows present in the table but absent from
    the file are LEFT ALONE — a truncate-and-reload would erase a determination
    outright if the file were ever trimmed by accident, and this table is
    reference data other systems read.

    `retries=0`: a malformed source will not become well-formed on a retry, and
    a database error here should surface immediately rather than three times.
    """
    determinations, source_version = load_determinations(path)

    result = {"inserted": 0, "updated": 0, "unchanged": 0, "rows": []}
    pool = await _db_pool(db_env)
    try:
        async with pool.acquire() as conn:
            async with maybe_transaction(conn, validate_only):
                for d in determinations:
                    existing = await conn.fetchrow(
                        """
                        SELECT status, reason, evidence, determined_on
                        FROM field_availability
                        WHERE platform = $1 AND content_type = $2 AND field_name = $3
                        """,
                        d["platform"], d["content_type"], d["field_name"],
                    )
                    if existing is None:
                        action = "inserted"
                    elif (
                        existing["status"] == d["status"]
                        and existing["reason"] == d["reason"]
                        and existing["evidence"] == d["evidence"]
                        and existing["determined_on"] == d["determined_on"]
                    ):
                        action = "unchanged"
                    else:
                        action = "updated"

                    if action != "unchanged":
                        await conn.execute(
                            """
                            INSERT INTO field_availability
                                (platform, content_type, field_name, status, reason,
                                 evidence, determined_on, source_version)
                            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                            ON CONFLICT (platform, content_type, field_name) DO UPDATE SET
                                status = EXCLUDED.status,
                                reason = EXCLUDED.reason,
                                evidence = EXCLUDED.evidence,
                                determined_on = EXCLUDED.determined_on,
                                source_version = EXCLUDED.source_version,
                                synced_at = now()
                            """,
                            d["platform"], d["content_type"], d["field_name"], d["status"],
                            d["reason"], d["evidence"], d["determined_on"], d["source_version"],
                        )

                    result[action] += 1
                    result["rows"].append({
                        "platform": d["platform"],
                        "content_type": d["content_type"],
                        "field": d["field_name"],
                        "status": d["status"],
                        "action": action,
                    })
    finally:
        await pool.close()

    result["source_version"] = source_version
    return result


@task(name="availability.determination.coverage-gaps", retries=0)
async def availability_determination_coverage_gaps(
    db_env: Optional[str] = None, fields: Optional[List[str]] = None
) -> List[str]:
    """
    Every (platform, content_type, field) present in `harvested_signals` that has
    no determination. This is the value SC-001 is measured against.

    Derived from what has actually been *collected*, not from a hardcoded matrix,
    so a newly appearing content type shows up as a gap instead of being silently
    absent from the coverage question.
    """
    fields = fields or list(METRIC_FIELDS)
    pool = await _db_pool(db_env)
    try:
        async with pool.acquire() as conn:
            observed = await conn.fetch(
                "SELECT DISTINCT platform, content_type FROM harvested_signals"
            )
            have = {
                (r["platform"], r["content_type"], r["field_name"])
                for r in await conn.fetch(
                    "SELECT platform, content_type, field_name FROM field_availability"
                )
            }
    finally:
        await pool.close()

    return [
        f"{r['platform']}/{r['content_type']}/{f}"
        for r in observed
        for f in fields
        if (r["platform"], r["content_type"], f) not in have
    ]


@task(name="availability.determination.get", retries=0)
async def availability_determination_get(
    platform: str, content_type: str, field: str, db_env: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """
    One determination, or None.

    A caller receiving None MUST treat it as `undetermined`. It MUST NOT infer
    availability from whether stored values happen to be present or absent —
    concluding "the platform must not publish it" from an empty column is
    exactly the reasoning error this feature exists to eliminate.
    """
    pool = await _db_pool(db_env)
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT platform, content_type, field_name, status, reason,
                       evidence, determined_on, source_version
                FROM field_availability
                WHERE platform = $1 AND content_type = $2 AND field_name = $3
                """,
                platform, content_type, field,
            )
    finally:
        await pool.close()
    return dict(row) if row else None
