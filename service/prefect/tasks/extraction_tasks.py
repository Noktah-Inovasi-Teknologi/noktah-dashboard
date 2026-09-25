"""
Structured extraction storage tasks (feature 007-structured-extraction).

Turns a validated roach response into rows, records what failed, and answers the
questions the feature exists to answer — residual share, quarantine counts,
per-path parity, cost projection, and the monthly ceiling.

WHY THIS IS ITS OWN MODULE, not part of social_tasks.py: nothing here performs
collection or platform I/O. Constitution II's retry carve-out distinguishes
collection tasks (exponential backoff, circuit breaker, never rapid retry against
a throttling target) from everything else, and mixing the two blurs a rule that
has to stay sharp. Feature 006 made the same split for velocity_tasks.py.

⚠️ THIS MODULE MUST NEVER IMPORT THE SIGNAL WRITER.
`social.signal.record` is the natural-looking thing to reuse from a backfill — it
is the function that already knows how to store an extraction result. It also
overwrites `subtitle`, and `harvested_signals` holds exactly ONE row per item, so
that overwrite is unrecoverable: a re-extraction cannot restore the transcript it
replaced, only produce a third different one, at cost. Backfill writes a strict
SUBSET of what the forward path writes (research.md R10, FR-009). A static import
guard in tests/test_extraction_store.py asserts this line stays true.
"""
import logging
import os
from datetime import datetime, timezone
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
    """The module-local pool seam every task module here exposes (see availability_tasks)."""
    return await db_pool(db_env)


# The closed failure vocabulary (FR-021), mirrored by chk_quarantine_failure_kind
# in migration 009. Validated here for a readable error and there so a bad value
# is impossible to store even if this task is bypassed.
#
# There is deliberately NO 'other'. An escape hatch would quietly absorb exactly
# the novel failures worth noticing, and this is what FR-020's counts group by.
FAILURE_KINDS = frozenset({
    "schema_invalid",
    "truncated",
    "empty_content",
    "out_of_vocabulary",
    "provider_error",
    "rate_limited",
    "media_unavailable",
    "unsupported_media",
    "spend_ceiling_reached",
})

# Ordered, not continuous (Constitution VI). Rank is defined HERE because
# `extraction_attributes.confidence` is a CHECK'd text column with no stored
# ordering — "minimum confidence" (FR-014) needs one and this is its definition.
CONFIDENCE_RANK = {"low": 1, "medium": 2, "high": 3}

# Per-run threshold (FR-037) and the monthly hard ceiling (FR-037a). Env, not
# flags: a per-run override would make the ceiling a formality.
SPEND_THRESHOLD_USD = float(os.environ.get("EXTRACTION_SPEND_THRESHOLD_USD", "5.00"))
MONTHLY_CEILING_USD = float(os.environ.get("EXTRACTION_MONTHLY_CEILING_USD", "25.00"))


class SpendCeilingExceeded(RuntimeError):
    """The monthly extraction ceiling would be exceeded. A HARD STOP, not a warning.

    Constitution XI requires the ceiling be *enforced*, not merely reported.
    """


def render_beats(beats: List[Dict[str, Any]]) -> str:
    """Beats -> readable numbered text (FR-041).

    Deterministic and model-free: Constitution XI requires anything a rule can
    produce not be produced by a model call. Defined here as well as in roach
    because backfill renders from STORED beats, with no roach response in hand.
    """
    return "\n".join(
        f"{b['position']}. [{b['function']}] {b['description']}"
        for b in sorted(beats, key=lambda b: b["position"])
    )


@task(name="extraction.flow.render", retries=0)
def extraction_flow_render(beats: List[Dict[str, Any]]) -> str:
    """Task wrapper for `render_beats`, so a flow can call it in-graph."""
    return render_beats(beats)


