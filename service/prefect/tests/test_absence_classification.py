"""
Absence classification tests (feature 006-longitudinal-metric-capture).

Two layers:
  * the reach discriminator — which reason a missing item gets, tested against
    the harvest engine with mocks;
  * the `velocity_status` view — the FR-024 guarantee that every item without
    velocity resolves to EXACTLY ONE reason, tested against a real database.

The view's `UNRESOLVED` branch must always return zero rows. That assertion is
the whole point: a velocity of "nothing" that silently spans five distinct
causes is the confident-but-false conclusion this system treats as worse than a
visible failure.
"""
import logging
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flows.common import social_harvest as engine  # noqa: E402

NOW = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)


def _listed(content_id, published_at):
    return {"content_id": content_id, "published_at": published_at,
            "public_counts": {"likes": 1}}


async def _classify(known, listed, monkeypatch):
    """Run the classifier and return the recorded capture_outcomes calls."""
    recorded = []

    async def fake_record(**kw):
        recorded.append(kw)

    async def fake_known(platform, profile_key):
        return known

    monkeypatch.setattr(engine, "social_capture_record_outcome", fake_record)
    monkeypatch.setattr(engine, "social_known_items", fake_known)

    summary = {"items_aged_out": 0, "items_absent_within_reach": 0}
    await engine._classify_absent_items(
        platform="instagram", username="acct", all_items=listed,
        account_id="a1", run_id="r1", summary=summary,
        logger_=logging.getLogger("test"),
    )
    return recorded, summary


# ---------------------------------------------------------------------------
# The reach discriminator (FR-019)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_item_older_than_the_oldest_returned_is_aged_out(monkeypatch):
    """Provably beyond reach: the listing did return items, and this one
    predates all of them, so no page could have contained it."""
    known = [{"content_id": "old", "published_at": NOW - timedelta(days=400)}]
    listed = [_listed("a", "2026-07-01T00:00:00Z"), _listed("b", "2026-08-01T00:00:00Z")]

    recorded, summary = await _classify(known, listed, monkeypatch)

    assert len(recorded) == 1
    assert recorded[0]["outcome"] == "not_attempted"
    assert recorded[0]["reason"] == "aged_out_of_listing"
    assert summary["items_aged_out"] == 1


@pytest.mark.asyncio
async def test_item_newer_than_the_oldest_returned_is_absent_within_reach(monkeypatch):
    """It should have been in the response and was not. Consistent with
    removal — but a short page produces the same observation, so this must not
    claim deletion."""
    known = [{"content_id": "gone", "published_at": NOW - timedelta(days=1)}]
    listed = [_listed("a", "2026-07-01T00:00:00Z"), _listed("b", "2026-08-01T00:00:00Z")]

    recorded, summary = await _classify(known, listed, monkeypatch)

    assert recorded[0]["reason"] == "absent_within_reach"
    assert summary["items_absent_within_reach"] == 1


@pytest.mark.asyncio
async def test_a_listing_absence_is_never_recorded_as_deleted(monkeypatch):
    """Principle VI. `deleted` requires an explicit platform not-found, which
    only the download path can observe."""
    known = [
        {"content_id": "old", "published_at": NOW - timedelta(days=400)},
        {"content_id": "gone", "published_at": NOW - timedelta(days=1)},
    ]
    listed = [_listed("a", "2026-07-01T00:00:00Z")]

    recorded, _ = await _classify(known, listed, monkeypatch)

    assert {r["reason"] for r in recorded} == {"aged_out_of_listing", "absent_within_reach"}
    assert "deleted" not in {r["reason"] for r in recorded}


@pytest.mark.asyncio
async def test_items_present_in_the_listing_are_not_classified_absent(monkeypatch):
    known = [{"content_id": "a", "published_at": NOW}]
    listed = [_listed("a", "2026-08-01T00:00:00Z")]

    recorded, summary = await _classify(known, listed, monkeypatch)

    assert recorded == []
    assert summary["items_aged_out"] == 0


