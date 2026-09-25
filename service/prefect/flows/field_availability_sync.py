"""
field-availability-sync — reconcile field availability determinations.

Reads `config/field_availability.yaml` (the version-controlled source of truth,
FR-001a) and mirrors it into the `field_availability` table so a determination
can be joined against `harvested_signals` in SQL rather than existing only
inside a running process (FR-001b).

Answers "can this field ever be known?". The companion record — "was it known
this time?" — is `capture_outcomes`, written by the harvest engine. Together
they let a consumer resolve any empty engagement value to exactly one cause,
which is the entire point of feature 005.

Run:
    docker exec prefect python flows/field_availability_sync.py
    docker exec prefect python flows/field_availability_sync.py --validate-only
"""
import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from prefect import flow, get_run_logger

try:
    from ..tasks.availability_tasks import (
        DeterminationSourceError,
        availability_determination_coverage_gaps,
        availability_determination_sync,
    )
except ImportError:
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from tasks.availability_tasks import (
        DeterminationSourceError,
        availability_determination_coverage_gaps,
        availability_determination_sync,
    )

try:
    from .common.alerts import alert_hooks
except ImportError:
    sys.path.append(os.path.dirname(os.path.abspath(__file__)))
    from common.alerts import alert_hooks


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@flow(name="field-availability-sync", **alert_hooks())
async def field_availability_sync(
    path: Optional[str] = None,
    validate_only: bool = False,
    db_env: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Sync determinations from the YAML source into the datastore.

    Returns the standard result dict (constitution I) and never raises. A
    malformed source sets `error` and writes NOTHING — importing the valid
    subset would let a determination silently revert to a stale value, which is
    worse than a visibly failed sync because the stale value still reads as
    authoritative to every consumer.
    """
    run_logger = get_run_logger()
    result: Dict[str, Any] = {
        "start_time": _now(),
        "end_time": None,
        "data": [],
        "summary": {
            "determinations_total": 0,
            "inserted": 0,
            "updated": 0,
            "unchanged": 0,
            "rejected": 0,
            "coverage_gaps": [],
            "validate_only": validate_only,
        },
        "error": None,
    }

    try:
        synced = await availability_determination_sync(
            path=path, validate_only=validate_only, db_env=db_env
        )
        result["data"] = synced["rows"]
        result["summary"].update({
            "determinations_total": len(synced["rows"]),
            "inserted": synced["inserted"],
            "updated": synced["updated"],
            "unchanged": synced["unchanged"],
            "source_version": synced["source_version"],
        })
        run_logger.info(
            f"determinations={len(synced['rows'])} "
            f"inserted={synced['inserted']} updated={synced['updated']} "
            f"unchanged={synced['unchanged']} (validate_only={validate_only})"
        )

        # SC-001 is measured against this. Derived from what has actually been
        # collected, so a newly appearing content type surfaces as a gap rather
        # than being quietly outside the question.
        #
        # Under --validate-only the writes above were rolled back, so this
        # reports the gaps as they stand BEFORE syncing — which is the honest
        # answer for a dry run, not a bug. Only a real run's empty list means
        # coverage is actually complete.
        gaps = await availability_determination_coverage_gaps(db_env=db_env)
        result["summary"]["coverage_gaps"] = gaps
        if gaps:
            run_logger.warning(
                f"{len(gaps)} observed (platform, content_type, field) combination(s) "
                f"have no determination: {gaps}"
            )
        else:
            run_logger.info("coverage complete: every observed combination has a determination")

    except DeterminationSourceError as e:
        # The whole file is rejected, so the count is "all of them".
        result["summary"]["rejected"] = 1
        result["error"] = str(e)
        run_logger.error(f"source rejected, nothing written: {e}")
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"
        run_logger.error(f"sync failed: {result['error']}")
    finally:
        result["end_time"] = _now()

    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sync field availability determinations")
    parser.add_argument("--path", help="Override the YAML source path")
    parser.add_argument("--validate-only", action="store_true",
                        help="Parse, validate and report without writing")
    parser.add_argument("--db-env", help="Env var holding an alternate DSN (e.g. SPINE_DB_URL)")
    args = parser.parse_args()

    outcome = asyncio.run(field_availability_sync(
        path=args.path, validate_only=args.validate_only, db_env=args.db_env,
    ))
    print(json.dumps(outcome, indent=2, ensure_ascii=False, default=str))
    sys.exit(1 if outcome.get("error") else 0)
