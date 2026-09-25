"""
Extraction storage tests (feature 007-structured-extraction).

Covers the invariants that fail SILENTLY and so matter most (data-model
invariants 6, 7, 9): calibration leaking into analytical figures, backfill
writing `harvested_signals`, and an estimate reaching `cost_usd`. Each of those
produces confident, wrong output rather than an error.

Real disposable PostgreSQL, per `.claude/rules/backend/schema.md`.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.conftest import pool_from_connection  # noqa: E402
from tasks import extraction_tasks  # noqa: E402

pytestmark = [pytest.mark.schema, pytest.mark.asyncio]

REPO = Path(__file__).resolve().parents[3]

VOCAB = [
    ("hook", False, 1), ("setup", False, 2), ("main_point", False, 3),
    ("call_to_action", False, 4), ("unclassified", True, 5),
]

PROVENANCE = {
    "model_requested": "xiaomi/mimo-v2.5", "model_served": "xiaomi/mimo-v2.5",
    "provider": "xiaomi", "media_path": "video", "prompt_version": "v1",
    "schema_version": "v1", "vocabulary_version": "v1", "attempts": 1,
}
USAGE = {"prompt_tokens": 1200, "completion_tokens": 340, "cost_usd": 0.0021}


async def _seed_vocabulary(db, version="v1"):
    for term, residual, ordinal in VOCAB:
        await db.execute(
            "INSERT INTO extraction_vocabulary_terms "
            "(version, dimension, term, is_residual, ordinal, description) "
            "VALUES ($1,'beat_function',$2,$3,$4,'d') ON CONFLICT DO NOTHING",
            version, term, residual, ordinal)


def _analysis(**overrides):
    payload = {
        "status": "success",
        "subtitle": "halo semuanya",
        "subtitle_absence": None,
        "beats": [
            {"position": 1, "function": "hook", "description": "opens with a question"},
            {"position": 2, "function": "main_point", "description": "explains it"},
        ],
        "attributes": [],
        "summary": "an explainer",
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def patched(monkeypatch, spine_db):
    monkeypatch.setattr(extraction_tasks, "_db_pool", pool_from_connection(spine_db))
    return spine_db


# ---------------------------------------------------------------------------
# Storing
# ---------------------------------------------------------------------------

async def test_store_writes_extraction_and_beats_in_one_transaction(patched):
    await _seed_vocabulary(patched)
    ex_id = await extraction_tasks.extraction_record_store.fn(
        platform="instagram", content_id="c1", content_type="video",
        analysis=_analysis(), provenance=PROVENANCE, usage=USAGE,
        content_hash="sha256:abc")
    assert ex_id is not None

    beats = await patched.fetch(
        "SELECT position, function FROM extraction_beats WHERE extraction_id=$1 ORDER BY position",
        ex_id)
    assert [(b["position"], b["function"]) for b in beats] == [(1, "hook"), (2, "main_point")]

    row = await patched.fetchrow("SELECT * FROM content_extractions WHERE id=$1", ex_id)
    assert row["media_path"] == "video"
    assert row["model_served"] == "xiaomi/mimo-v2.5"
    assert float(row["cost_usd"]) == 0.0021


async def test_re_storing_at_the_same_version_writes_nothing_and_recharges_nothing(patched):
    """FR-036. The conflict happens in the DATABASE, so a bug in a caller's skip
    logic cannot cause a double write or a double charge."""
    await _seed_vocabulary(patched)
    first = await extraction_tasks.extraction_record_store.fn(
        platform="instagram", content_id="dup", content_type="video",
        analysis=_analysis(), provenance=PROVENANCE, usage=USAGE, content_hash="h")
    second = await extraction_tasks.extraction_record_store.fn(
        platform="instagram", content_id="dup", content_type="video",
        analysis=_analysis(), provenance=PROVENANCE, usage=USAGE, content_hash="h")

    assert first is not None and second is None
    assert await patched.fetchval(
        "SELECT count(*) FROM content_extractions WHERE content_id='dup'") == 1
    assert await patched.fetchval("SELECT count(*) FROM extraction_beats") == 2


async def test_storing_freezes_the_vocabulary_version(patched):
    """Once a stored row depends on a version, altering it would silently rewrite
    that row's meaning (FR-030)."""
    await _seed_vocabulary(patched)
    assert await patched.fetchval(
        "SELECT count(*) FROM extraction_vocabulary_terms WHERE frozen_at IS NOT NULL") == 0
    await extraction_tasks.extraction_record_store.fn(
        platform="instagram", content_id="c1", content_type="video",
        analysis=_analysis(), provenance=PROVENANCE, usage=USAGE, content_hash="h")
    assert await patched.fetchval(
        "SELECT count(*) FROM extraction_vocabulary_terms WHERE frozen_at IS NULL") == 0


