"""
Metric observation store tests (feature 006-longitudinal-metric-capture).

Runs against a real disposable PostgreSQL database, per
`.claude/rules/backend/schema.md`: a CHECK constraint that passes on NULL, a
partial unique index, and an ON CONFLICT DO NOTHING that should have been DO
UPDATE are all bugs that pass happily against a mock.

The invariants asserted here are the ones that make velocity trustworthy. If
any of them stops holding, the derived numbers keep being produced and quietly
stop meaning what they claim.
"""
import os
import sys
from datetime import datetime, timedelta, timezone

import asyncpg
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tasks.social_tasks import social_observation_record, social_signal_record_metrics  # noqa: E402
from tasks.velocity_tasks import _observation_backfill_impl  # noqa: E402
from tests.conftest import pool_from_connection  # noqa: E402

# `schema` gates these on a reachable database (conftest auto-skips otherwise);
# `asyncio` is applied module-wide rather than per-test, since every test here
# is async.
pytestmark = [pytest.mark.schema, pytest.mark.asyncio]

NOW = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)


async def _insert_signal(conn, content_id, *, views=None, likes=10, comments=None,
                         shares=None, harvested_at=NOW, platform="instagram",
                         subtitle="sub", content_flow="flow", summary="summary"):
    await conn.execute(
        """
        INSERT INTO harvested_signals
            (platform, profile_key, content_id, content_type, views, likes, comments,
             shares, subtitle, content_flow, summary, harvested_at)
        VALUES ($1, 'acct', $2, 'video', $3, $4, $5, $6, $7, $8, $9, $10)
        """,
        platform, content_id, views, likes, comments, shares,
        subtitle, content_flow, summary, harvested_at,
    )


# ---------------------------------------------------------------------------
# Schema constraints
# ---------------------------------------------------------------------------

async def test_all_null_observation_is_rejected(spine_db):
    """An observation recording nothing is not an observation.

    The correct record for "the capture produced no counts" is a
    capture_outcomes row with outcome='no_match'. Storing an all-NULL
    observation instead would make the series look longer than the evidence
    supports — and would make an item appear to have history it does not.
    """
    with pytest.raises(asyncpg.CheckViolationError):
        await spine_db.execute(
            "INSERT INTO metric_observations (platform, content_id) VALUES ('instagram', 'x1')"
        )


async def test_zero_is_a_real_value_and_is_not_absence(spine_db):
    """0 likes is an observation; NULL likes is the absence of one.

    Conflating them is the Principle VI failure feature 005 exists to prevent,
    and it would make a delta of 0 indistinguishable from an uncomputable one.
    """
    await spine_db.execute(
        "INSERT INTO metric_observations (platform, content_id, likes) VALUES ('instagram', 'zero', 0)"
    )
    row = await spine_db.fetchrow(
        "SELECT likes, views FROM metric_observations WHERE content_id = 'zero'"
    )
    assert row["likes"] == 0
    assert row["views"] is None


async def test_provenance_vocabulary_is_closed(spine_db):
    with pytest.raises(asyncpg.CheckViolationError):
        await spine_db.execute(
            "INSERT INTO metric_observations (platform, content_id, likes, provenance) "
            "VALUES ('instagram', 'p1', 5, 'guessed')"
        )


async def test_platform_vocabulary_is_closed(spine_db):
    with pytest.raises(asyncpg.CheckViolationError):
        await spine_db.execute(
            "INSERT INTO metric_observations (platform, content_id, likes) "
            "VALUES ('threads', 'p2', 5)"
        )


async def test_same_instant_reinsert_is_a_noop(spine_db, monkeypatch):
    """Makes the harvest observation pass safely retryable.

    A retried run must not double-count an item's observation — that would
    create a zero-elapsed interval, which chk_velocity_elapsed_positive forbids
    outright.
    """
    monkeypatch.setattr("tasks.social_tasks._db_pool", pool_from_connection(spine_db))

    first = await social_observation_record.fn(
        platform="instagram", content_id="dup", likes=10, observed_at=NOW.isoformat()
    )
    second = await social_observation_record.fn(
        platform="instagram", content_id="dup", likes=99, observed_at=NOW.isoformat()
    )

    assert first is not None
    assert second is None, "a same-instant re-insert must not create a second row"

    rows = await spine_db.fetch("SELECT likes FROM metric_observations WHERE content_id = 'dup'")
    assert len(rows) == 1
    assert rows[0]["likes"] == 10, "the original observation must not be rewritten (append-only)"


