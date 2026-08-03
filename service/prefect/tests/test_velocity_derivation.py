"""
Velocity derivation tests (feature 006-longitudinal-metric-capture).

Two layers:
  * pure arithmetic — no database, no fixtures (irregular spacing, decreases,
    the too-short floor, scale-freeness);
  * derivation over a real disposable PostgreSQL database (idempotence, the
    upsert key, threshold changes, legacy endpoints).

The arithmetic tests are the ones that matter most. Every bug they guard
against produces numbers that look perfectly plausible and are wrong.
"""
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tasks.velocity_tasks import (  # noqa: E402
    _derive_velocity_impl,
    derive_interval,
    detect_plateau_and_acceleration,
)

NOW = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)


def _obs(offset_days, **metrics):
    row = {"observed_at": NOW + timedelta(days=offset_days),
           "views": None, "likes": None, "comments": None, "shares": None}
    row.update(metrics)
    return row


def _interval(rate_likes, withheld=None):
    return {"rate_likes_per_day": rate_likes, "rate_withheld_reason": withheld}


# ===========================================================================
# Pure arithmetic — no database
# ===========================================================================

def test_rate_uses_measured_elapsed_time_not_observation_position():
    """SC-007 / FR-013 — the failure this feature exists to prevent.

    Observations arrive monthly and ragged. If elapsed time were taken from an
    observation's position in the series rather than its timestamp, both
    intervals below would report the same per-day rate despite spanning 2 days
    and 26 days.
    """
    short = derive_interval(_obs(0, likes=100), _obs(2, likes=200))
    long = derive_interval(_obs(2, likes=200), _obs(28, likes=300))

    assert short["elapsed_seconds"] == 2 * 86400
    assert long["elapsed_seconds"] == 26 * 86400
    assert short["rates"]["likes"] == pytest.approx(50.0)
    assert long["rates"]["likes"] == pytest.approx(100 / 26)
    assert short["rates"]["likes"] != long["rates"]["likes"], (
        "equal rates here would mean spacing was assumed uniform"
    )


def test_rate_times_days_reconstructs_the_delta():
    """The invariant quickstart asks an operator to spot-check by eye."""
    result = derive_interval(_obs(0, likes=10), _obs(7, likes=101))
    days = result["elapsed_seconds"] / 86400
    assert result["rates"]["likes"] * days == pytest.approx(result["deltas"]["likes"])


def test_a_decrease_is_stored_as_negative_not_clamped():
    """FR-012. Comments get deleted and view counters get revised; clamping to
    zero would fabricate an observation nobody made."""
    result = derive_interval(_obs(0, comments=50), _obs(10, comments=30))
    assert result["deltas"]["comments"] == -20
    assert result["rates"]["comments"] == pytest.approx(-2.0)


def test_missing_endpoint_yields_null_delta_not_a_delta_against_zero():
    """"Not published" and "was zero" are different facts — feature 005 exists
    to keep them apart, and velocity must not quietly merge them."""
    result = derive_interval(_obs(0, likes=10), _obs(5, likes=20, views=500))
    assert result["deltas"]["likes"] == 10
    assert result["deltas"]["views"] is None
    assert result["rates"]["views"] is None


def test_sub_24h_interval_withholds_rate_but_keeps_deltas():
    """FR-014. Delta-present/rate-absent is a valid, meaningful state: the
    change was observed, the rate would be an artefact of dividing by a
    fraction of a day."""
    result = derive_interval(_obs(0, likes=100), _obs(0.25, likes=103))

    assert result["deltas"]["likes"] == 3
    assert result["rates"]["likes"] is None
    assert result["rate_withheld_reason"] == "interval_too_short"


def test_exactly_24h_is_rated():
    """Boundary is inclusive — 24h is the minimum, not just above it."""
    result = derive_interval(_obs(0, likes=100), _obs(1, likes=110))
    assert result["rate_withheld_reason"] is None
    assert result["rates"]["likes"] == pytest.approx(10.0)


def test_zero_delta_is_a_rate_of_zero_not_a_missing_rate():
    result = derive_interval(_obs(0, likes=100), _obs(10, likes=100))
    assert result["deltas"]["likes"] == 0
    assert result["rates"]["likes"] == 0.0


# ---------------------------------------------------------------------------
# Plateau / acceleration
# ---------------------------------------------------------------------------

def test_acceleration_after_plateau_is_flagged_with_its_antecedent():
    """FR-015. Peak 100; the plateau interval is at 5 (<=10% of peak); the next
    is 30, which is 6x the plateau rate."""
    intervals = [_interval(100.0), _interval(5.0), _interval(30.0)]

    verdicts = detect_plateau_and_acceleration(intervals, metric="likes")

    assert verdicts[1]["is_plateau"] is True
    assert verdicts[2]["is_acceleration"] is True
    assert verdicts[2]["plateau_index"] == 1, "a verdict must name the plateau it rose from"
    assert verdicts[0]["is_acceleration"] is False