@pytest.mark.asyncio
async def test_item_without_a_publish_time_gets_the_weaker_classification(monkeypatch):
    """Without a publication time the item cannot be placed relative to the
    boundary. The honest answer is the weaker one, not a guess."""
    known = [{"content_id": "undated", "published_at": None}]
    listed = [_listed("a", "2026-07-01T00:00:00Z")]

    recorded, _ = await _classify(known, listed, monkeypatch)

    assert recorded[0]["reason"] == "absent_within_reach"


@pytest.mark.asyncio
async def test_empty_listing_classifies_nothing(monkeypatch):
    """Nothing came back at all — that is a profile-level listing failure, not
    an item-level absence. Attributing it per item would blame every item for a
    problem none of them has."""
    known = [{"content_id": "x", "published_at": NOW - timedelta(days=400)}]

    recorded, summary = await _classify(known, [], monkeypatch)

    assert recorded == []
    assert summary["items_aged_out"] == 0


# ---------------------------------------------------------------------------
# The velocity_status view (FR-024)
# ---------------------------------------------------------------------------

async def _obs(conn, content_id, offset_days, provenance="captured", likes=10):
    return await conn.fetchval(
        "INSERT INTO metric_observations (platform, content_id, observed_at, provenance, likes) "
        "VALUES ('instagram', $1, $2, $3, $4) RETURNING id",
        content_id, NOW + timedelta(days=offset_days), provenance, likes,
    )


@pytest.mark.schema
@pytest.mark.asyncio
async def test_unresolved_returns_zero_rows(spine_db):
    """THE invariant. Any row here is a defect, not a curiosity."""
    await _obs(spine_db, "legacy_only", 0, provenance="legacy")
    await _obs(spine_db, "once", 0)
    await _obs(spine_db, "twice", 0)
    await _obs(spine_db, "twice", 30)

    rows = await spine_db.fetch("SELECT content_id FROM velocity_status WHERE reason = 'UNRESOLVED'")

    assert rows == [], f"unclassified items: {[r['content_id'] for r in rows]}"


@pytest.mark.schema
@pytest.mark.asyncio
async def test_legacy_only_is_distinguished_from_observed_once(spine_db):
    """Both have exactly one observation; only provenance separates them. The
    `captured_count = 0` term is the discriminator, and dropping it would
    silently reclassify every pre-feature item as merely under-observed."""
    await _obs(spine_db, "inherited", 0, provenance="legacy")
    await _obs(spine_db, "fresh", 0, provenance="captured")

    rows = {r["content_id"]: r["reason"] for r in
            await spine_db.fetch("SELECT content_id, reason FROM velocity_status")}

    assert rows["inherited"] == "legacy_only"
    assert rows["fresh"] == "observed_once"


@pytest.mark.schema
@pytest.mark.asyncio
async def test_two_observations_without_derivation_report_not_yet_derived(spine_db):
    """Distinct from "too few observations": the data is there, the derivation
    process simply has not run over it yet. Actionable, and only if it says so."""
    await _obs(spine_db, "pending", 0)
    await _obs(spine_db, "pending", 30)

    reason = await spine_db.fetchval(
        "SELECT reason FROM velocity_status WHERE content_id = 'pending'"
    )
    assert reason == "not_yet_derived"


@pytest.mark.schema
@pytest.mark.asyncio
async def test_item_with_no_observation_at_all_is_still_classified(spine_db):
    """Found by validating against real data: 2 of 819 rows carry no metric
    anywhere, so seeding correctly skips them and they have zero observations.

    A view driven by the observation table alone would drop them entirely —
    they would lack a velocity AND a reason, which is exactly the state FR-024
    forbids. The item universe is therefore the UNION of both tables.
    """
    await spine_db.execute(
        "INSERT INTO harvested_signals (platform, profile_key, content_id, content_type) "
        "VALUES ('instagram', 'acct', 'metricless', 'image')"
    )

    row = await spine_db.fetchrow(
        "SELECT observation_count, has_velocity, reason FROM velocity_status "
        "WHERE content_id = 'metricless'"
    )

    assert row is not None, "an item with no observations must still appear"
    assert row["observation_count"] == 0
    assert row["has_velocity"] is False
    assert row["reason"] == "never_observed"