@task(name="extraction.record.store", retries=0)
async def extraction_record_store(
    platform: str,
    content_id: str,
    content_type: str,
    analysis: Dict[str, Any],
    provenance: Dict[str, Any],
    usage: Optional[Dict[str, Any]] = None,
    content_hash: str = "",
    purpose: str = "production",
    account_id: Optional[str] = None,
    run_id: Optional[str] = None,
    db_env: Optional[str] = None,
) -> Optional[str]:
    """
    Write one validated extraction plus its beats and attributes, in ONE transaction.

    Re-validates at the storage boundary before writing. FR-016 says no code path
    that stores unvalidated output may exist — roach validating is necessary but
    not sufficient, because this task is reachable from backfill and calibration
    too, and "the caller already checked" is exactly the assumption that lets an
    unchecked path appear later.

    Returns the extraction id, or None when an identical extraction already
    exists at this version (FR-036's idempotence, enforced by
    uq_extraction_version — the conflict happens in the database, so a bug in a
    caller's skip logic cannot cause a double write).
    """
    beats = analysis.get("beats") or []
    if not beats:
        # FR-003. Reaching here means validation was skipped somewhere upstream.
        raise ValueError(
            f"{platform}/{content_id}: an extraction must carry at least one beat; "
            f"content with no discernible structure is ONE residual beat, not zero")

    positions = sorted(b["position"] for b in beats)
    if positions != list(range(1, len(positions) + 1)):
        raise ValueError(
            f"{platform}/{content_id}: beat positions must be contiguous from 1, got {positions}")

    subtitle = analysis.get("subtitle")
    absence = analysis.get("subtitle_absence")
    if subtitle is None and not absence:
        raise ValueError(
            f"{platform}/{content_id}: a null subtitle requires a recorded reason (FR-010)")

    vocabulary_version = provenance.get("vocabulary_version")
    usage = usage or {}

    pool = await _db_pool(db_env)
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                extraction_id = await conn.fetchval(
                    """
                    INSERT INTO content_extractions
                        (platform, content_id, account_id, run_id, purpose, content_type,
                         media_path, subtitle, subtitle_absence, summary, model_requested,
                         model_served, provider, prompt_version, schema_version,
                         vocabulary_version, content_hash, prompt_tokens, completion_tokens,
                         cost_usd, attempts)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21)
                    ON CONFLICT (platform, content_id, purpose, model_requested,
                                 prompt_version, schema_version, vocabulary_version)
                    DO NOTHING
                    RETURNING id
                    """,
                    platform, content_id, account_id, run_id, purpose, content_type,
                    provenance.get("media_path"), subtitle, absence, analysis.get("summary"),
                    provenance.get("model_requested"), provenance.get("model_served"),
                    provenance.get("provider"), provenance.get("prompt_version"),
                    provenance.get("schema_version"), vocabulary_version, content_hash,
                    usage.get("prompt_tokens"), usage.get("completion_tokens"),
                    usage.get("cost_usd"), provenance.get("attempts", 1),
                )
                if extraction_id is None:
                    logger.info(
                        "%s/%s already extracted at this version — skipped, not recharged",
                        platform, content_id)
                    return None

                for b in beats:
                    await conn.execute(
                        "INSERT INTO extraction_beats "
                        "(extraction_id, vocabulary_version, position, function, description) "
                        "VALUES ($1,$2,$3,$4,$5)",
                        extraction_id, vocabulary_version, b["position"], b["function"],
                        b["description"],
                    )

                for a in analysis.get("attributes") or []:
                    await conn.execute(
                        "INSERT INTO extraction_attributes "
                        "(extraction_id, dimension, value, confidence, vocabulary_version) "
                        "VALUES ($1,$2,$3,$4,$5)",
                        extraction_id, a["dimension"], a["value"], a["confidence"],
                        vocabulary_version,
                    )

                # Latch the vocabulary version as immutable now that a stored row
                # depends on it. A version nothing references yet stays editable,
                # which is what makes iterating on descriptions possible before
                # going live; from here on, altering it would silently rewrite the
                # meaning of this row (FR-030).
                await conn.execute(
                    "UPDATE extraction_vocabulary_terms SET frozen_at = now() "
                    "WHERE version = $1 AND frozen_at IS NULL",
                    vocabulary_version,
                )
            return str(extraction_id)
    finally:
        await pool.close()