async def test_zero_beats_is_refused_at_the_storage_boundary(patched):
    """FR-016: the validator is not the only line of defence.

    This task is reachable from backfill and calibration too, and "the caller
    already checked" is exactly the assumption that lets an unchecked path appear.
    """
    await _seed_vocabulary(patched)
    with pytest.raises(ValueError, match="at least one beat"):
        await extraction_tasks.extraction_record_store.fn(
            platform="instagram", content_id="c1", content_type="video",
            analysis=_analysis(beats=[]), provenance=PROVENANCE, usage=USAGE, content_hash="h")


async def test_non_contiguous_positions_refused_at_the_storage_boundary(patched):
    await _seed_vocabulary(patched)
    with pytest.raises(ValueError, match="contiguous"):
        await extraction_tasks.extraction_record_store.fn(
            platform="instagram", content_id="c1", content_type="video",
            analysis=_analysis(beats=[
                {"position": 1, "function": "hook", "description": "a"},
                {"position": 3, "function": "setup", "description": "b"}]),
            provenance=PROVENANCE, usage=USAGE, content_hash="h")


async def test_null_subtitle_without_reason_refused_at_the_storage_boundary(patched):
    await _seed_vocabulary(patched)
    with pytest.raises(ValueError, match="null subtitle requires a recorded reason"):
        await extraction_tasks.extraction_record_store.fn(
            platform="instagram", content_id="c1", content_type="image",
            analysis=_analysis(subtitle=None, subtitle_absence=None),
            provenance=PROVENANCE, usage=USAGE, content_hash="h")


# ---------------------------------------------------------------------------
# Invariant 1 & 2 — the whole-table queries
# ---------------------------------------------------------------------------

async def test_every_stored_extraction_has_contiguous_positions_from_one(patched):
    await _seed_vocabulary(patched)
    for cid in ("a", "b", "c"):
        await extraction_tasks.extraction_record_store.fn(
            platform="instagram", content_id=cid, content_type="video",
            analysis=_analysis(), provenance=PROVENANCE, usage=USAGE, content_hash="h")

    offenders = await patched.fetch(
        "SELECT extraction_id FROM extraction_beats GROUP BY extraction_id "
        "HAVING count(*) <> max(position) OR min(position) <> 1")
    assert offenders == []


async def test_no_stored_extraction_lacks_beats(patched):
    await _seed_vocabulary(patched)
    await extraction_tasks.extraction_record_store.fn(
        platform="instagram", content_id="a", content_type="video",
        analysis=_analysis(), provenance=PROVENANCE, usage=USAGE, content_hash="h")
    orphans = await patched.fetch(
        "SELECT e.id FROM content_extractions e "
        "LEFT JOIN extraction_beats b ON b.extraction_id = e.id WHERE b.id IS NULL")
    assert orphans == []


# ---------------------------------------------------------------------------
# SC-004 — field-set parity between the two paths
# ---------------------------------------------------------------------------

async def test_video_and_image_paths_produce_the_same_field_set(patched):
    """SC-004. T018 unified the two paths; this is what proves they stayed unified.

    Compares which columns are populated, not their values — the only permitted
    difference is subtitle CONTENT, which is a fact about the media rather than a
    difference in the contract (FR-023).
    """
    await _seed_vocabulary(patched)
    await extraction_tasks.extraction_record_store.fn(
        platform="instagram", content_id="vid", content_type="video",
        analysis=_analysis(), provenance=PROVENANCE, usage=USAGE, content_hash="h")
    await extraction_tasks.extraction_record_store.fn(
        platform="instagram", content_id="img", content_type="image",
        analysis=_analysis(subtitle=None, subtitle_absence="not_applicable_no_audio"),
        provenance={**PROVENANCE, "media_path": "image"}, usage=USAGE, content_hash="h2")

    rows = {r["media_path"]: r for r in
            await patched.fetch("SELECT * FROM content_extractions")}
    subtitle_fields = {"subtitle", "subtitle_absence"}
    populated = {
        path: {k for k, v in dict(row).items() if v is not None} - subtitle_fields
        for path, row in rows.items()
    }
    assert populated["video"] == populated["image"], (
        "the two paths must differ only in subtitle content; "
        f"video-only={populated['video'] - populated['image']}, "
        f"image-only={populated['image'] - populated['video']}")

    # And both must account for their subtitle one way or the other.
    for row in rows.values():
        assert (row["subtitle"] is not None) or (row["subtitle_absence"] is not None)