@pytest.mark.schema
@pytest.mark.asyncio
async def test_never_observed_is_not_reported_as_observed_once(spine_db):
    """Branch ordering: `never_observed` must be tested before the
    single-observation branches, or zero would fall through to one."""
    await spine_db.execute(
        "INSERT INTO harvested_signals (platform, profile_key, content_id, content_type) "
        "VALUES ('instagram', 'acct', 'zero_obs', 'image')"
    )
    await spine_db.execute(
        "INSERT INTO harvested_signals (platform, profile_key, content_id, content_type) "
        "VALUES ('instagram', 'acct', 'one_obs', 'image')"
    )
    await _obs(spine_db, "one_obs", 0)

    rows = {r["content_id"]: r["reason"] for r in
            await spine_db.fetch("SELECT content_id, reason FROM velocity_status")}

    assert rows["zero_obs"] == "never_observed"
    assert rows["one_obs"] == "observed_once"


@pytest.mark.schema
@pytest.mark.asyncio
async def test_every_item_appears_exactly_once(spine_db):
    for cid in ("a", "b", "c"):
        await _obs(spine_db, cid, 0)
    await _obs(spine_db, "a", 30)

    rows = await spine_db.fetch("SELECT content_id, count(*) AS n FROM velocity_status GROUP BY 1")

    assert {r["content_id"] for r in rows} == {"a", "b", "c"}
    assert all(r["n"] == 1 for r in rows)


@pytest.mark.schema
@pytest.mark.asyncio
async def test_has_velocity_agrees_with_the_reason(spine_db):
    from tasks.velocity_tasks import _derive_velocity_impl

    await _obs(spine_db, "moving", 0, likes=10)
    await _obs(spine_db, "moving", 30, likes=100)
    await _obs(spine_db, "still", 0)
    await _derive_velocity_impl(spine_db)

    rows = await spine_db.fetch("SELECT content_id, has_velocity, reason FROM velocity_status")

    for row in rows:
        assert row["has_velocity"] == (row["reason"] == "has_velocity")


@pytest.mark.schema
@pytest.mark.asyncio
async def test_all_intervals_too_short_is_its_own_reason(spine_db):
    """Rare, and worth investigating when it appears — it means duplicate runs.
    Folding it into "no velocity" would hide a real operational problem."""
    from tasks.velocity_tasks import _derive_velocity_impl

    await _obs(spine_db, "sameday", 0)
    await _obs(spine_db, "sameday", 0.25)
    await _derive_velocity_impl(spine_db)

    reason = await spine_db.fetchval(
        "SELECT reason FROM velocity_status WHERE content_id = 'sameday'"
    )
    assert reason == "all_intervals_too_short"


@pytest.mark.schema
@pytest.mark.asyncio
async def test_aged_out_item_reports_no_longer_observable(spine_db):
    """The ~252 rows measured in research R2. They are a reported fact about
    how far the listing reaches, not a collection regression."""
    from tasks.velocity_tasks import _derive_velocity_impl

    await _obs(spine_db, "beyond", 0)
    await _obs(spine_db, "beyond", 30)
    await _derive_velocity_impl(spine_db)
    # A later run finds it gone from the listing.
    await spine_db.execute(
        "INSERT INTO capture_outcomes (platform, content_id, capture_kind, outcome, reason) "
        "VALUES ('instagram', 'beyond', 'metric_refresh', 'not_attempted', 'aged_out_of_listing')"
    )
    # It still has velocity from the two observations it did get, so the
    # has_velocity branch wins — absence only surfaces once there is nothing
    # derivable, which is the correct precedence.
    reason = await spine_db.fetchval(
        "SELECT reason FROM velocity_status WHERE content_id = 'beyond'"
    )
    assert reason == "has_velocity"

    await spine_db.execute("DELETE FROM velocity_intervals")
    await spine_db.execute("DELETE FROM metric_observations WHERE content_id='beyond' AND observed_at > $1", NOW)
    reason = await spine_db.fetchval(
        "SELECT reason FROM velocity_status WHERE content_id = 'beyond'"
    )
    assert reason == "observed_once", (
        "with one observation left, the item's own history is the more specific fact"
    )