@task(name="extraction.quarantine.record", retries=0)
async def extraction_quarantine_record(
    platform: str,
    content_id: str,
    failure_kind: str,
    analysis: Optional[Dict[str, Any]] = None,
    provenance: Optional[Dict[str, Any]] = None,
    usage: Optional[Dict[str, Any]] = None,
    purpose: str = "production",
    run_id: Optional[str] = None,
    db_env: Optional[str] = None,
) -> str:
    """
    Append one quarantine row: raw output, BOTH attempts' errors, provenance, cost.

    APPEND-ONLY. There is no update path and no delete path — the evidence is the
    product. A count that cannot be opened into the text that failed would tell
    you the rate was rising and nothing about why, which is the position the
    pre-feature code left you in.
    """
    if failure_kind not in FAILURE_KINDS:
        raise ValueError(
            f"unknown failure_kind {failure_kind!r}; the vocabulary is closed and has no 'other'. "
            f"Legal: {', '.join(sorted(FAILURE_KINDS))}")

    import json as _json

    analysis = analysis or {}
    provenance = provenance or {}
    usage = usage or {}

    # A twice-invalid response arrives with `attempts` (both sets of validation
    # errors). A response that never became validatable at all — a provider 5xx,
    # a timeout, unreachable media — arrives with a plain `error` string instead,
    # and NOTHING was persisting it: those rows landed with `validation_errors =
    # []` and `raw_output = NULL`, so the count could not be opened into the text
    # that failed.
    #
    # Measured: a full-corpus backfill produced 14 `provider_error` rows whose
    # cause was unrecoverable afterwards. FR-020's counts are only useful if they
    # can be opened up; a count that tells you the rate is rising and nothing
    # about why is the position the pre-feature code left you in.
    attempts = analysis.get("attempts")
    if not attempts and analysis.get("error"):
        attempts = [{
            "attempt": 1,
            "failure_kind": failure_kind,
            "errors": [str(analysis["error"])],
        }]

    pool = await _db_pool(db_env)
    try:
        async with pool.acquire() as conn:
            return str(await conn.fetchval(
                """
                INSERT INTO extraction_quarantine
                    (platform, content_id, run_id, purpose, failure_kind, raw_output,
                     validation_errors, model_requested, model_served, prompt_version,
                     schema_version, vocabulary_version, prompt_tokens, completion_tokens, cost_usd)
                VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb,$8,$9,$10,$11,$12,$13,$14,$15)
                RETURNING id
                """,
                platform, content_id, run_id, purpose, failure_kind,
                analysis.get("raw_output"),
                _json.dumps(attempts or []),
                provenance.get("model_requested") or "unknown",
                provenance.get("model_served"),
                provenance.get("prompt_version") or "unknown",
                provenance.get("schema_version") or "unknown",
                provenance.get("vocabulary_version") or "unknown",
                usage.get("prompt_tokens"), usage.get("completion_tokens"), usage.get("cost_usd"),
            ))
    finally:
        await pool.close()


@task(name="extraction.quarantine.count", retries=0)
async def extraction_quarantine_count(db_env: Optional[str] = None) -> Dict[str, Any]:
    """
    Quarantine counts by model, prompt version, schema version, and failure kind (FR-020).

    Also breaks `truncated` out BY CONTENT TYPE, which is what lets
    ANALYZE_VIDEO_MAX_TOKENS be resized on evidence after a month of real data
    (FR-011). The pre-feature maximum transcript length is right-censored — every
    truncated response was regex-salvaged into looking complete — so the budget
    cannot be sized honestly until truncation is countable, and it has to be
    countable from the day the forward path ships.
    """
    pool = await _db_pool(db_env)
    try:
        async with pool.acquire() as conn:
            by_axis = await conn.fetch(
                "SELECT failure_kind, model_requested, prompt_version, schema_version, "
                "count(*) AS n FROM extraction_quarantine GROUP BY 1,2,3,4 ORDER BY n DESC")
            truncated = await conn.fetch(
                "SELECT COALESCE(s.content_type, 'unknown') AS content_type, count(*) AS n "
                "FROM extraction_quarantine q "
                "LEFT JOIN harvested_signals s "
                "  ON s.platform = q.platform AND s.content_id = q.content_id "
                "WHERE q.failure_kind = 'truncated' GROUP BY 1 ORDER BY n DESC")
            total = await conn.fetchval("SELECT count(*) FROM extraction_quarantine")
            return {
                "total": total,
                "by_axis": [dict(r) for r in by_axis],
                "truncated_by_content_type": [dict(r) for r in truncated],
            }
    finally:
        await pool.close()