# ---------------------------------------------------------------------------
# FR-005 / SC-011 — the residual share
# ---------------------------------------------------------------------------

async def test_residual_share_is_reportable(patched):
    """The measurement that decides v2's membership (research.md R11)."""
    await _seed_vocabulary(patched)
    await extraction_tasks.extraction_record_store.fn(
        platform="instagram", content_id="a", content_type="video",
        analysis=_analysis(beats=[
            {"position": 1, "function": "hook", "description": "x"},
            {"position": 2, "function": "unclassified", "description": "y"}]),
        provenance=PROVENANCE, usage=USAGE, content_hash="h")

    rows = await extraction_tasks.extraction_vocabulary_residual_share.fn()
    assert rows and rows[0]["beats"] == 2
    assert rows[0]["residual_beats"] == 1
    assert float(rows[0]["pct_residual"]) == 50.0


# ---------------------------------------------------------------------------
# FR-014 / SC-012 — the confidence filter reports its count
# ---------------------------------------------------------------------------

async def test_confidence_filter_returns_survivors_and_their_count(patched):
    await _seed_vocabulary(patched)
    await patched.execute(
        "INSERT INTO extraction_vocabulary_terms "
        "(version, dimension, term, is_residual, ordinal, description) VALUES "
        "('v1','tone','playful',false,1,'d'), ('v1','tone','serious',false,2,'d'), "
        "('v1','pace','fast',false,1,'d')")
    ex = await extraction_tasks.extraction_record_store.fn(
        platform="instagram", content_id="a", content_type="video",
        analysis=_analysis(attributes=[
            {"dimension": "tone", "value": "playful", "confidence": "high"},
            {"dimension": "pace", "value": "fast", "confidence": "low"}]),
        provenance=PROVENANCE, usage=USAGE, content_hash="h")
    assert ex is not None

    out = await extraction_tasks.extraction_attributes_filter.fn(min_confidence="medium")
    assert out["assignment_count"] == 1
    assert out["excluded_count"] == 1
    assert out["total_before_filter"] == 2
    assert out["assignments"][0]["dimension"] == "tone"

    # The rest of the extraction survives the exclusion (FR-014).
    assert await patched.fetchval("SELECT count(*) FROM extraction_beats") == 2
    assert await patched.fetchval(
        "SELECT subtitle FROM content_extractions WHERE id=$1", ex) == "halo semuanya"


# ---------------------------------------------------------------------------
# Invariant 7 — backfill must never reach the signal writer. TWO LAYERS.
# ---------------------------------------------------------------------------

    # NOTE: the STATIC half of invariant 7 lives in test_extraction_guards.py,
    # not here. This module is marked `schema` and is skipped wholesale without a
    # database — and an import guard that only runs when Postgres happens to be
    # reachable is not a guard.


async def test_storing_an_extraction_leaves_harvested_signals_untouched(patched):
    """The behavioural half of invariant 7: 'this did not happen'."""
    await _seed_vocabulary(patched)
    await patched.execute(
        "INSERT INTO harvested_signals "
        "(platform, content_id, profile_key, content_type, subtitle, content_flow, summary) "
        "VALUES ('instagram','a','acct','video','ORIGINAL TRANSCRIPT','old flow','old summary')")
    before = dict(await patched.fetchrow(
        "SELECT subtitle, content_flow, summary FROM harvested_signals WHERE content_id='a'"))

    await extraction_tasks.extraction_record_store.fn(
        platform="instagram", content_id="a", content_type="video",
        analysis=_analysis(subtitle="A COMPLETELY DIFFERENT TRANSCRIPT"),
        provenance=PROVENANCE, usage=USAGE, content_hash="h")

    after = dict(await patched.fetchrow(
        "SELECT subtitle, content_flow, summary FROM harvested_signals WHERE content_id='a'"))
    assert after == before, "a re-extraction must never overwrite the stored transcript"
    assert after["subtitle"] == "ORIGINAL TRANSCRIPT"