def test_no_verdict_from_fewer_than_two_rated_intervals():
    """Three observations minimum, because the verdict is a comparison BETWEEN
    intervals. Fewer means no verdict — and specifically not a verdict of
    'no acceleration'."""
    verdicts = detect_plateau_and_acceleration([_interval(10.0)], metric="likes")
    assert verdicts[0]["is_plateau"] is False
    assert verdicts[0]["is_acceleration"] is False


def test_withheld_interval_is_excluded_and_never_counts_as_a_plateau():
    """FR-015a. An interval with no rate is not a low rate. Counting it as a
    plateau would manufacture acceleration out of two same-day captures."""
    intervals = [_interval(100.0), _interval(None, withheld="interval_too_short"), _interval(30.0)]

    verdicts = detect_plateau_and_acceleration(intervals, metric="likes")

    assert verdicts[1]["is_plateau"] is False
    assert verdicts[2]["is_acceleration"] is False, (
        "the withheld interval must not act as the plateau antecedent"
    )


def test_thresholds_are_scale_free_across_account_sizes():
    """Account baselines here differ ~8x. An absolute threshold would fire
    constantly on large accounts and never on small ones, making the flag a
    measure of account size rather than of the content."""
    small = [_interval(10.0), _interval(0.5), _interval(3.0)]
    large = [_interval(10_000.0), _interval(500.0), _interval(3_000.0)]

    small_verdicts = detect_plateau_and_acceleration(small, metric="likes")
    large_verdicts = detect_plateau_and_acceleration(large, metric="likes")

    assert [v["is_plateau"] for v in small_verdicts] == [v["is_plateau"] for v in large_verdicts]
    assert [v["is_acceleration"] for v in small_verdicts] == [v["is_acceleration"] for v in large_verdicts]
    assert small_verdicts[2]["is_acceleration"] is True


def test_a_rise_that_is_not_from_a_plateau_is_not_acceleration():
    """Steady growth is not late acceleration. Without the plateau precondition
    the flag would fire on any ordinary increase."""
    intervals = [_interval(10.0), _interval(20.0), _interval(40.0)]
    verdicts = detect_plateau_and_acceleration(intervals, metric="likes")
    assert not any(v["is_acceleration"] for v in verdicts)


def test_all_non_positive_rates_yield_no_verdict():
    """"Plateau at <=10% of peak" is meaningless against a non-positive peak.
    No verdict beats one derived from a sign flip."""
    intervals = [_interval(-5.0), _interval(-1.0), _interval(-20.0)]
    verdicts = detect_plateau_and_acceleration(intervals, metric="likes")
    assert not any(v["is_plateau"] or v["is_acceleration"] for v in verdicts)


# ===========================================================================
# Derivation over a real database
# ===========================================================================

async def _seed(conn, content_id, points, platform="instagram", provenance="captured"):
    for offset_days, likes in points:
        await conn.execute(
            "INSERT INTO metric_observations (platform, content_id, observed_at, provenance, likes) "
            "VALUES ($1, $2, $3, $4, $5)",
            platform, content_id, NOW + timedelta(days=offset_days), provenance, likes,
        )


@pytest.mark.schema
@pytest.mark.asyncio
async def test_single_observation_yields_no_interval_and_is_not_zero_velocity(spine_db):
    await _seed(spine_db, "lonely", [(0, 10)])

    summary = await _derive_velocity_impl(spine_db)

    assert summary["items_insufficient_observations"] == 1
    assert summary["intervals_derived"] == 0
    assert await spine_db.fetchval("SELECT count(*) FROM velocity_intervals") == 0


@pytest.mark.schema
@pytest.mark.asyncio
async def test_derivation_is_idempotent(spine_db):
    """FR-016c. Keyed on the ordered observation pair, so a re-run recomputes
    the same rows rather than accumulating duplicates."""
    await _seed(spine_db, "idem", [(0, 10), (30, 100)])

    first = await _derive_velocity_impl(spine_db)
    second = await _derive_velocity_impl(spine_db)

    assert first["intervals_derived"] == 1
    assert second["intervals_derived"] == 0
    assert second["intervals_updated"] == 1
    assert await spine_db.fetchval("SELECT count(*) FROM velocity_intervals") == 1


