"""
extraction-vocabulary-sync — reconcile the extraction vocabulary into the store.

Reads `config/extraction/vocabulary_v1.yaml` (the version-controlled source of
truth) and mirrors it into `extraction_vocabulary_terms`, so a beat function can
be constrained by a foreign key rather than by a list that only exists inside a
running process (FR-004, FR-043a).

Unscheduled by design: run it after editing the YAML. A vocabulary change is a
deliberate act with a migration-shaped blast radius, not something that should
drift in on a cron.

Run:
    docker exec prefect python flows/extraction_vocabulary_sync.py --validate-only
    docker exec prefect python flows/extraction_vocabulary_sync.py
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
    from ..tasks.extraction_vocabulary_tasks import (
        FrozenVocabularyError,
        VocabularySourceError,
        extraction_vocabulary_get,
        extraction_vocabulary_sync as sync_task,
    )
except ImportError:
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from tasks.extraction_vocabulary_tasks import (
        FrozenVocabularyError,
        VocabularySourceError,
        extraction_vocabulary_get,
        extraction_vocabulary_sync as sync_task,
    )

try:
    from .common.alerts import alert_hooks
except ImportError:
    sys.path.append(os.path.dirname(os.path.abspath(__file__)))
    from common.alerts import alert_hooks


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@flow(name="extraction-vocabulary-sync", **alert_hooks())
async def extraction_vocabulary_sync(
    path: Optional[str] = None,
    validate_only: bool = False,
    db_env: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Sync the vocabulary from YAML into the datastore.

    Returns the standard result dict (constitution I) and never raises. Two
    failure modes are reported distinctly rather than collapsed into one error
    string, because the operator response differs completely:

      * a malformed source is a file to fix;
      * a frozen-version conflict means the change itself is illegitimate and
        needs a NEW version, not a corrected file.
    """
    run_logger = get_run_logger()
    result: Dict[str, Any] = {
        "start_time": _now(),
        "end_time": None,
        "data": [],
        "summary": {
            "version": None,
            "terms_total": 0,
            "inserted": 0,
            "updated": 0,
            "unchanged": 0,
            "rejected": 0,
            "frozen": False,
            "residual": None,
            "validate_only": validate_only,
        },
        "error": None,
    }

    try:
        synced = await sync_task(path=path, validate_only=validate_only, db_env=db_env)
        result["data"] = synced["rows"]
        result["summary"].update({
            "version": synced["version"],
            "terms_total": len(synced["rows"]),
            "inserted": synced["inserted"],
            "updated": synced["updated"],
            "unchanged": synced["unchanged"],
            "frozen": synced["frozen_at"] is not None,
            "frozen_at": synced["frozen_at"],
            "retained_not_in_source": synced["retained_not_in_source"],
        })
        run_logger.info(
            f"vocabulary={synced['version']} terms={len(synced['rows'])} "
            f"inserted={synced['inserted']} updated={synced['updated']} "
            f"unchanged={synced['unchanged']} (validate_only={validate_only})"
        )

        if synced["retained_not_in_source"]:
            run_logger.warning(
                f"{len(synced['retained_not_in_source'])} term(s) present in the table but absent "
                f"from the source and NOT deleted: {synced['retained_not_in_source']}. "
                f"Withdrawing a term requires a new version — beats may reference it."
            )

        # Report the residual explicitly. It is the one term whose absence would
        # be invisible in a term count yet would make FR-003 unsatisfiable, so it
        # is worth naming in the summary rather than leaving to be inferred.
        #
        # Under --validate-only the writes were rolled back, so this reads the
        # table as it stands BEFORE syncing. On a first run that is legitimately
        # empty — the honest answer for a dry run, not a failure.
        current = await extraction_vocabulary_get(version=synced["version"], db_env=db_env)
        result["summary"]["residual"] = current["residual"]
        if not validate_only and not current["residual"]:
            run_logger.error(
                "no residual term is present after sync — content with no discernible structure "
                "has no legal beat function (FR-003)"
            )

    except VocabularySourceError as e:
        # The whole file is rejected, so the count is "all of them".
        result["summary"]["rejected"] = 1
        result["error"] = f"source rejected: {e}"
        run_logger.error(f"source rejected, nothing written: {e}")
    except FrozenVocabularyError as e:
        result["summary"]["rejected"] = 1
        result["summary"]["frozen"] = True
        result["error"] = f"frozen version: {e}"
        run_logger.error(str(e))
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"
        run_logger.error(f"sync failed: {result['error']}")
    finally:
        result["end_time"] = _now()

    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sync the extraction vocabulary")
    parser.add_argument("--path", help="Override the YAML source path")
    parser.add_argument("--validate-only", action="store_true",
                        help="Parse, validate and report without writing")
    parser.add_argument("--db-env", help="Env var holding an alternate DSN (e.g. SPINE_DB_URL)")
    args = parser.parse_args()

    outcome = asyncio.run(extraction_vocabulary_sync(
        path=args.path, validate_only=args.validate_only, db_env=args.db_env,
    ))
    print(json.dumps(outcome, indent=2, ensure_ascii=False, default=str))
    sys.exit(1 if outcome.get("error") else 0)
