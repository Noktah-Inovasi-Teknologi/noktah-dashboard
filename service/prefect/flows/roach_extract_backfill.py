"""
roach-extract-backfill — re-extract already-collected items under the new schema.

The 819+ signal rows collected before feature 007 hold prose flow, unusable by the
structured queries. This re-extracts them, and tells you the bill first.

⚠️ COSTS MONEY. Unscheduled and manual, with three gates in front of any spend:
a dry-run projection, a per-run threshold, and a hard monthly ceiling.

Four rules this flow exists to keep:

  1. **It writes a STRICT SUBSET of what the forward path writes** — only the new
     extraction tables, NEVER `harvested_signals` (research.md R10, FR-009).
     Re-extraction produces a new subtitle; writing it back would overwrite the
     stored transcript irreversibly, because harvested_signals holds exactly one
     row per item. A re-extraction cannot restore what it replaced, only produce
     a third different one, at cost.
  2. **Media comes from Drive, never from a platform** (FR-034, Constitution X).
     An unreachable Drive object is a classified skip, not a re-scrape, and no
     code path offers one.
  3. **The first dry-run is UNCALIBRATED and says so** (FR-033). A confident
     dollar figure with no measured baseline behind it would be a fabrication
     sharing a field with a measurement.
  4. **Every item resolves to exactly one outcome** (SC-010) — extracted, cached,
     quarantined, or a classified skip. `unclassified: 0` is the assertion.

Run:
    docker exec prefect python flows/roach_extract_backfill.py --dry-run
    docker exec prefect python flows/roach_extract_backfill.py --pilot 12
    docker exec prefect python flows/roach_extract_backfill.py --confirm
    docker exec prefect python flows/roach_extract_backfill.py --dry-run --platform instagram
"""
import argparse
import asyncio
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from prefect import flow, get_run_logger

try:
    from ..db import db_pool
    from ..tasks.extraction_tasks import (
        SpendCeilingExceeded,
        extraction_batch_record_finish,
        extraction_batch_record_start,
        extraction_cost_ceiling_check,
        extraction_cost_project,
        extraction_quarantine_record,
        extraction_record_store,
        SPEND_THRESHOLD_USD,
    )
    from ..tasks.google_tasks import drive_file_download
    from ..tasks.social_tasks import social_item_analyze
except ImportError:
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from db import db_pool
    from tasks.extraction_tasks import (
        SpendCeilingExceeded,
        extraction_batch_record_finish,
        extraction_batch_record_start,
        extraction_cost_ceiling_check,
        extraction_cost_project,
        extraction_quarantine_record,
        extraction_record_store,
        SPEND_THRESHOLD_USD,
    )
    from tasks.google_tasks import drive_file_download
    from tasks.social_tasks import social_item_analyze

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")

# Media MUST be staged on the volume BOTH containers share.
#
# roach runs in its own container and receives a PATH, not bytes. A path under
# this process's /tmp does not exist over there, so roach opens nothing and
# reports a provider error — measured: a 12-item pilot produced 12 quarantine
# rows at $0.00, all of them our own plumbing rather than any provider failure.
#
# `social_data` is bind-mounted at /data in prefect, prefect-worker AND roach,
# which is exactly how the forward path already moves media between them.
SHARED_MEDIA_DIR = os.environ.get("HARVEST_SHARED_DIR", "/data")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


async def _select_scope(
    conn, platform: Optional[str], content_type: Optional[str], profile: Optional[str],
) -> List[Dict[str, Any]]:
    """
    Resolve the set of items to re-extract, AT THE MOMENT THE RUN STARTS.

    Scope is "everything present when the run starts", NEVER a literal count —
    the corpus was 656 items at the audit snapshot and 824 when this shipped
    (research.md R1), and a hardcoded number would silently under-cover a growing
    corpus.

    Items whose ORIGINAL analysis failed are included on the same terms as
    successful ones (FR-031): they have no prose to preserve, so there is nothing
    to weigh against re-extracting them.
    """
    clauses = [
        # Not yet extracted at the CURRENT vocabulary version. Derived from the
        # vocabulary table rather than hardcoded, so publishing v2 re-opens the
        # whole corpus with no code change.
        """NOT EXISTS (
             SELECT 1 FROM content_extractions e
             WHERE e.platform = s.platform AND e.content_id = s.content_id
               AND e.purpose = 'production'
               AND e.vocabulary_version = (SELECT max(version) FROM extraction_vocabulary_terms))"""
    ]
    params: List[Any] = []
    if platform:
        params.append(platform)
        clauses.append(f"s.platform = ${len(params)}")
    if content_type:
        params.append(content_type)
        clauses.append(f"s.content_type = ${len(params)}")
    if profile:
        params.append(profile)
        clauses.append(f"s.profile_key = ${len(params)}")

    rows = await conn.fetch(
        f"""
        SELECT s.platform, s.content_id, s.content_type, s.profile_key,
               i.drive_file_id, i.account_id
        FROM harvested_signals s
        LEFT JOIN LATERAL (
            SELECT drive_file_id, account_id FROM harvested_items
            WHERE platform = s.platform AND content_id = s.content_id
              AND drive_file_id IS NOT NULL
            ORDER BY id LIMIT 1
        ) i ON true
        WHERE {' AND '.join(clauses)}
        ORDER BY s.platform, s.content_id
        """,
        *params,
    )
    return [dict(r) for r in rows]