@task(name="extraction.vocabulary.residual-share", retries=0)
async def extraction_vocabulary_residual_share(
    by_month: bool = True, db_env: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    The share of beats carrying the residual function (FR-005, SC-011).

    This is THE measurement that decides what v2 should contain, and the entire
    reason v1 is deliberately minimal (research.md R11). A rising share means the
    five terms are too few. Without this the v2 decision has no evidence, and
    pre-loading eight plausible terms would have pre-empted the very finding this
    mechanism exists to produce.
    """
    group = "date_trunc('month', e.extracted_at)" if by_month else "NULL::timestamptz"
    pool = await _db_pool(db_env)
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT {group} AS month,
                       count(*) AS beats,
                       count(*) FILTER (WHERE b.function = v.residual) AS residual_beats,
                       CASE WHEN count(*) = 0 THEN NULL ELSE
                         round(100.0 * count(*) FILTER (WHERE b.function = v.residual)
                               / count(*), 1) END AS pct_residual
                FROM content_extractions e
                JOIN extraction_beats b ON b.extraction_id = e.id
                JOIN LATERAL (
                    SELECT term AS residual FROM extraction_vocabulary_terms
                    WHERE version = e.vocabulary_version AND dimension = 'beat_function'
                      AND is_residual LIMIT 1
                ) v ON true
                WHERE e.purpose = 'production'
                GROUP BY 1 ORDER BY 1
                """
            )
            return [dict(r) for r in rows]
    finally:
        await pool.close()


@task(name="extraction.parity.report", retries=0)
async def extraction_parity_report(db_env: Optional[str] = None) -> Dict[str, Any]:
    """
    OBSERVATIONAL per-path distributions, each with its observation count (FR-025).

    Costs ZERO model calls — a query over extractions that already exist.

    ⚠️ THIS IS NOT EVIDENCE OF A MODEL DIFFERENCE, and the output says so in its
    own body rather than leaving the caller to remember. Video and image content
    differ substantively as well as by model: a Reel really does open differently
    from a carousel. A divergence here is consistent with a model effect, a
    content effect, or both, and nothing in this query can separate them. Only the
    controlled comparison (calibration_tasks.calibration_agreement_compute) can —
    same items, same input, both models (FR-026, FR-027f).
    """
    pool = await _db_pool(db_env)
    try:
        async with pool.acquire() as conn:
            beats = await conn.fetch(
                "SELECT e.media_path, b.function, count(*) AS n "
                "FROM content_extractions e JOIN extraction_beats b ON b.extraction_id = e.id "
                "WHERE e.purpose = 'production' GROUP BY 1,2 ORDER BY 1,3 DESC")
            attrs = await conn.fetch(
                "SELECT e.media_path, a.dimension, a.value, count(*) AS n "
                "FROM content_extractions e JOIN extraction_attributes a ON a.extraction_id = e.id "
                "WHERE e.purpose = 'production' GROUP BY 1,2,3 ORDER BY 1,4 DESC")
            totals = await conn.fetch(
                "SELECT media_path, count(*) AS extractions FROM content_extractions "
                "WHERE purpose = 'production' GROUP BY 1")
            return {
                "beat_functions_by_path": [dict(r) for r in beats],
                "attributes_by_path": [dict(r) for r in attrs],
                "extractions_by_path": [dict(r) for r in totals],
                "confound": (
                    "OBSERVATIONAL ONLY. Video and image content differ substantively as well as "
                    "by model, so a divergence here is NOT evidence of a model difference unless "
                    "the controlled comparison supports it (FR-026, FR-027f). These distributions "
                    "must never be normalised away, averaged into a combined figure, or silently "
                    "reconciled by preferring one path's labels."
                ),
            }
    finally:
        await pool.close()


