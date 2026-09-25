"""
Controlled model-agreement tasks (feature 007-structured-extraction).

Video items are analysed by one model and image items by another. If those two
label content differently then every cross-format comparison in the system is
measuring the model, not the content.

Reading the two paths' distributions side by side CANNOT tell them apart, because
video and image content genuinely differ — a Reel really does open differently
from a carousel. So agreement is established by running a FIXED SAMPLE through
BOTH models on IDENTICAL INPUT and measuring how often they agree (FR-027). That
is the only comparison that isolates the model.

Three properties this module exists to preserve:

  * The sample is FIXED and re-runnable (FR-027b). A LIMIT or a random draw would
    mean a changed agreement rate could be the new model or the new sample, and
    no reading could tell them apart.
  * Agreement is COMPUTED, never stored. A stored rate could silently disagree
    with the extractions it summarises, and re-deriving it after a vocabulary
    change would need a migration.
  * Disagreements are ENUMERATED WITH DIRECTION, never reduced to one averaged
    score (FR-026). Neither model is ground truth — there is no "accuracy" here,
    only agreement.
"""
import logging
import os
from typing import Any, Dict, List, Optional

from prefect import task

try:
    from ..db import db_pool
except ImportError:
    import sys
    sys.path.append(os.path.dirname(os.path.dirname(__file__)))
    from db import db_pool

logger = logging.getLogger(__name__)


async def _db_pool(db_env: Optional[str] = None):
    return await db_pool(db_env)


# Below this many USABLE PAIRS, no agreement figure is reported at all — not a
# weak one, not a provisional one (Constitution VIII, FR-027e). Deliberately NOT
# overridable by a flag: a minimum sample that can be waived from the command
# line is a suggestion, and Constitution VIII requires it be structural.
CALIBRATION_MIN_SAMPLE = int(os.environ.get("CALIBRATION_MIN_SAMPLE", "30"))

# The media classes both models can accept. Video is excluded because the image
# model cannot take it — which makes the whole measurement one-directional, a
# limit that is REPORTED rather than worked around (FR-027a). Closing it would
# mean sending video to a model that cannot receive it.
OVERLAP_MEDIA_CLASSES = ("image", "carousel", "story")