# ---------------------------------------------------------------------------
# Invariant 6 — calibration never enters an analytical figure
# ---------------------------------------------------------------------------

async def test_calibration_extractions_are_absent_from_analytical_figures(patched):
    """SC-018. The failure is INVISIBLE — figures just quietly double-weight."""
    await _seed_vocabulary(patched)
    await extraction_tasks.extraction_record_store.fn(
        platform="instagram", content_id="a", content_type="video",
        analysis=_analysis(), provenance=PROVENANCE, usage=USAGE, content_hash="h")
    await extraction_tasks.extraction_record_store.fn(
        platform="instagram", content_id="a", content_type="video", purpose="calibration",
        analysis=_analysis(beats=[{"position": 1, "function": "setup", "description": "z"}]),
        provenance={**PROVENANCE, "model_requested": "other/model"},
        usage=USAGE, content_hash="h")

    assert await patched.fetchval("SELECT count(*) FROM content_extractions") == 2

    # The residual-share figure sees ONE extraction's beats, not two.
    share = await extraction_tasks.extraction_vocabulary_residual_share.fn()
    assert share[0]["beats"] == 2  # the production extraction's two beats only

    parity = await extraction_tasks.extraction_parity_report.fn()
    assert sum(r["n"] for r in parity["beat_functions_by_path"]) == 2
    # And the report states its confound in its own body (FR-027f).
    assert "OBSERVATIONAL ONLY" in parity["confound"]


# ---------------------------------------------------------------------------
# SC-005 — re-validating the whole store against each row's own version
# ---------------------------------------------------------------------------

async def test_revalidating_the_store_reports_zero_failures(patched):
    await _seed_vocabulary(patched)
    for cid in ("a", "b"):
        await extraction_tasks.extraction_record_store.fn(
            platform="instagram", content_id=cid, content_type="video",
            analysis=_analysis(), provenance=PROVENANCE, usage=USAGE, content_hash="h")
    out = await extraction_tasks.extraction_store_revalidate.fn()
    assert out["extractions_checked"] == 2
    assert out["failure_count"] == 0


async def test_revalidation_detects_a_gap_introduced_behind_the_validators(patched):
    """Proves the check is real, by breaking the data underneath it."""
    await _seed_vocabulary(patched)
    ex = await extraction_tasks.extraction_record_store.fn(
        platform="instagram", content_id="a", content_type="video",
        analysis=_analysis(), provenance=PROVENANCE, usage=USAGE, content_hash="h")
    await patched.execute(
        "UPDATE extraction_beats SET position = 5 WHERE extraction_id=$1 AND position = 2", ex)

    out = await extraction_tasks.extraction_store_revalidate.fn()
    assert out["failure_count"] == 1
    assert out["failures"]["non_contiguous_positions"]


# ---------------------------------------------------------------------------
# Cost, projection, and the ceiling
# ---------------------------------------------------------------------------

async def test_projection_is_uncalibrated_before_any_measured_cost_exists(patched):
    """The honest first output (FR-033, research.md R3).

    A confident dollar figure here would be a fabrication sharing a field with a
    measurement — exactly what Constitution VI forbids.
    """
    out = await extraction_tasks.extraction_cost_project.fn(items_by_type={"video": 364})
    assert out["basis"] == "uncalibrated"
    assert out["projection_usd"] is None
    assert "UNCALIBRATED" in out["note"]
    assert "video" in out["uncovered_content_types"]


async def test_projection_becomes_measured_once_a_baseline_exists(patched):
    await _seed_vocabulary(patched)
    for cid in ("a", "b"):
        await extraction_tasks.extraction_record_store.fn(
            platform="instagram", content_id=cid, content_type="video",
            analysis=_analysis(), provenance=PROVENANCE, usage=USAGE, content_hash=cid)

    out = await extraction_tasks.extraction_cost_project.fn(items_by_type={"video": 100})
    assert out["basis"] == "measured"
    assert out["projection_usd"] == pytest.approx(0.21, rel=1e-3)
    assert out["sample_size"] == 2


