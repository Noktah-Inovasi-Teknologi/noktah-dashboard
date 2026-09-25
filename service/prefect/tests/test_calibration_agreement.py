"""
Calibration and agreement tests (feature 007-structured-extraction).

The two properties worth testing are the ones whose failure is INVISIBLE:

  * sample fixity — a changed rate must not be ambiguous between "the model
    changed" and "the sample changed" (FR-027b);
  * exclusion of calibration rows from analytical figures — the failure mode is
    not an error, it is every distribution quietly double-weighting (SC-018).
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.conftest import pool_from_connection  # noqa: E402
from tasks import calibration_tasks, extraction_tasks  # noqa: E402

pytestmark = [pytest.mark.schema, pytest.mark.asyncio]

MODEL_A = "xiaomi/mimo-v2.5"
MODEL_B = "google/gemini-2.5-flash-lite"


async def _seed_vocabulary(db):
    for term, residual, ordinal in [
        ("hook", False, 1), ("setup", False, 2), ("main_point", False, 3),
        ("call_to_action", False, 4), ("unclassified", True, 5),
    ]:
        await db.execute(
            "INSERT INTO extraction_vocabulary_terms "
            "(version, dimension, term, is_residual, ordinal, description) "
            "VALUES ('v1','beat_function',$1,$2,$3,'d') ON CONFLICT DO NOTHING",
            term, residual, ordinal)


async def _seed_signals(db, n=4, content_type="image"):
    for i in range(n):
        await db.execute(
            "INSERT INTO harvested_signals (platform, content_id, profile_key, content_type) "
            "VALUES ('instagram',$1,'acct',$2) ON CONFLICT DO NOTHING",
            f"c{i}", content_type)


def _analysis(functions):
    return {
        "status": "success", "subtitle": "s", "subtitle_absence": None,
        "beats": [{"position": i + 1, "function": f, "description": f"beat {i}"}
                  for i, f in enumerate(functions)],
        "attributes": [], "summary": "sum",
    }


def _prov(model):
    return {
        "model_requested": model, "model_served": model, "provider": "p",
        "media_path": "image", "prompt_version": "v1", "schema_version": "v1",
        "vocabulary_version": "v1", "attempts": 1,
    }


@pytest.fixture
def patched(monkeypatch, spine_db):
    monkeypatch.setattr(calibration_tasks, "_db_pool", pool_from_connection(spine_db))
    monkeypatch.setattr(extraction_tasks, "_db_pool", pool_from_connection(spine_db))
    return spine_db


# ---------------------------------------------------------------------------
# Sample fixity (FR-027b)
# ---------------------------------------------------------------------------

async def test_sample_membership_is_stable_across_reruns(patched):
    """A re-measurement after a model change must compare THE SAME ITEMS.

    A LIMIT applied at random, or a re-populate that reshuffled, would make a
    changed agreement rate ambiguous between the new model and the new sample —
    and nothing in the output could tell the two apart.
    """
    await _seed_signals(patched, n=5)
    first = await calibration_tasks.calibration_sample_populate.fn(limit=3)
    members_1 = [dict(r) for r in await patched.fetch(
        "SELECT platform, content_id FROM calibration_sample_members ORDER BY content_id")]

    second = await calibration_tasks.calibration_sample_populate.fn(limit=3)
    members_2 = [dict(r) for r in await patched.fetch(
        "SELECT platform, content_id FROM calibration_sample_members ORDER BY content_id")]

    assert first["added"] == 3
    assert second["added"] == 0, "a re-run must add nothing and reshuffle nothing"
    assert members_1 == members_2


async def test_sample_excludes_video_and_says_so(patched):
    """The overlap is one-directional by construction: the image model cannot
    accept video. The limit is REPORTED, not worked around (FR-027a)."""
    await _seed_signals(patched, n=2, content_type="image")
    await _seed_signals(patched, n=0)
    await patched.execute(
        "INSERT INTO harvested_signals (platform, content_id, profile_key, content_type) "
        "VALUES ('instagram','v1','acct','video'), ('instagram','v2','acct','video')")

    out = await calibration_tasks.calibration_sample_populate.fn()
    assert out["members"] == 2
    assert out["excluded_items"] == 2
    assert "does NOT extend" in out["excluded_note"]

    in_sample = await patched.fetch(
        "SELECT content_id FROM calibration_sample_members ORDER BY content_id")
    assert all(r["content_id"].startswith("c") for r in in_sample)


# ---------------------------------------------------------------------------
# The minimum sample is structural (Constitution VIII, FR-027e)
# ---------------------------------------------------------------------------

async def test_below_the_minimum_no_rate_is_reported_at_all(patched):
    """Not a weak figure, not a provisional one — NONE.

    A rate from four pairs would carry more apparent authority than evidence.
    """
    await _seed_vocabulary(patched)
    await _seed_signals(patched, n=2)
    await calibration_tasks.calibration_sample_populate.fn()

    for cid in ("c0", "c1"):
        for model, funcs in ((MODEL_A, ["hook", "setup"]), (MODEL_B, ["hook", "setup"])):
            await extraction_tasks.extraction_record_store.fn(
                platform="instagram", content_id=cid, content_type="image",
                analysis=_analysis(funcs), provenance=_prov(model), usage={},
                content_hash="", purpose="calibration")

    out = await calibration_tasks.calibration_agreement_compute.fn(
        model_a=MODEL_A, model_b=MODEL_B)
    assert out["sufficient"] is False
    assert out["beat_function_agreement"] is None
    assert out["usable_pairs"] == 2
    assert "INSUFFICIENT DATA" in out["note"]
    assert "not overridable" in out["note"]


async def test_agreement_is_reported_per_position_with_its_observation_count(patched):
    """FR-027: per beat-function POSITION, each carrying its own count."""
    await _seed_vocabulary(patched)
    await _seed_signals(patched, n=4)
    await calibration_tasks.calibration_sample_populate.fn()

    # c0, c1, c2 agree on position 1; c3 disagrees there.
    plan = {
        "c0": (["hook", "setup"], ["hook", "setup"]),
        "c1": (["hook", "main_point"], ["hook", "main_point"]),
        "c2": (["hook", "setup"], ["hook", "call_to_action"]),
        "c3": (["hook", "setup"], ["setup", "setup"]),
    }
    for cid, (fa, fb) in plan.items():
        await extraction_tasks.extraction_record_store.fn(
            platform="instagram", content_id=cid, content_type="image",
            analysis=_analysis(fa), provenance=_prov(MODEL_A), usage={},
            content_hash="", purpose="calibration")
        await extraction_tasks.extraction_record_store.fn(
            platform="instagram", content_id=cid, content_type="image",
            analysis=_analysis(fb), provenance=_prov(MODEL_B), usage={},
            content_hash="", purpose="calibration")

    out = await calibration_tasks.calibration_agreement_compute.fn(
        model_a=MODEL_A, model_b=MODEL_B, min_sample=1)
    assert out["sufficient"] is True

    by_position = {r["position"]: r for r in out["beat_function_agreement"]}
    # Position 1: c0/c1/c2 agree on `hook`, c3 disagrees (A=hook, B=setup) -> 3/4.
    assert by_position[1]["observations"] == 4
    assert by_position[1]["agreements"] == 3
    assert by_position[1]["agreement_rate"] == 0.75
    # Position 2: c0 setup/setup, c1 main_point/main_point, c3 setup/setup agree;
    # only c2 (setup vs call_to_action) disagrees -> 3/4. The rate differing by
    # position is the point — a single blended figure would hide it.
    assert by_position[2]["observations"] == 4
    assert by_position[2]["agreements"] == 3

    # Disagreements carry DIRECTION, not just a count (FR-026).
    directions = {d["direction"] for d in out["disagreements"]}
    assert "A=hook B=setup" in directions
    assert "A=setup B=call_to_action" in directions
    # And no single blended score is emitted anywhere.
    assert "overall_agreement" not in out
    assert "accuracy" not in out


async def test_pairs_served_by_an_unintended_model_are_excluded_and_counted(patched):
    """allow_fallbacks means a pair can be served by some THIRD model, in which
    case the comparison is not between the two models it claims to compare."""
    await _seed_vocabulary(patched)
    await _seed_signals(patched, n=2)
    await calibration_tasks.calibration_sample_populate.fn()

    await extraction_tasks.extraction_record_store.fn(
        platform="instagram", content_id="c0", content_type="image",
        analysis=_analysis(["hook"]), provenance=_prov(MODEL_A), usage={},
        content_hash="", purpose="calibration")
    # Requested B, but a fallback provider actually served something else.
    fallback = {**_prov(MODEL_B), "model_served": "some/other-model"}
    await extraction_tasks.extraction_record_store.fn(
        platform="instagram", content_id="c0", content_type="image",
        analysis=_analysis(["setup"]), provenance=fallback, usage={},
        content_hash="", purpose="calibration")

    out = await calibration_tasks.calibration_agreement_compute.fn(
        model_a=MODEL_A, model_b=MODEL_B, min_sample=1)
    assert out["excluded_provider_fallback"] == 1
    assert out["usable_pairs"] == 0


async def test_the_report_carries_the_versions_it_was_measured_under(patched):
    """FR-027b: a figure measured under superseded versions must not read as current."""
    await _seed_vocabulary(patched)
    await _seed_signals(patched, n=1)
    await calibration_tasks.calibration_sample_populate.fn()
    for model in (MODEL_A, MODEL_B):
        await extraction_tasks.extraction_record_store.fn(
            platform="instagram", content_id="c0", content_type="image",
            analysis=_analysis(["hook"]), provenance=_prov(model), usage={},
            content_hash="", purpose="calibration")

    out = await calibration_tasks.calibration_agreement_compute.fn(
        model_a=MODEL_A, model_b=MODEL_B, min_sample=1)
    assert out["versions_measured_under"] == ["('v1', 'v1', 'v1')"]
    assert out["model_a"] == MODEL_A and out["model_b"] == MODEL_B


# ---------------------------------------------------------------------------
# SC-018 — calibration never enters an analytical figure
# ---------------------------------------------------------------------------

async def test_no_sampled_item_contributes_more_than_one_extraction_to_any_figure(patched):
    """The verification SC-018 actually names.

    Every calibration item is extracted twice. If both copies counted as
    production, every distribution over the sample would be double-weighted —
    and nothing would look wrong, which is what makes this worth a test.
    """
    await _seed_vocabulary(patched)
    await _seed_signals(patched, n=2)
    await calibration_tasks.calibration_sample_populate.fn()

    for cid in ("c0", "c1"):
        # One production extraction...
        await extraction_tasks.extraction_record_store.fn(
            platform="instagram", content_id=cid, content_type="image",
            analysis=_analysis(["hook"]), provenance=_prov(MODEL_A), usage={},
            content_hash="", purpose="production")
        # ...and the calibration pair.
        for model in (MODEL_A, MODEL_B):
            await extraction_tasks.extraction_record_store.fn(
                platform="instagram", content_id=cid, content_type="image",
                analysis=_analysis(["setup"]), provenance=_prov(model), usage={},
                content_hash="", purpose="calibration")

    assert await patched.fetchval("SELECT count(*) FROM content_extractions") == 6

    parity = await extraction_tasks.extraction_parity_report.fn()
    assert sum(r["n"] for r in parity["beat_functions_by_path"]) == 2, (
        "only the two production extractions may contribute")
    assert all(r["function"] == "hook" for r in parity["beat_functions_by_path"])

    share = await extraction_tasks.extraction_vocabulary_residual_share.fn()
    assert share[0]["beats"] == 2

    per_item = await patched.fetch(
        "SELECT content_id, count(*) FROM content_extractions "
        "WHERE purpose='production' GROUP BY 1")
    assert all(r["count"] == 1 for r in per_item)


async def test_calibration_only_items_are_visible_in_the_status_view(patched):
    """They are not corpus evidence, but they must not vanish either — an item
    with extractions and no reason is what SC-010 forbids."""
    await _seed_vocabulary(patched)
    await _seed_signals(patched, n=1)
    await extraction_tasks.extraction_record_store.fn(
        platform="instagram", content_id="c0", content_type="image",
        analysis=_analysis(["hook"]), provenance=_prov(MODEL_A), usage={},
        content_hash="", purpose="calibration")

    reason = await patched.fetchval(
        "SELECT reason FROM extraction_status WHERE content_id='c0'")
    assert reason == "calibration_only"