@flow(name="roach-extract-backfill")
async def roach_extract_backfill(
    dry_run: bool = True,
    pilot: Optional[int] = None,
    confirm: bool = False,
    platform: Optional[str] = None,
    content_type: Optional[str] = None,
    profile: Optional[str] = None,
    credentials_block_name: str = "google-creds",
    db_env: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Re-extract collected items, costed.

    Modes:
      dry_run (default)  0 model calls. Reports scope and projection, or says
                         UNCALIBRATED when no measured baseline exists.
      pilot N            N items, really extracted and measured. THIS IS the
                         baseline the later projections rest on.
      confirm            The real run over the whole selected scope.

    Never raises (constitution I).
    """
    run_logger = get_run_logger()
    mode = "dry_run" if (dry_run and not pilot and not confirm) else ("pilot" if pilot else "real")
    result: Dict[str, Any] = {
        "start_time": _now(),
        "end_time": None,
        "data": [],
        "summary": {
            "mode": mode,
            "items_in_scope": 0,
            "outcomes": {"extracted": 0, "cached": 0, "quarantined": 0,
                         "skipped": 0, "unclassified": 0},
            "projection": None,
            "ceiling": None,
            "actual_usd": 0.0,
            "scope": {"platform": platform, "content_type": content_type, "profile": profile},
        },
        "error": None,
    }
    run_id = None
    outcomes = result["summary"]["outcomes"]

    try:
        pool = await db_pool(db_env)
        try:
            async with pool.acquire() as conn:
                scope = await _select_scope(conn, platform, content_type, profile)
        finally:
            await pool.close()

        result["summary"]["items_in_scope"] = len(scope)
        scope_description = (
            "everything present when the run started, not yet extracted at the current "
            "vocabulary version"
            + (f", platform={platform}" if platform else "")
            + (f", content_type={content_type}" if content_type else "")
            + (f", profile={profile}" if profile else "")
        )
        run_logger.info(f"scope: {len(scope)} item(s) — {scope_description}")

        # --- Projection (FR-032, FR-033) -------------------------------------
        by_type: Dict[str, int] = {}
        for item in scope:
            by_type[item["content_type"]] = by_type.get(item["content_type"], 0) + 1
        projection = await extraction_cost_project(items_by_type=by_type, db_env=db_env)
        result["summary"]["projection"] = projection

        if projection["basis"] == "uncalibrated":
            run_logger.warning(
                "PROJECTED SPEND: UNCALIBRATED — %s This is the correct output, not a failure "
                "(FR-033).", projection["note"])
            if projection.get("covered_projection_usd") is not None:
                run_logger.info(
                    f"LOWER BOUND (extraction only): ${projection['covered_projection_usd']:.4f} "
                    f"across {projection['covered_item_count']} measured item(s); "
                    f"{projection['uncovered_item_count']} item(s) of type "
                    f"{', '.join(projection['uncovered_content_types'])} are NOT estimated")
        else:
            run_logger.info(
                f"PROJECTED SPEND (extraction only): ${projection['projection_usd']:.4f} "
                f"over {len(scope)} item(s), from {projection['sample_size']} measured extraction(s)")

        # --- Dry run stops here, having made zero model calls (FR-032) --------
        if mode == "dry_run":
            run_id = await extraction_batch_record_start(
                batch_kind="backfill", mode="dry_run",
                scope_description=scope_description, items_in_scope=len(scope),
                flow_name="roach-extract-backfill", projection=projection, db_env=db_env)
            outcomes["skipped"] = len(scope)
            finish = await extraction_batch_record_finish(
                run_id=run_id, outcomes=outcomes, actual_usd=0.0, db_env=db_env)
            result["summary"]["batch"] = finish
            result["summary"]["run_id"] = run_id
            run_logger.info("dry run complete — ZERO model calls made")
            return result

        # --- Gates before any spend ------------------------------------------
        # The ceiling FIRST: it is a hard stop and the cheapest thing to fail on.
        ceiling = await extraction_cost_ceiling_check(
            projection_usd=projection.get("projection_usd"), db_env=db_env)
        result["summary"]["ceiling"] = ceiling
        run_logger.info(
            f"EXTRACTION SPEND ONLY (songbird generation spend is NOT counted here): "
            f"month-to-date ${ceiling['month_to_date_usd']:.4f}, "
            f"ceiling ${ceiling['ceiling_usd']:.2f}")

        projected = projection.get("projection_usd")
        if projected is not None and projected > SPEND_THRESHOLD_USD and not confirm:
            result["error"] = (
                f"projected extraction spend ${projected:.4f} exceeds the configured threshold "
                f"${SPEND_THRESHOLD_USD:.2f}; re-run with --confirm to proceed")
            run_logger.error(result["error"])
            return result

        targets = scope[:pilot] if pilot else scope
        if pilot:
            run_logger.info(f"pilot: extracting {len(targets)} of {len(scope)} item(s) to establish a baseline")

        run_id = await extraction_batch_record_start(
            batch_kind="backfill", mode=mode, scope_description=scope_description,
            items_in_scope=len(targets), flow_name="roach-extract-backfill",
            projection=projection, ceiling=ceiling, confirmed=confirm, db_env=db_env)
        result["summary"]["run_id"] = run_id

        # --- The run ----------------------------------------------------------
        for item in targets:
            outcome = await _backfill_one(
                item, run_id=run_id, credentials_block_name=credentials_block_name,
                db_env=db_env, run_logger=run_logger)
            outcomes[outcome["outcome"]] = outcomes.get(outcome["outcome"], 0) + 1
            result["summary"]["actual_usd"] += outcome.get("cost_usd") or 0.0
            result["data"].append(outcome)

        finish = await extraction_batch_record_finish(
            run_id=run_id, outcomes=outcomes,
            actual_usd=round(result["summary"]["actual_usd"], 6), db_env=db_env)
        result["summary"]["batch"] = finish

        # SC-010: a non-zero unclassified count is the silent partial success
        # Constitution V calls the most dangerous failure mode in this system.
        if outcomes["unclassified"]:
            run_logger.error(
                f"{outcomes['unclassified']} item(s) resolved to NO classified outcome — "
                f"this is a defect, not a result")
        if not finish["fully_accounted"]:
            run_logger.error(
                f"outcome counts ({finish['items_accounted']}) do not account for every item in "
                f"scope ({finish['items_in_scope']})")

        # SC-008: actual vs projected, or the divergence with its cause.
        cmp = finish["cost_comparison"]
        if cmp.get("within_tolerance") is True:
            run_logger.info(
                f"actual ${cmp['actual_usd']:.4f} vs projected ${cmp['projection_usd']:.4f} "
                f"({cmp['delta_pct']:+.1f}%) — within ±25%")
        elif cmp.get("within_tolerance") is False:
            run_logger.warning(
                f"actual ${cmp['actual_usd']:.4f} vs projected ${cmp['projection_usd']:.4f} "
                f"({cmp['delta_pct']:+.1f}%) — OUTSIDE ±25%. Likely causes: a content-type mix "
                f"different from the baseline sample, or transcript lengths unlike those measured.")
        else:
            run_logger.info(cmp.get("note", "no calibrated projection to compare against"))

    except SpendCeilingExceeded as e:
        result["error"] = str(e)
        run_logger.error(str(e))
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"
        run_logger.error(f"backfill failed: {result['error']}")
    finally:
        result["end_time"] = _now()

    return result


async def _backfill_one(
    item: Dict[str, Any], run_id: str, credentials_block_name: str,
    db_env: Optional[str], run_logger,
) -> Dict[str, Any]:
    """
    Re-extract ONE item. Always returns a classified outcome — never a silent skip.

    Writes ONLY the new extraction tables. `harvested_signals` is not touched by
    any path reachable from here (research.md R10).
    """
    platform, content_id = item["platform"], item["content_id"]
    base = {"platform": platform, "content_id": content_id,
            "content_type": item["content_type"], "cost_usd": 0.0}

    # No retained media => a classified skip, COUNTED (FR-035). Every item carries
    # a drive_file_id, but that does not prove the Drive object still exists.
    if not item.get("drive_file_id"):
        await extraction_quarantine_record(
            platform=platform, content_id=content_id, failure_kind="media_unavailable",
            analysis={"raw_output": None, "attempts": [{"reason": "no drive_file_id recorded"}]},
            provenance={}, run_id=run_id, db_env=db_env)
        run_logger.warning(f"{platform}/{content_id}: no retained media reference — skipped")
        return {**base, "outcome": "skipped", "reason": "media_unavailable"}

    file_ids = [f for f in str(item["drive_file_id"]).split(",") if f]
    os.makedirs(SHARED_MEDIA_DIR, exist_ok=True)
    tmpdir = tempfile.mkdtemp(prefix="backfill_", dir=SHARED_MEDIA_DIR)
    local_paths: List[str] = []
    try:
        try:
            for idx, file_id in enumerate(file_ids):
                # No extension here on purpose — `drive_file_download` inherits the
                # original file's suffix from Drive metadata, which is what keeps
                # `media_path` provenance correct (FR-024).
                dest = os.path.join(tmpdir, f"{content_id}_{idx}")
                local_paths.append(await drive_file_download(
                    file_id=file_id, local_path=dest,
                    credentials_block_name=credentials_block_name))
        except FileNotFoundError as e:
            # Deletion or a permissions change. NOT a reason to re-download from
            # the platform — there is no such fallback and no code path offers one.
            await extraction_quarantine_record(
                platform=platform, content_id=content_id, failure_kind="media_unavailable",
                analysis={"raw_output": None, "attempts": [{"reason": str(e)}]},
                provenance={}, run_id=run_id, db_env=db_env)
            run_logger.warning(f"{platform}/{content_id}: retained media unreachable — {e}")
            return {**base, "outcome": "skipped", "reason": "media_unavailable"}

        analysis = await social_item_analyze(
            content_id, local_paths, item["content_type"], client="backfill")
        usage = analysis.get("usage") or {}
        provenance = analysis.get("provenance") or {}
        cost = float(usage.get("cost_usd") or 0.0)

        if analysis.get("status") == "success":
            extraction_id = await extraction_record_store(
                platform=platform, content_id=content_id, content_type=item["content_type"],
                analysis=analysis, provenance=provenance, usage=usage,
                content_hash=_hash_files(local_paths), purpose="production",
                account_id=item.get("account_id"), run_id=run_id, db_env=db_env)
            if extraction_id is None:
                # uq_extraction_version refused it — already present at this
                # version. Charged once, recorded once.
                return {**base, "outcome": "cached", "cost_usd": cost}
            return {**base, "outcome": "extracted", "cost_usd": cost}

        failure_kind = analysis.get("failure_kind") or "provider_error"
        await extraction_quarantine_record(
            platform=platform, content_id=content_id, failure_kind=failure_kind,
            analysis=analysis, provenance=provenance, usage=usage,
            run_id=run_id, db_env=db_env)
        run_logger.warning(f"{platform}/{content_id}: {analysis.get('status')} — {failure_kind}")
        return {**base, "outcome": "quarantined", "reason": failure_kind, "cost_usd": cost}

    finally:
        for p in local_paths:
            try:
                Path(p).unlink(missing_ok=True)
            except OSError:
                pass
        try:
            os.rmdir(tmpdir)
        except OSError:
            pass


def _hash_files(paths: List[str]) -> str:
    """Content-hash the media actually sent (FR-039), matching the forward path."""
    import hashlib

    digest = hashlib.sha256()
    try:
        for path in sorted(paths):
            with open(path, "rb") as fh:
                for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                    digest.update(chunk)
        return f"sha256:{digest.hexdigest()}"
    except OSError:
        return ""


def _parse_args():
    p = argparse.ArgumentParser(description="Re-extract collected items under the current schema")
    p.add_argument("--dry-run", action="store_true", default=False,
                   help="report scope and projected spend; makes ZERO model calls")
    p.add_argument("--pilot", type=int, help="really extract N items to establish a cost baseline")
    p.add_argument("--confirm", action="store_true",
                   help="proceed with a real run above the spend threshold")
    p.add_argument("--platform", choices=["instagram", "tiktok"])
    p.add_argument("--content-type", help="e.g. video, image, carousel")
    p.add_argument("--profile", help="restrict to one profile_key")
    p.add_argument("--db-env", help="env var holding an alternate DSN (e.g. SPINE_DB_URL)")
    args = p.parse_args()
    # Dry run is the DEFAULT, not an opt-in: nothing spends money unless asked.
    if not args.pilot and not args.confirm:
        args.dry_run = True
    return args


if __name__ == "__main__":
    args = _parse_args()
    outcome = asyncio.run(roach_extract_backfill(
        dry_run=args.dry_run, pilot=args.pilot, confirm=args.confirm,
        platform=args.platform, content_type=args.content_type, profile=args.profile,
        db_env=args.db_env,
    ))
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    path = os.path.join(OUTPUT_DIR, f"roach_extract_backfill_{stamp}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(outcome, f, indent=2, ensure_ascii=False, default=str)
    print(json.dumps(outcome["summary"], indent=2, default=str))
    print(f"Full result saved to {path}")
    sys.exit(1 if outcome.get("error") else 0)