async def test_quarantine_cost_counts_toward_the_baseline(patched):
    """Tokens were spent whether or not the output was usable.

    Excluding them would bias the projection low and make a pathological item
    that quarantines on every attempt look free.
    """
    await extraction_tasks.extraction_quarantine_record.fn(
        platform="instagram", content_id="q", failure_kind="truncated",
        analysis={"raw_output": "{trunc", "attempts": [{"attempt": 1}, {"attempt": 2}]},
        provenance=PROVENANCE, usage=USAGE)
    await patched.execute(
        "INSERT INTO harvested_signals (platform, content_id, profile_key, content_type) "
        "VALUES ('instagram','q','acct','video')")

    out = await extraction_tasks.extraction_cost_project.fn(items_by_type={"video": 10})
    assert out["basis"] == "measured"
    assert out["per_type"]["video"]["sample_size"] == 1


async def test_ceiling_hard_stops_rather_than_warning(patched):
    """Constitution XI requires the ceiling be ENFORCED.

    It raises rather than returning a flag, so a caller cannot proceed by
    forgetting to check a boolean.
    """
    await _seed_vocabulary(patched)
    await extraction_tasks.extraction_record_store.fn(
        platform="instagram", content_id="a", content_type="video",
        analysis=_analysis(), provenance=PROVENANCE,
        usage={**USAGE, "cost_usd": 9.0}, content_hash="h")

    with pytest.raises(extraction_tasks.SpendCeilingExceeded) as e:
        await extraction_tasks.extraction_cost_ceiling_check.fn(
            projection_usd=2.0, ceiling_usd=10.0)
    # And it says what the figure covers (FR-037b).
    assert "EXTRACTION SPEND ONLY" in str(e.value)


async def test_ceiling_reports_its_scope_when_it_passes_too(patched):
    out = await extraction_tasks.extraction_cost_ceiling_check.fn(
        projection_usd=1.0, ceiling_usd=100.0)
    assert out["would_exceed"] is False
    assert "excludes songbird" in out["scope"]


# ---------------------------------------------------------------------------
# FR-020 — quarantine counts, incl. truncation by content type (FR-011)
# ---------------------------------------------------------------------------

async def test_quarantine_counts_break_truncation_out_by_content_type(patched):
    await patched.execute(
        "INSERT INTO harvested_signals (platform, content_id, profile_key, content_type) VALUES "
        "('instagram','t1','a','video'), ('instagram','t2','a','video'), "
        "('instagram','t3','a','carousel')")
    for cid in ("t1", "t2", "t3"):
        await extraction_tasks.extraction_quarantine_record.fn(
            platform="instagram", content_id=cid, failure_kind="truncated",
            analysis={"raw_output": "x", "attempts": []}, provenance=PROVENANCE, usage=USAGE)

    out = await extraction_tasks.extraction_quarantine_count.fn()
    assert out["total"] == 3
    by_type = {r["content_type"]: r["n"] for r in out["truncated_by_content_type"]}
    assert by_type == {"video": 2, "carousel": 1}


async def test_a_provider_failure_records_its_reason_not_an_empty_list(patched):
    """FR-020's counts must be openable into the text that failed.

    A twice-invalid response arrives with `attempts`; a provider 5xx or a timeout
    arrives with a plain `error` string and no attempts at all. Persisting only
    the former left 14 rows in a real backfill whose cause was unrecoverable —
    a count that says the rate is rising and nothing about why is exactly the
    position the pre-feature code left you in.
    """
    await extraction_tasks.extraction_quarantine_record.fn(
        platform="instagram", content_id="perr", failure_kind="provider_error",
        analysis={"status": "failed", "error": "OpenRouter HTTP 502: upstream timeout"},
        provenance=PROVENANCE)

    errors = await patched.fetchval(
        "SELECT validation_errors FROM extraction_quarantine WHERE content_id='perr'")
    import json
    parsed = json.loads(errors) if isinstance(errors, str) else errors
    assert parsed, "the failure reason must be persisted, not dropped"
    assert "502" in parsed[0]["errors"][0]
    assert parsed[0]["failure_kind"] == "provider_error"