@task(name="extraction.store.revalidate", retries=0)
async def extraction_store_revalidate(db_env: Optional[str] = None) -> Dict[str, Any]:
    """
    Re-validate the WHOLE store, each row against ITS OWN recorded version (SC-005).

    Distinct from write-time validation, and not redundant with it: this catches a
    row stored before a validator bug was fixed, and it is the only check that a
    PAST version is still interpretable (FR-030). Write-time validation can only
    ever attest to the rules as they stood at write time.

    Returns zero failures on a healthy store. Any failure is a defect.
    """
    pool = await _db_pool(db_env)
    try:
        async with pool.acquire() as conn:
            bad_positions = await conn.fetch(
                "SELECT e.platform, e.content_id, e.vocabulary_version "
                "FROM content_extractions e JOIN extraction_beats b ON b.extraction_id = e.id "
                "GROUP BY e.id, e.platform, e.content_id, e.vocabulary_version "
                "HAVING count(*) <> max(b.position) OR min(b.position) <> 1")
            no_beats = await conn.fetch(
                "SELECT e.platform, e.content_id FROM content_extractions e "
                "LEFT JOIN extraction_beats b ON b.extraction_id = e.id "
                "WHERE b.id IS NULL")
            # The composite FK makes this structurally impossible, so a non-empty
            # result means the constraint was dropped — worth detecting loudly
            # rather than trusting it silently.
            bad_terms = await conn.fetch(
                "SELECT e.platform, e.content_id, b.function, e.vocabulary_version "
                "FROM content_extractions e JOIN extraction_beats b ON b.extraction_id = e.id "
                "LEFT JOIN extraction_vocabulary_terms t "
                "  ON t.version = b.vocabulary_version AND t.dimension = b.dimension "
                " AND t.term = b.function "
                "WHERE t.term IS NULL")
            bad_absence = await conn.fetch(
                "SELECT platform, content_id FROM content_extractions "
                "WHERE subtitle IS NULL AND subtitle_absence IS NULL")
            total = await conn.fetchval("SELECT count(*) FROM content_extractions")

            failures = {
                "non_contiguous_positions": [dict(r) for r in bad_positions],
                "extractions_without_beats": [dict(r) for r in no_beats],
                "out_of_vocabulary_terms": [dict(r) for r in bad_terms],
                "null_subtitle_without_reason": [dict(r) for r in bad_absence],
            }
            return {
                "extractions_checked": total,
                "failures": failures,
                "failure_count": sum(len(v) for v in failures.values()),
            }
    finally:
        await pool.close()