@pytest.mark.schema
@pytest.mark.asyncio
async def test_rederivation_under_a_new_config_version_updates_in_place(spine_db, monkeypatch):
    await _seed(spine_db, "cfg", [(0, 10), (30, 100)])
    await _derive_velocity_impl(spine_db)

    monkeypatch.setenv("VELOCITY_CONFIG_VERSION", "v2")
    await _derive_velocity_impl(spine_db)

    rows = await spine_db.fetch("SELECT config_version FROM velocity_intervals")
    assert len(rows) == 1, "a threshold change must not duplicate the interval"
    assert rows[0]["config_version"] == "v2", (
        "the stored version must say which thresholds produced the verdict"
    )


@pytest.mark.schema
@pytest.mark.asyncio
async def test_irregular_spacing_persists_distinct_rates(spine_db):
    """The end-to-end form of the arithmetic test above."""
    await _seed(spine_db, "ragged", [(0, 100), (2, 200), (28, 300)])

    await _derive_velocity_impl(spine_db)

    rows = await spine_db.fetch(
        "SELECT elapsed_seconds, delta_likes, rate_likes_per_day FROM velocity_intervals "
        "WHERE content_id = 'ragged' ORDER BY elapsed_seconds"
    )
    assert [r["elapsed_seconds"] for r in rows] == [2 * 86400, 26 * 86400]
    assert rows[0]["rate_likes_per_day"] == pytest.approx(50.0)
    assert rows[1]["rate_likes_per_day"] == pytest.approx(100 / 26)


@pytest.mark.schema
@pytest.mark.asyncio
async def test_legacy_endpoint_is_usable_and_stays_identifiable(spine_db):
    """FR-023a. A pre-feature value can anchor the earlier end of an interval,
    but the interval must remain traceable to a legacy observation — it rests
    on a value captured at an unknown point in the item's life."""
    await spine_db.execute(
        "INSERT INTO metric_observations (platform, content_id, observed_at, provenance, likes) "
        "VALUES ('instagram', 'mixed', $1, 'legacy', 40)", NOW,
    )
    await spine_db.execute(
        "INSERT INTO metric_observations (platform, content_id, observed_at, provenance, likes) "
        "VALUES ('instagram', 'mixed', $1, 'captured', 100)", NOW + timedelta(days=30),
    )

    summary = await _derive_velocity_impl(spine_db)

    row = await spine_db.fetchrow(
        """
        SELECT v.delta_likes, o.provenance AS from_provenance
        FROM velocity_intervals v
        JOIN metric_observations o ON o.id = v.from_observation_id
        WHERE v.content_id = 'mixed'
        """
    )
    assert summary["intervals_derived"] == 1
    assert row["delta_likes"] == 60
    assert row["from_provenance"] == "legacy"


@pytest.mark.schema
@pytest.mark.asyncio
async def test_sub_day_pair_stores_deltas_with_no_rate(spine_db):
    await _seed(spine_db, "sameday", [(0, 100), (0.25, 103)])

    await _derive_velocity_impl(spine_db)

    row = await spine_db.fetchrow(
        "SELECT delta_likes, rate_likes_per_day, rate_withheld_reason "
        "FROM velocity_intervals WHERE content_id = 'sameday'"
    )
    assert row["delta_likes"] == 3
    assert row["rate_likes_per_day"] is None
    assert row["rate_withheld_reason"] == "interval_too_short"


@pytest.mark.schema
@pytest.mark.asyncio
async def test_acceleration_persists_with_its_plateau_reference(spine_db):
    # peak 10/day, then a plateau at 0.5/day, then 3/day (6x the plateau)
    await _seed(spine_db, "accel", [(0, 0), (10, 100), (20, 105), (30, 135)])

    summary = await _derive_velocity_impl(spine_db)

    row = await spine_db.fetchrow(
        "SELECT id, is_acceleration, plateau_interval_id FROM velocity_intervals "
        "WHERE content_id = 'accel' AND is_acceleration"
    )
    assert summary["accelerations_detected"] == 1
    assert row["plateau_interval_id"] is not None
    plateau = await spine_db.fetchrow(
        "SELECT is_plateau FROM velocity_intervals WHERE id = $1", row["plateau_interval_id"]
    )
    assert plateau["is_plateau"] is True


@pytest.mark.schema
@pytest.mark.asyncio
async def test_platform_filter_scopes_derivation(spine_db):
    await _seed(spine_db, "ig", [(0, 10), (30, 100)], platform="instagram")
    await _seed(spine_db, "tt", [(0, 10), (30, 100)], platform="tiktok")

    summary = await _derive_velocity_impl(spine_db, platform="tiktok")

    assert summary["items_considered"] == 1
    rows = await spine_db.fetch("SELECT platform FROM velocity_intervals")
    assert [r["platform"] for r in rows] == ["tiktok"]