async def test_unknown_failure_kind_is_refused_by_the_task_too(patched):
    with pytest.raises(ValueError, match="closed and has no 'other'"):
        await extraction_tasks.extraction_quarantine_record.fn(
            platform="instagram", content_id="x", failure_kind="other",
            analysis={}, provenance=PROVENANCE)


# ---------------------------------------------------------------------------
# SC-010 — every batch item resolves to exactly one outcome
# ---------------------------------------------------------------------------

async def test_batch_run_accounts_for_every_item_in_scope(patched):
    run_id = await extraction_tasks.extraction_batch_record_start.fn(
        batch_kind="backfill", mode="real", scope_description="everything at start",
        items_in_scope=10, flow_name="roach-extract-backfill")
    out = await extraction_tasks.extraction_batch_record_finish.fn(
        run_id=run_id,
        outcomes={"extracted": 6, "cached": 2, "quarantined": 1, "skipped": 1, "unclassified": 0},
        actual_usd=0.5)
    assert out["fully_accounted"] is True
    assert out["unclassified"] == 0


async def test_an_unaccounted_item_is_reported_not_hidden(patched):
    """SC-010's defect must be VISIBLE. A constraint forbidding it would make the
    run fail to write its own summary rather than report the problem."""
    run_id = await extraction_tasks.extraction_batch_record_start.fn(
        batch_kind="backfill", mode="real", scope_description="all",
        items_in_scope=10, flow_name="roach-extract-backfill")
    out = await extraction_tasks.extraction_batch_record_finish.fn(
        run_id=run_id,
        outcomes={"extracted": 6, "cached": 0, "quarantined": 0, "skipped": 0, "unclassified": 2},
        actual_usd=0.1)
    assert out["fully_accounted"] is False
    assert out["unclassified"] == 2


async def test_finished_batch_runs_all_account_for_their_scope(patched):
    """Data-model invariant 10, as the whole-table zero-row query."""
    run_id = await extraction_tasks.extraction_batch_record_start.fn(
        batch_kind="backfill", mode="real", scope_description="all",
        items_in_scope=3, flow_name="roach-extract-backfill")
    await extraction_tasks.extraction_batch_record_finish.fn(
        run_id=run_id,
        outcomes={"extracted": 3, "cached": 0, "quarantined": 0, "skipped": 0, "unclassified": 0},
        actual_usd=0.1)

    offenders = await patched.fetch(
        "SELECT b.run_id FROM extraction_batch_runs b JOIN runs r ON r.id = b.run_id "
        "WHERE r.ended_at IS NOT NULL AND b.mode <> 'dry_run' "
        "  AND b.items_extracted + b.items_cached + b.items_quarantined "
        "    + b.items_skipped + b.items_unclassified <> b.items_in_scope")
    assert offenders == []


async def test_actual_is_compared_against_the_projection(patched):
    """SC-008: within ±25%, or the divergence is reported."""
    run_id = await extraction_tasks.extraction_batch_record_start.fn(
        batch_kind="backfill", mode="real", scope_description="all", items_in_scope=2,
        flow_name="roach-extract-backfill",
        projection={"basis": "measured", "projection_usd": 1.0, "sample_size": 12})
    out = await extraction_tasks.extraction_batch_record_finish.fn(
        run_id=run_id,
        outcomes={"extracted": 2, "cached": 0, "quarantined": 0, "skipped": 0, "unclassified": 0},
        actual_usd=1.5)
    cmp = out["cost_comparison"]
    assert cmp["delta_pct"] == 50.0
    assert cmp["within_tolerance"] is False


async def test_uncalibrated_run_says_so_rather_than_faking_a_comparison(patched):
    run_id = await extraction_tasks.extraction_batch_record_start.fn(
        batch_kind="backfill", mode="real", scope_description="all", items_in_scope=1,
        flow_name="roach-extract-backfill")
    out = await extraction_tasks.extraction_batch_record_finish.fn(
        run_id=run_id,
        outcomes={"extracted": 1, "cached": 0, "quarantined": 0, "skipped": 0, "unclassified": 0},
        actual_usd=0.2)
    assert out["cost_comparison"]["within_tolerance"] is None
    assert "uncalibrated" in out["cost_comparison"]["note"]


# FR-041's rendering is deterministic and needs no database, so its test lives
# in test_extraction_guards.py's neighbour, test_extraction_render.py — see there.