async def test_distinct_instants_create_distinct_rows(spine_db, monkeypatch):
    """Two runs in one day are allowed to both observe.

    The sub-24h pair is handled by withholding the RATE (FR-014), not by
    suppressing the observation — suppressing it would discard a real capture.
    """
    monkeypatch.setattr("tasks.social_tasks._db_pool", pool_from_connection(spine_db))

    await social_observation_record.fn(
        platform="instagram", content_id="twice", likes=10, observed_at=NOW.isoformat()
    )
    await social_observation_record.fn(
        platform="instagram", content_id="twice", likes=12,
        observed_at=(NOW + timedelta(hours=3)).isoformat(),
    )

    count = await spine_db.fetchval(
        "SELECT count(*) FROM metric_observations WHERE content_id = 'twice'"
    )
    assert count == 2


# ---------------------------------------------------------------------------
# Task behaviour
# ---------------------------------------------------------------------------

async def test_record_returns_none_when_no_counts(spine_db, monkeypatch):
    monkeypatch.setattr("tasks.social_tasks._db_pool", pool_from_connection(spine_db))

    result = await social_observation_record.fn(platform="instagram", content_id="empty")

    assert result is None
    assert await spine_db.fetchval("SELECT count(*) FROM metric_observations") == 0


async def test_counts_are_coerced_consistently(spine_db, monkeypatch):
    """"1.2K" and 1200 must land identically.

    Two different parses of the same number would register phantom movement as
    a delta between consecutive observations — an entirely fabricated velocity.
    """
    monkeypatch.setattr("tasks.social_tasks._db_pool", pool_from_connection(spine_db))

    await social_observation_record.fn(
        platform="instagram", content_id="k", likes="1.2K", observed_at=NOW.isoformat()
    )
    stored = await spine_db.fetchval("SELECT likes FROM metric_observations WHERE content_id = 'k'")
    assert stored == 1200


async def test_unknown_provenance_is_rejected_before_the_database(spine_db, monkeypatch):
    monkeypatch.setattr("tasks.social_tasks._db_pool", pool_from_connection(spine_db))
    with pytest.raises(ValueError, match="provenance"):
        await social_observation_record.fn(
            platform="instagram", content_id="bad", likes=1, provenance="assumed"
        )


# ---------------------------------------------------------------------------
# FR-010a — a refresh must not erase what it did not observe
# ---------------------------------------------------------------------------

async def test_metrics_only_update_preserves_analysis_and_reviewer_fields(spine_db, monkeypatch):
    """The single most destructive thing this feature could do.

    `social.signal.record` sets subtitle/content_flow/summary to EXCLUDED
    unconditionally. A refresh carries no analysis, so routing one through that
    task would blank stored analysis on every refreshed row — silently, and
    irrecoverably: re-running analysis is out of scope for this feature and
    would cost model spend FR-025 forbids.
    """
    monkeypatch.setattr("tasks.social_tasks._db_pool", pool_from_connection(spine_db))
    await _insert_signal(spine_db, "keep", likes=10, subtitle="S", content_flow="F", summary="M")
    await spine_db.execute("UPDATE harvested_signals SET advertisement = true WHERE content_id = 'keep'")

    updated = await social_signal_record_metrics.fn(
        platform="instagram", content_id="keep", likes=99
    )

    row = await spine_db.fetchrow(
        "SELECT likes, subtitle, content_flow, summary, advertisement "
        "FROM harvested_signals WHERE content_id = 'keep'"
    )
    assert updated is True
    assert row["likes"] == 99, "the metric must refresh"
    assert row["subtitle"] == "S"
    assert row["content_flow"] == "F"
    assert row["summary"] == "M"
    assert row["advertisement"] is True, "reviewer-assigned flag must survive a refresh"


async def test_metrics_only_update_does_not_erase_a_count_that_stopped_being_returned(spine_db, monkeypatch):
    """Absence is not evidence of zero — the `shares` precedent, generalized."""
    monkeypatch.setattr("tasks.social_tasks._db_pool", pool_from_connection(spine_db))
    await _insert_signal(spine_db, "coalesce", views=500, likes=10)

    await social_signal_record_metrics.fn(platform="instagram", content_id="coalesce", likes=11)

    row = await spine_db.fetchrow(
        "SELECT views, likes FROM harvested_signals WHERE content_id = 'coalesce'"
    )
    assert row["views"] == 500, "a metric absent from this refresh must not be nulled"
    assert row["likes"] == 11


async def test_metrics_only_update_reports_unknown_item(spine_db, monkeypatch):
    monkeypatch.setattr("tasks.social_tasks._db_pool", pool_from_connection(spine_db))
    assert await social_signal_record_metrics.fn(platform="instagram", content_id="nope", likes=1) is False


# ---------------------------------------------------------------------------
# Legacy seeding (FR-021)
# ---------------------------------------------------------------------------