@task(name="calibration.sample.populate", retries=0)
async def calibration_sample_populate(
    sample_key: str = "image_overlap_v1",
    media_classes: tuple = OVERLAP_MEDIA_CLASSES,
    limit: Optional[int] = None,
    db_env: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Fix the membership of a calibration sample, once.

    Idempotent by primary key: re-running adds any newly-eligible items but never
    removes or reshuffles existing members, so a re-measurement after a model
    change compares THE SAME ITEMS (FR-027b).

    `limit` exists for a first small run and is applied by a DETERMINISTIC order
    (platform, content_id), never at random — a random draw would make a changed
    agreement rate ambiguous between "the model changed" and "the sample changed".
    """
    pool = await _db_pool(db_env)
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT platform, content_id, content_type FROM harvested_signals "
                "WHERE content_type = ANY($1::text[]) ORDER BY platform, content_id"
                + (f" LIMIT {int(limit)}" if limit else ""),
                list(media_classes),
            )
            added = 0
            for r in rows:
                status = await conn.fetchval(
                    "INSERT INTO calibration_sample_members "
                    "(sample_key, platform, content_id, media_class) VALUES ($1,$2,$3,$4) "
                    "ON CONFLICT (sample_key, platform, content_id) DO NOTHING RETURNING 1",
                    sample_key, r["platform"], r["content_id"], r["content_type"],
                )
                added += 1 if status else 0

            total = await conn.fetchval(
                "SELECT count(*) FROM calibration_sample_members WHERE sample_key = $1",
                sample_key)
            excluded = await conn.fetchval(
                "SELECT count(*) FROM harvested_signals WHERE NOT (content_type = ANY($1::text[]))",
                list(media_classes))
            return {
                "sample_key": sample_key,
                "members": total,
                "added": added,
                "media_classes": list(media_classes),
                "excluded_items": excluded,
                "excluded_note": (
                    f"{excluded} item(s) are outside this sample because only one model can "
                    f"accept their media. The agreement measured here does NOT extend to them "
                    f"(FR-027a)."
                ),
            }
    finally:
        await pool.close()


@task(name="calibration.agreement.compute", retries=0)
async def calibration_agreement_compute(
    sample_key: str = "image_overlap_v1",
    model_a: Optional[str] = None,
    model_b: Optional[str] = None,
    min_sample: Optional[int] = None,
    db_env: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Compute agreement from the STORED calibration extractions. Never stores a rate.

    Reported per beat-function POSITION and per attribute DIMENSION, each with its
    own observation count (FR-027). Disagreements are enumerated with direction.

    Pairs whose `model_served` does not match the intended pair are EXCLUDED and
    counted (FR-027e): provider routing runs with allow_fallbacks, so a pair can
    be served by some third model, in which case the comparison is not between the
    two models it claims to compare.
    """
    floor = CALIBRATION_MIN_SAMPLE if min_sample is None else min_sample
    pool = await _db_pool(db_env)
    try:
        async with pool.acquire() as conn:
            members = await conn.fetch(
                "SELECT platform, content_id, media_class FROM calibration_sample_members "
                "WHERE sample_key = $1 ORDER BY platform, content_id", sample_key)

            extractions = await conn.fetch(
                """
                SELECT e.platform, e.content_id, e.model_requested, e.model_served,
                       e.prompt_version, e.schema_version, e.vocabulary_version, e.id
                FROM content_extractions e
                JOIN calibration_sample_members m
                  ON m.platform = e.platform AND m.content_id = e.content_id
                WHERE m.sample_key = $1 AND e.purpose = 'calibration'
                """,
                sample_key,
            )

            by_item: Dict[tuple, List[Dict[str, Any]]] = {}
            for r in extractions:
                by_item.setdefault((r["platform"], r["content_id"]), []).append(dict(r))

            beats = await conn.fetch(
                """
                SELECT b.extraction_id, b.position, b.function
                FROM extraction_beats b
                JOIN content_extractions e ON e.id = b.extraction_id
                JOIN calibration_sample_members m
                  ON m.platform = e.platform AND m.content_id = e.content_id
                WHERE m.sample_key = $1 AND e.purpose = 'calibration'
                ORDER BY b.position
                """,
                sample_key,
            )
            beats_by_extraction: Dict[Any, Dict[int, str]] = {}
            for b in beats:
                beats_by_extraction.setdefault(b["extraction_id"], {})[b["position"]] = b["function"]

            attrs = await conn.fetch(
                """
                SELECT a.extraction_id, a.dimension, a.value
                FROM extraction_attributes a
                JOIN content_extractions e ON e.id = a.extraction_id
                JOIN calibration_sample_members m
                  ON m.platform = e.platform AND m.content_id = e.content_id
                WHERE m.sample_key = $1 AND e.purpose = 'calibration'
                """,
                sample_key,
            )
            attrs_by_extraction: Dict[Any, Dict[str, str]] = {}
            for a in attrs:
                attrs_by_extraction.setdefault(a["extraction_id"], {})[a["dimension"]] = a["value"]
    finally:
        await pool.close()

    usable: List[tuple] = []
    excluded_fallback = 0
    excluded_incomplete = 0
    versions: set = set()

    for key, rows in by_item.items():
        if len(rows) < 2:
            excluded_incomplete += 1
            continue
        a = next((r for r in rows if not model_a or r["model_requested"] == model_a), None)
        b = next((r for r in rows if r is not a and (not model_b or r["model_requested"] == model_b)), None)
        if a is None or b is None:
            excluded_incomplete += 1
            continue
        # allow_fallbacks means "what we asked for" and "what answered" differ.
        # A pair served by some third model is not the comparison this claims.
        if (model_a and a["model_served"] != model_a) or (model_b and b["model_served"] != model_b):
            excluded_fallback += 1
            continue
        usable.append((a, b))
        versions.add((a["prompt_version"], a["schema_version"], a["vocabulary_version"]))
        versions.add((b["prompt_version"], b["schema_version"], b["vocabulary_version"]))

    result: Dict[str, Any] = {
        "sample_key": sample_key,
        "model_a": model_a,
        "model_b": model_b,
        "sample_members": len(members),
        "usable_pairs": len(usable),
        "excluded_provider_fallback": excluded_fallback,
        "excluded_incomplete_pairs": excluded_incomplete,
        "media_classes_covered": sorted({m["media_class"] for m in members}),
        "versions_measured_under": sorted(str(v) for v in versions),
        "min_sample": floor,
    }

    # Below the floor: report insufficient data and emit NO rate. Not a weak one.
    if len(usable) < floor:
        result["sufficient"] = False
        result["beat_function_agreement"] = None
        result["attribute_agreement"] = None
        result["disagreements"] = None
        result["note"] = (
            f"INSUFFICIENT DATA: {len(usable)} usable pair(s) is below the configured minimum of "
            f"{floor}. No agreement rate is reported — a figure from this few pairs would carry "
            f"more apparent authority than evidence (Constitution VIII). This threshold is not "
            f"overridable from the command line."
        )
        return result

    # --- per beat-function position ---
    position_stats: Dict[int, Dict[str, int]] = {}
    disagreements: Dict[str, int] = {}
    for a, b in usable:
        ba = beats_by_extraction.get(a["id"], {})
        bb = beats_by_extraction.get(b["id"], {})
        for position in sorted(set(ba) | set(bb)):
            fa, fb = ba.get(position), bb.get(position)
            stats = position_stats.setdefault(position, {"n": 0, "agree": 0})
            stats["n"] += 1
            if fa == fb and fa is not None:
                stats["agree"] += 1
            else:
                # DIRECTION is retained: "A=hook B=setup" is a finding; "they
                # disagreed 9 times" is not.
                disagreements[f"A={fa} B={fb}"] = disagreements.get(f"A={fa} B={fb}", 0) + 1

    result["beat_function_agreement"] = [
        {"position": p, "observations": s["n"], "agreements": s["agree"],
         "agreement_rate": round(s["agree"] / s["n"], 3) if s["n"] else None}
        for p, s in sorted(position_stats.items())
    ]

    # --- per attribute dimension ---
    dim_stats: Dict[str, Dict[str, int]] = {}
    for a, b in usable:
        aa = attrs_by_extraction.get(a["id"], {})
        ab = attrs_by_extraction.get(b["id"], {})
        for dim in sorted(set(aa) | set(ab)):
            va, vb = aa.get(dim), ab.get(dim)
            stats = dim_stats.setdefault(dim, {"n": 0, "agree": 0})
            stats["n"] += 1
            if va == vb and va is not None:
                stats["agree"] += 1
            else:
                disagreements[f"{dim}: A={va} B={vb}"] = (
                    disagreements.get(f"{dim}: A={va} B={vb}", 0) + 1)

    result["attribute_agreement"] = [
        {"dimension": d, "observations": s["n"], "agreements": s["agree"],
         "agreement_rate": round(s["agree"] / s["n"], 3) if s["n"] else None}
        for d, s in sorted(dim_stats.items())
    ]

    result["sufficient"] = True
    # Sorted by frequency: the enumeration IS the finding, not a footnote to a
    # single blended score (FR-026).
    result["disagreements"] = [
        {"direction": k, "count": v}
        for k, v in sorted(disagreements.items(), key=lambda kv: -kv[1])
    ]
    result["note"] = (
        "Neither model is treated as ground truth. There is no 'accuracy' here, only agreement: "
        "a disagreement says the two models labelled the same input differently, not that either "
        "is wrong."
    )
    return result