@task(name="extraction.cost.project", retries=0)
async def extraction_cost_project(
    content_types: Optional[List[str]] = None,
    items_by_type: Optional[Dict[str, int]] = None,
    db_env: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Project spend from MEASURED per-item usage, and state the basis (FR-033).

    Returns `basis='uncalibrated'` and NO dollar figure when no measured baseline
    exists for a class of item. That is the correct first output, not a failure:
    no cost baseline existed anywhere in this system before this feature
    (research.md R3), and a confident number here would be a fabrication sharing a
    field with a measurement — exactly what Constitution VI forbids.

    Quarantine rows count toward the baseline. Tokens were spent whether or not
    the output was usable; excluding them would bias the projection low.
    """
    pool = await _db_pool(db_env)
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT content_type,
                       count(*) AS sample_size,
                       avg(cost_usd) AS avg_cost_usd
                FROM (
                    SELECT e.content_type, e.cost_usd
                    FROM content_extractions e WHERE e.cost_usd IS NOT NULL
                    UNION ALL
                    SELECT COALESCE(s.content_type, 'unknown'), q.cost_usd
                    FROM extraction_quarantine q
                    LEFT JOIN harvested_signals s
                      ON s.platform = q.platform AND s.content_id = q.content_id
                    WHERE q.cost_usd IS NOT NULL
                ) t
                GROUP BY 1
                """
            )
            measured = {r["content_type"]: r for r in rows}

            wanted = list(items_by_type or {}) or (content_types or list(measured))
            uncovered = [ct for ct in wanted if ct not in measured]

            per_type = {k: {"sample_size": v["sample_size"],
                            "avg_cost_usd": float(v["avg_cost_usd"])}
                        for k, v in measured.items()}

            if not measured or uncovered:
                # PARTIAL COVERAGE gets a labelled LOWER BOUND, not silence.
                #
                # FR-033 requires the projection SAY SO where no baseline exists —
                # not that it withhold every figure. Refusing a number because 2 of
                # 819 items lack a baseline is stricter than the rule and less
                # useful: an operator deciding whether to spend needs to know this
                # is about a dollar rather than about a hundred.
                #
                # The bound goes in its OWN field. `projection_usd` stays None, so
                # the stored column can never hold anything but a full measured
                # projection — the database column and the operator's screen carry
                # different things on purpose, and only the screen gets the
                # caveated number.
                items = items_by_type or {}
                covered_usd = sum(
                    float(measured[ct]["avg_cost_usd"]) * n
                    for ct, n in items.items() if ct in measured
                ) if measured else None
                covered_items = sum(n for ct, n in items.items() if ct in measured)
                uncovered_items = sum(n for ct, n in items.items() if ct not in measured)
                bound_note = ""
                if covered_usd is not None and items:
                    bound_note = (
                        f"Measured content types alone come to ${covered_usd:.4f}, a LOWER BOUND "
                        f"covering {covered_items} of {sum(items.values())} item(s); the remaining "
                        f"{uncovered_items} item(s) have no measurement and are NOT estimated. ")
                return {
                    "basis": "uncalibrated",
                    "projection_usd": None,
                    "sample_size": None,
                    "covered_projection_usd": (
                        round(covered_usd, 6) if covered_usd is not None else None),
                    "covered_item_count": covered_items,
                    "uncovered_item_count": uncovered_items,
                    "uncovered_content_types": uncovered or wanted,
                    "per_type": per_type,
                    "note": (
                        "PROJECTED SPEND: UNCALIBRATED — no measured baseline for "
                        f"{', '.join(uncovered or wanted) or 'any content type'}. "
                        + bound_note
                        + "Run --pilot N to establish the missing baseline. A single total here "
                          "would be an estimate wearing a measurement's form."
                    ),
                }

            total = sum(
                float(measured[ct]["avg_cost_usd"]) * n
                for ct, n in (items_by_type or {}).items()
            )
            sample = sum(int(measured[ct]["sample_size"]) for ct in wanted)
            return {
                "basis": "measured",
                "projection_usd": round(total, 6),
                "sample_size": sample,
                "covered_projection_usd": round(total, 6),
                "uncovered_item_count": 0,
                "uncovered_content_types": [],
                "per_type": per_type,
                "note": (
                    f"Projected from measured per-item cost over {sample} prior extraction(s), "
                    f"grouped by content type."
                ),
            }
    finally:
        await pool.close()


@task(name="extraction.cost.ceiling-check", retries=0)
async def extraction_cost_ceiling_check(
    projection_usd: Optional[float] = None,
    ceiling_usd: Optional[float] = None,
    db_env: Optional[str] = None,
) -> Dict[str, Any]:
    """
    HARD STOP when month-to-date + this run's projection would exceed the ceiling.

    Constitution XI requires the ceiling be ENFORCED, not merely reported, and
    FR-037a says the check happens before ANY costed run begins — which includes
    the recurring forward harvest, not only the batch operations.

    ⚠️ THE FIGURE COVERS EXTRACTION SPEND ONLY (FR-037b). Songbird's generation
    spend sits outside this count, so it must never be surfaced as a system-wide
    budget: reporting "within budget" while most of the system's model spend is
    uncounted is exactly the incomplete-evidence failure Constitution VI names.
    Every caller labels it accordingly.

    Raises SpendCeilingExceeded rather than returning a flag, so a caller cannot
    proceed by forgetting to check a boolean.
    """
    ceiling = MONTHLY_CEILING_USD if ceiling_usd is None else ceiling_usd
    pool = await _db_pool(db_env)
    try:
        async with pool.acquire() as conn:
            month_to_date = float(await conn.fetchval(
                """
                SELECT COALESCE(SUM(cost_usd), 0) FROM (
                    SELECT cost_usd FROM content_extractions
                    WHERE extracted_at >= date_trunc('month', now())
                    UNION ALL
                    SELECT cost_usd FROM extraction_quarantine
                    WHERE quarantined_at >= date_trunc('month', now())
                ) t
                """
            ) or 0)
    finally:
        await pool.close()

    projected_total = month_to_date + float(projection_usd or 0)
    result = {
        "scope": "extraction spend only (excludes songbird generation spend)",
        "month_to_date_usd": round(month_to_date, 6),
        "projection_usd": projection_usd,
        "projected_total_usd": round(projected_total, 6),
        "ceiling_usd": ceiling,
        "would_exceed": projected_total > ceiling,
    }
    if result["would_exceed"]:
        raise SpendCeilingExceeded(
            f"EXTRACTION spend ceiling would be exceeded: month-to-date "
            f"${month_to_date:.4f} + projected ${float(projection_usd or 0):.4f} "
            f"= ${projected_total:.4f} > ceiling ${ceiling:.2f}. "
            f"This ceiling covers EXTRACTION SPEND ONLY — songbird generation spend is not "
            f"counted against it, so it is not a system-wide budget. "
            f"Raise EXTRACTION_MONTHLY_CEILING_USD deliberately or wait for the next month."
        )
    return result