async def test_seeding_uses_the_recorded_harvest_time_not_now(spine_db):
    """Stamping the seed with now() would assert the value was observed at a
    time it was not — Principle VI, and the check quickstart step 3 makes an
    operator run by eye."""
    harvested = datetime(2026, 7, 15, 8, 30, tzinfo=timezone.utc)
    await _insert_signal(spine_db, "legacy1", likes=42, harvested_at=harvested)

    result = await _observation_backfill_impl(spine_db)

    row = await spine_db.fetchrow(
        "SELECT observed_at, provenance, likes FROM metric_observations WHERE content_id = 'legacy1'"
    )
    assert result["seeded"] == 1
    assert row["observed_at"] == harvested
    assert row["provenance"] == "legacy"
    assert row["likes"] == 42


async def test_seeding_is_idempotent(spine_db):
    await _insert_signal(spine_db, "legacy2", likes=7)

    first = await _observation_backfill_impl(spine_db)
    second = await _observation_backfill_impl(spine_db)

    assert first["seeded"] == 1
    assert second["seeded"] == 0
    assert second["candidates"] == 0
    assert await spine_db.fetchval(
        "SELECT count(*) FROM metric_observations WHERE content_id = 'legacy2'"
    ) == 1


async def test_seeding_skips_rows_with_no_metric_at_all(spine_db):
    """Reported separately rather than silently dropped, so "nothing to do
    because everything is seeded" stays distinguishable from "nothing to do
    because these rows have nothing to observe"."""
    await _insert_signal(spine_db, "nometrics", views=None, likes=None, comments=None, shares=None)

    result = await _observation_backfill_impl(spine_db)

    assert result["seeded"] == 0
    assert result["skipped_no_metrics"] == 1
    assert await spine_db.fetchval("SELECT count(*) FROM metric_observations") == 0


# ---------------------------------------------------------------------------
# FR-005 — observation history survives the failure-retry purge
# ---------------------------------------------------------------------------
# Lives here rather than in test_social_harvest.py (which is mock-based) because
# the assertion is fundamentally about the SCHEMA: whether deleting a ledger or
# signal row can reach metric_observations through a cascade. That is only
# answerable against a real database.

async def test_purging_the_dedup_ledger_does_not_touch_observations(spine_db):
    """The failure-retry path deletes an item's ledger record and re-harvests.

    The media was bad; the past measurements were not. This holds today because
    metric_observations is keyed by (platform, content_id) and carries no FK to
    harvested_items — but nothing except this test would notice if someone added
    one with ON DELETE CASCADE.
    """
    await spine_db.execute(
        "INSERT INTO harvested_items (platform, profile_key, content_id, drive_target, "
        "content_type, analysis_status) VALUES ('instagram', 'acct', 'purge', 'folder', 'video', 'failed')"
    )
    await spine_db.execute(
        "INSERT INTO metric_observations (platform, content_id, likes, observed_at) "
        "VALUES ('instagram', 'purge', 5, $1), ('instagram', 'purge', 9, $2)",
        NOW, NOW + timedelta(days=30),
    )

    await spine_db.execute(
        "DELETE FROM harvested_items WHERE platform = 'instagram' AND content_id = 'purge'"
    )

    surviving = await spine_db.fetchval(
        "SELECT count(*) FROM metric_observations WHERE content_id = 'purge'"
    )
    assert surviving == 2, "the purge must not reach the observation store (FR-005)"


async def test_deleting_a_signal_row_does_not_cascade_to_observations(spine_db):
    """Same invariant from the other direction: harvested_signals is a
    convenience view of the latest value, not the owner of the history."""
    await _insert_signal(spine_db, "sigdel", likes=5)
    await spine_db.execute(
        "INSERT INTO metric_observations (platform, content_id, likes, observed_at) "
        "VALUES ('instagram', 'sigdel', 5, $1)", NOW,
    )

    await spine_db.execute("DELETE FROM harvested_signals WHERE content_id = 'sigdel'")

    assert await spine_db.fetchval(
        "SELECT count(*) FROM metric_observations WHERE content_id = 'sigdel'"
    ) == 1


async def test_seeding_does_not_add_a_legacy_row_to_an_already_observed_item(spine_db):
    """Re-running after a harvest must not inject a legacy row *behind* a
    captured one — that would fabricate an earlier endpoint for an item whose
    history genuinely starts later."""
    await _insert_signal(spine_db, "observed", likes=50)
    await spine_db.execute(
        "INSERT INTO metric_observations (platform, content_id, likes, provenance, observed_at) "
        "VALUES ('instagram', 'observed', 50, 'captured', $1)", NOW,
    )

    result = await _observation_backfill_impl(spine_db)

    assert result["seeded"] == 0
    provenances = await spine_db.fetch(
        "SELECT provenance FROM metric_observations WHERE content_id = 'observed'"
    )
    assert [r["provenance"] for r in provenances] == ["captured"]
