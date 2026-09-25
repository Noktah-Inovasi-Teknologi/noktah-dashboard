"""
roach-extract-calibrate — measure whether the two models actually agree.

Video is analysed by one model and images by another. Reading their distributions
side by side cannot separate a model effect from the fact that video and image
content genuinely differ, so agreement is established by running a FIXED SAMPLE
through BOTH models on IDENTICAL INPUT (FR-027).

⚠️ COSTS MONEY, and roughly double per item — every sampled item is extracted
twice. On backfill's exact terms (FR-027e): dry-run projection, threshold
confirmation, hard monthly ceiling, measured cost recorded.

Three refusals built in, each preventing a figure that would look like evidence
and not be one:

  * **Same model on both paths** -> reports that no cross-model comparison
    applies and makes ZERO calls. `IMAGE_MODEL` falls back to `MODEL` when unset,
    so this is one env var away from being the default configuration, and the
    trivial 100% it would otherwise produce is worse than no number (FR-027d).
  * **Below the minimum sample** -> reports insufficient data and emits NO rate.
  * **Video** -> named as NOT COVERED in the same report as the number, because
    the image model cannot accept it. A rate quoted without that reads as a
    property of the system rather than of one media class (FR-027a).

Calibration extractions are stored with `purpose = 'calibration'` and are excluded
from every analytical figure (FR-027c) — otherwise every distribution over the
sample would be double-weighted.

Run:
    docker exec prefect python flows/roach_extract_calibrate.py --dry-run
    docker exec prefect python flows/roach_extract_calibrate.py --sample image_overlap_v1 --confirm
    docker exec prefect python flows/roach_extract_calibrate.py --report
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
    from ..tasks.calibration_tasks import (
        CALIBRATION_MIN_SAMPLE,
        calibration_agreement_compute,
        calibration_sample_populate,
    )
    from ..tasks.extraction_tasks import (
        SpendCeilingExceeded,
        SPEND_THRESHOLD_USD,
        extraction_batch_record_finish,
        extraction_batch_record_start,
        extraction_cost_ceiling_check,
        extraction_cost_project,
        extraction_quarantine_record,
        extraction_record_store,
    )
    from ..tasks.google_tasks import drive_file_download
    from ..tasks.social_tasks import social_extraction_config, social_item_analyze
except ImportError:
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from db import db_pool
    from tasks.calibration_tasks import (
        CALIBRATION_MIN_SAMPLE,
        calibration_agreement_compute,
        calibration_sample_populate,
    )
    from tasks.extraction_tasks import (
        SpendCeilingExceeded,
        SPEND_THRESHOLD_USD,
        extraction_batch_record_finish,
        extraction_batch_record_start,
        extraction_cost_ceiling_check,
        extraction_cost_project,
        extraction_quarantine_record,
        extraction_record_store,
    )
    from tasks.google_tasks import drive_file_download
    from tasks.social_tasks import social_extraction_config, social_item_analyze

try:
    from .common.alerts import alert_hooks
except ImportError:
    sys.path.append(os.path.dirname(os.path.abspath(__file__)))
    from common.alerts import alert_hooks

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")

# Same reason as roach_extract_backfill: roach is a SEPARATE container and takes a
# path, not bytes. Staging under this process's /tmp gives it a path that does not
# exist on its side. `social_data` is mounted at /data in both.
SHARED_MEDIA_DIR = os.environ.get("HARVEST_SHARED_DIR", "/data")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


async def _configured_models() -> tuple:
    """Ask ROACH which models it routes to. Never guess from this process's env.

    `OPENROUTER_IMAGE_MODEL` lives in `service/roach/.env`, which the Prefect
    containers do not load. An earlier version of this function read the local
    environment and reported "no cross-model comparison applies" — while roach was
    demonstrably serving images with `google/gemini-2.5-flash-lite` and video with
    `xiaomi/mimo-v2.5`. That is a false negative that silently skips a real
    measurement, and it is exactly the shape of quiet wrong answer this feature
    exists to remove.
    """
    config = await social_extraction_config()
    return config["video_model"], config["image_model"]


@flow(name="roach-extract-calibrate", **alert_hooks())
async def roach_extract_calibrate(
    sample: str = "image_overlap_v1",
    dry_run: bool = True,
    confirm: bool = False,
    report: bool = False,
    limit: Optional[int] = None,
    credentials_block_name: str = "google-creds",
    db_env: Optional[str] = None,
) -> Dict[str, Any]:
    """Controlled two-model comparison over a fixed sample. Never raises."""
    run_logger = get_run_logger()
    model_a, model_b = await _configured_models()
    result: Dict[str, Any] = {
        "start_time": _now(),
        "end_time": None,
        "data": [],
        "summary": {
            "sample_key": sample,
            "model_a": model_a,
            "model_b": model_b,
            "cross_model_comparison_applies": model_a != model_b,
            "outcomes": {"extracted": 0, "cached": 0, "quarantined": 0,
                         "skipped": 0, "unclassified": 0},
            "actual_usd": 0.0,
        },
        "error": None,
    }
    outcomes = result["summary"]["outcomes"]

    try:
        # --- Refusal 1: the two paths are the same model (FR-027d) ------------
        # ZERO model calls. `IMAGE_MODEL` falls back to `MODEL` when unset, so
        # this is the DEFAULT configuration, not an exotic case — and a trivial
        # 100% agreement reported as a finding would be worse than no finding.
        if model_a == model_b:
            result["summary"]["note"] = (
                f"NO CROSS-MODEL COMPARISON APPLIES: both paths are configured to {model_a!r}. "
                f"Set OPENROUTER_IMAGE_MODEL to a different model to make this measurable. "
                f"Zero model calls were made; reporting a 100% agreement here would describe the "
                f"configuration, not the models (FR-027d)."
            )
            run_logger.warning(result["summary"]["note"])
            return result

        # --- Reporting only: no spend ----------------------------------------
        if report:
            agreement = await calibration_agreement_compute(
                sample_key=sample, model_a=model_a, model_b=model_b, db_env=db_env)
            result["summary"]["agreement"] = agreement
            result["summary"]["uncovered"] = await _uncovered_report(sample, db_env)
            _log_report(run_logger, agreement, result["summary"]["uncovered"])
            return result

        populated = await calibration_sample_populate(
            sample_key=sample, limit=limit, db_env=db_env)
        result["summary"]["sample"] = populated
        run_logger.info(
            f"sample {sample!r}: {populated['members']} member(s) "
            f"across {populated['media_classes']}; {populated['excluded_note']}")

        pool = await db_pool(db_env)
        try:
            async with pool.acquire() as conn:
                targets = [dict(r) for r in await conn.fetch(
                    """
                    SELECT m.platform, m.content_id, m.media_class AS content_type,
                           i.drive_file_id, i.account_id
                    FROM calibration_sample_members m
                    LEFT JOIN LATERAL (
                        SELECT drive_file_id, account_id FROM harvested_items
                        WHERE platform = m.platform AND content_id = m.content_id
                          AND drive_file_id IS NOT NULL ORDER BY id LIMIT 1
                    ) i ON true
                    WHERE m.sample_key = $1 ORDER BY m.platform, m.content_id
                    """, sample)]
        finally:
            await pool.close()

        # Every item is extracted TWICE, so the projection is doubled.
        by_type: Dict[str, int] = {}
        for t in targets:
            by_type[t["content_type"]] = by_type.get(t["content_type"], 0) + 2
        projection = await extraction_cost_project(items_by_type=by_type, db_env=db_env)
        result["summary"]["projection"] = projection
        run_logger.info(
            f"projection (each item extracted twice): {projection['note']}"
            if projection["basis"] == "uncalibrated"
            else f"PROJECTED SPEND (extraction only): ${projection['projection_usd']:.4f}")

        if dry_run and not confirm:
            run_id = await extraction_batch_record_start(
                batch_kind="calibration", mode="dry_run",
                scope_description=f"calibration sample {sample!r}, each item extracted twice",
                items_in_scope=len(targets) * 2, flow_name="roach-extract-calibrate",
                projection=projection, db_env=db_env)
            outcomes["skipped"] = len(targets) * 2
            result["summary"]["batch"] = await extraction_batch_record_finish(
                run_id=run_id, outcomes=outcomes, actual_usd=0.0, db_env=db_env)
            run_logger.info("dry run complete — ZERO model calls made")
            return result

        # --- Gates, on backfill's exact terms (FR-027e) ----------------------
        ceiling = await extraction_cost_ceiling_check(
            projection_usd=projection.get("projection_usd"), db_env=db_env)
        result["summary"]["ceiling"] = ceiling
        run_logger.info(
            f"EXTRACTION SPEND ONLY (songbird generation spend is NOT counted here): "
            f"month-to-date ${ceiling['month_to_date_usd']:.4f}, ceiling ${ceiling['ceiling_usd']:.2f}")

        projected = projection.get("projection_usd")
        if projected is not None and projected > SPEND_THRESHOLD_USD and not confirm:
            result["error"] = (
                f"projected extraction spend ${projected:.4f} exceeds the threshold "
                f"${SPEND_THRESHOLD_USD:.2f}; re-run with --confirm")
            run_logger.error(result["error"])
            return result

        run_id = await extraction_batch_record_start(
            batch_kind="calibration", mode="real",
            scope_description=f"calibration sample {sample!r}, each item extracted twice",
            items_in_scope=len(targets) * 2, flow_name="roach-extract-calibrate",
            projection=projection, ceiling=ceiling, confirmed=confirm, db_env=db_env)
        result["summary"]["run_id"] = run_id

        for item in targets:
            for model in (model_a, model_b):
                outcome = await _calibrate_one(
                    item, model=model, run_id=run_id,
                    credentials_block_name=credentials_block_name,
                    db_env=db_env, run_logger=run_logger)
                outcomes[outcome["outcome"]] = outcomes.get(outcome["outcome"], 0) + 1
                result["summary"]["actual_usd"] += outcome.get("cost_usd") or 0.0
                result["data"].append(outcome)

        result["summary"]["batch"] = await extraction_batch_record_finish(
            run_id=run_id, outcomes=outcomes,
            actual_usd=round(result["summary"]["actual_usd"], 6), db_env=db_env)

        agreement = await calibration_agreement_compute(
            sample_key=sample, model_a=model_a, model_b=model_b, db_env=db_env)
        result["summary"]["agreement"] = agreement
        result["summary"]["uncovered"] = await _uncovered_report(sample, db_env)
        _log_report(run_logger, agreement, result["summary"]["uncovered"])

    except SpendCeilingExceeded as e:
        result["error"] = str(e)
        run_logger.error(str(e))
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"
        run_logger.error(f"calibration failed: {result['error']}")
    finally:
        result["end_time"] = _now()

    return result


async def _uncovered_report(sample: str, db_env: Optional[str]) -> Dict[str, Any]:
    """Name the media classes the measurement does NOT cover (FR-027a, SC-017).

    Reported in the SAME output as the number. A rate quoted without this reads
    as a property of the system rather than of one media class.
    """
    pool = await db_pool(db_env)
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT s.content_type, count(*) AS n FROM harvested_signals s "
                "WHERE NOT EXISTS (SELECT 1 FROM calibration_sample_members m "
                "                  WHERE m.sample_key = $1 AND m.platform = s.platform "
                "                    AND m.content_id = s.content_id) "
                "GROUP BY 1 ORDER BY 2 DESC", sample)
    finally:
        await pool.close()
    return {
        "uncovered_content_types": [dict(r) for r in rows],
        "note": (
            "The measured agreement does NOT extend to these media classes, and NO agreement "
            "figure is asserted for them. The image model cannot accept video, so the overlap is "
            "one-directional by construction — this limit is reported, not worked around, because "
            "closing it would mean sending video to a model that cannot receive it (FR-027a)."
        ),
    }


def _log_report(run_logger, agreement: Dict[str, Any], uncovered: Dict[str, Any]) -> None:
    if not agreement.get("sufficient"):
        run_logger.warning(agreement.get("note", "insufficient data"))
    else:
        for row in agreement["beat_function_agreement"]:
            run_logger.info(
                f"beat position {row['position']}: agreement {row['agreement_rate']:.1%} "
                f"(n={row['observations']})")
        for row in agreement["attribute_agreement"]:
            run_logger.info(
                f"dimension {row['dimension']}: agreement {row['agreement_rate']:.1%} "
                f"(n={row['observations']})")
        for d in agreement["disagreements"][:10]:
            run_logger.info(f"disagreement {d['direction']} x{d['count']}")
    for row in uncovered["uncovered_content_types"]:
        run_logger.warning(
            f"NOT COVERED: {row['n']} {row['content_type']} item(s) — no agreement figure applies")


async def _calibrate_one(
    item: Dict[str, Any], model: str, run_id: str, credentials_block_name: str,
    db_env: Optional[str], run_logger,
) -> Dict[str, Any]:
    """Extract one sampled item under one model, stored as `purpose='calibration'`."""
    platform, content_id = item["platform"], item["content_id"]
    base = {"platform": platform, "content_id": content_id, "model": model, "cost_usd": 0.0}

    if not item.get("drive_file_id"):
        await extraction_quarantine_record(
            platform=platform, content_id=content_id, failure_kind="media_unavailable",
            analysis={"raw_output": None, "attempts": [{"reason": "no drive_file_id"}]},
            provenance={}, purpose="calibration", run_id=run_id, db_env=db_env)
        return {**base, "outcome": "skipped", "reason": "media_unavailable"}

    os.makedirs(SHARED_MEDIA_DIR, exist_ok=True)
    tmpdir = tempfile.mkdtemp(prefix="calibrate_", dir=SHARED_MEDIA_DIR)
    local_paths: List[str] = []
    try:
        try:
            for idx, file_id in enumerate(
                    [f for f in str(item["drive_file_id"]).split(",") if f]):
                # Extensionless on purpose — the download inherits Drive's suffix.
                dest = os.path.join(tmpdir, f"{content_id}_{idx}")
                local_paths.append(await drive_file_download(
                    file_id=file_id, local_path=dest,
                    credentials_block_name=credentials_block_name))
        except FileNotFoundError as e:
            await extraction_quarantine_record(
                platform=platform, content_id=content_id, failure_kind="media_unavailable",
                analysis={"raw_output": None, "attempts": [{"reason": str(e)}]},
                provenance={}, purpose="calibration", run_id=run_id, db_env=db_env)
            return {**base, "outcome": "skipped", "reason": "media_unavailable"}

        # IDENTICAL INPUT to both models — same media, same prompt, same schema
        # version. That is what makes this a controlled comparison rather than
        # two observations that happen to be adjacent.
        # `model=` FORCES this model, bypassing media-based routing. Without it
        # the comparison is a fiction: routing picks the model from the media
        # type, so both halves of every pair resolve to the SAME model and the
        # "agreement" describes the router. Measured before this was added — 39
        # of 40 pairs had both sides served by gemini, and only the model_served
        # mismatch check stopped a meaningless 100% being reported (FR-027).
        analysis = await social_item_analyze(
            content_id, local_paths, item["content_type"],
            client=f"calibration:{model}", model=model)
        usage = analysis.get("usage") or {}
        provenance = dict(analysis.get("provenance") or {})
        provenance["model_requested"] = model
        cost = float(usage.get("cost_usd") or 0.0)

        if analysis.get("status") == "success":
            stored = await extraction_record_store(
                platform=platform, content_id=content_id, content_type=item["content_type"],
                analysis=analysis, provenance=provenance, usage=usage,
                content_hash="", purpose="calibration",
                account_id=item.get("account_id"), run_id=run_id, db_env=db_env)
            return {**base, "outcome": "extracted" if stored else "cached", "cost_usd": cost}

        await extraction_quarantine_record(
            platform=platform, content_id=content_id,
            failure_kind=analysis.get("failure_kind") or "provider_error",
            analysis=analysis, provenance=provenance, usage=usage,
            purpose="calibration", run_id=run_id, db_env=db_env)
        return {**base, "outcome": "quarantined", "cost_usd": cost}
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


def _parse_args():
    p = argparse.ArgumentParser(description="Controlled two-model agreement measurement")
    p.add_argument("--sample", default="image_overlap_v1", help="calibration sample key")
    p.add_argument("--dry-run", action="store_true", default=False)
    p.add_argument("--confirm", action="store_true", help="proceed with a real, costed run")
    p.add_argument("--report", action="store_true",
                   help="compute agreement from stored extractions; makes ZERO model calls")
    p.add_argument("--limit", type=int, help="cap sample membership (deterministic order)")
    p.add_argument("--db-env")
    args = p.parse_args()
    if not args.confirm and not args.report:
        args.dry_run = True
    return args


if __name__ == "__main__":
    args = _parse_args()
    outcome = asyncio.run(roach_extract_calibrate(
        sample=args.sample, dry_run=args.dry_run, confirm=args.confirm,
        report=args.report, limit=args.limit, db_env=args.db_env,
    ))
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    path = os.path.join(OUTPUT_DIR, f"roach_extract_calibrate_{stamp}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(outcome, f, indent=2, ensure_ascii=False, default=str)
    print(json.dumps(outcome["summary"], indent=2, default=str))
    print(f"Full result saved to {path}")
    sys.exit(1 if outcome.get("error") else 0)