@task(name="extraction.attributes.filter", retries=0)
async def extraction_attributes_filter(
    min_confidence: str = "low", db_env: Optional[str] = None
) -> Dict[str, Any]:
    """
    Attribute assignments at or above a minimum confidence (FR-014, SC-012).

    Returns the survivors AND their count — Constitution VIII requires any
    aggregate over assignments report the observation count it rests on.

    Ordering comes from CONFIDENCE_RANK here rather than from the database,
    because `confidence` is a CHECK'd text column with no stored rank. That is
    deliberate (an ordered scale, not a false-precision float), but it means
    "minimum" has to be defined somewhere, and this is that definition.

    Excluding a low-confidence assignment leaves the rest of the extraction
    intact — its other assignments, its flow, and its subtitle all remain.
    """
    floor = CONFIDENCE_RANK.get(min_confidence)
    if floor is None:
        raise ValueError(
            f"unknown confidence {min_confidence!r}; legal: {', '.join(CONFIDENCE_RANK)}")
    kept = [c for c, rank in CONFIDENCE_RANK.items() if rank >= floor]

    pool = await _db_pool(db_env)
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT a.dimension, a.value, a.confidence, a.vocabulary_version, "
                "       e.platform, e.content_id "
                "FROM extraction_attributes a JOIN content_extractions e ON e.id = a.extraction_id "
                "WHERE e.purpose = 'production' AND a.confidence = ANY($1::text[]) "
                "ORDER BY a.dimension, a.value",
                kept,
            )
            total = await conn.fetchval(
                "SELECT count(*) FROM extraction_attributes a "
                "JOIN content_extractions e ON e.id = a.extraction_id "
                "WHERE e.purpose = 'production'")
            return {
                "min_confidence": min_confidence,
                "assignments": [dict(r) for r in rows],
                "assignment_count": len(rows),
                "excluded_count": (total or 0) - len(rows),
                "total_before_filter": total or 0,
            }
    finally:
        await pool.close()


@task(name="extraction.batch.record-start", retries=0)
async def extraction_batch_record_start(
    batch_kind: str,
    mode: str,
    scope_description: str,
    items_in_scope: int,
    flow_name: str,
    projection: Optional[Dict[str, Any]] = None,
    ceiling: Optional[Dict[str, Any]] = None,
    confirmed: bool = False,
    db_env: Optional[str] = None,
) -> str:
    """
    Open a `runs` row (kind='extraction') and its costed-batch detail row.

    This is the spec's "Backfill batch" entity, and it is PERSISTED rather than
    reported at run time for two reasons: the projection has to survive to be
    compared against actual spend (SC-008), and the per-item outcomes have to
    survive to be counted (SC-010). A flow summary that vanishes when the process
    exits makes both answerable only while watching.

    A detail table on `runs`, not a run table of its own — `runs` already holds
    flow name, timing, status and summary, and duplicating them would be the
    second-mechanism mistake this feature argues against elsewhere.
    """
    projection = projection or {}
    ceiling = ceiling or {}
    pool = await _db_pool(db_env)
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                run_id = await conn.fetchval(
                    "INSERT INTO runs (kind, flow_name, started_at) "
                    "VALUES ('extraction', $1, now()) RETURNING id",
                    flow_name,
                )
                await conn.execute(
                    """
                    INSERT INTO extraction_batch_runs
                        (run_id, batch_kind, mode, scope_description, items_in_scope,
                         projection_basis, projection_usd, projection_sample_size,
                         ceiling_usd, month_to_date_usd, confirmed)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
                    """,
                    run_id, batch_kind, mode, scope_description, items_in_scope,
                    projection.get("basis", "uncalibrated"),
                    projection.get("projection_usd"),
                    projection.get("sample_size"),
                    ceiling.get("ceiling_usd"),
                    ceiling.get("month_to_date_usd"),
                    confirmed,
                )
            return str(run_id)
    finally:
        await pool.close()


@task(name="extraction.batch.record-finish", retries=0)
async def extraction_batch_record_finish(
    run_id: str,
    outcomes: Dict[str, int],
    actual_usd: Optional[float] = None,
    status: str = "completed",
    db_env: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Close the run and record its measured spend and per-item outcomes.

    `items_unclassified` is RECORDED, not rejected. SC-010 wants unclassified
    outcomes counted; a constraint forbidding them would make the run fail to
    write its own summary rather than report the defect — the failure hidden by
    the very guard meant to prevent it.

    Also returns the projected-vs-actual comparison (SC-008): within ±25%, or the
    divergence made explicit so its cause can be stated.
    """
    pool = await _db_pool(db_env)
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    """
                    UPDATE extraction_batch_runs
                       SET actual_usd = $2,
                           items_extracted = $3, items_cached = $4, items_quarantined = $5,
                           items_skipped = $6, items_unclassified = $7
                     WHERE run_id = $1
                    """,
                    run_id, actual_usd,
                    outcomes.get("extracted", 0), outcomes.get("cached", 0),
                    outcomes.get("quarantined", 0), outcomes.get("skipped", 0),
                    outcomes.get("unclassified", 0),
                )
                await conn.execute(
                    "UPDATE runs SET ended_at = now(), status = $2 WHERE id = $1",
                    run_id, status)

            row = await conn.fetchrow(
                "SELECT items_in_scope, projection_usd, projection_basis, actual_usd, "
                "       items_extracted, items_cached, items_quarantined, items_skipped, "
                "       items_unclassified "
                "FROM extraction_batch_runs WHERE run_id = $1", run_id)

    finally:
        await pool.close()

    accounted = (row["items_extracted"] + row["items_cached"] + row["items_quarantined"]
                 + row["items_skipped"] + row["items_unclassified"])
    comparison: Dict[str, Any] = {
        "projection_usd": float(row["projection_usd"]) if row["projection_usd"] is not None else None,
        "actual_usd": float(row["actual_usd"]) if row["actual_usd"] is not None else None,
        "basis": row["projection_basis"],
    }
    if comparison["projection_usd"] and comparison["actual_usd"] is not None:
        proj = comparison["projection_usd"]
        delta = comparison["actual_usd"] - proj
        comparison["delta_usd"] = round(delta, 6)
        comparison["delta_pct"] = round(100.0 * delta / proj, 1) if proj else None
        comparison["within_tolerance"] = abs(comparison["delta_pct"] or 0) <= 25.0
    else:
        comparison["within_tolerance"] = None
        comparison["note"] = (
            "no calibrated projection to compare against — the run was uncalibrated, "
            "which is the honest state before a baseline exists (FR-033)")

    return {
        "run_id": run_id,
        "items_in_scope": row["items_in_scope"],
        "items_accounted": accounted,
        # SC-010: every item resolves to exactly one outcome.
        "fully_accounted": accounted == row["items_in_scope"],
        "unclassified": row["items_unclassified"],
        "cost_comparison": comparison,
    }
