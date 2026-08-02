"""
Tests for social_tasks signal capture (feature 003 additions):
- _coerce_count parsing of public-count cells
- social.signal.record issues an idempotent upsert with coerced values
DB access is mocked via a fake asyncpg pool; no real database.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

os.environ.setdefault("HARVEST_DB_URL", "postgresql://test:test@localhost/test")

from tasks import social_tasks as st


@pytest.mark.parametrize("value,expected", [
    ("", None), (None, None),
    ("1234", 1234), (1234, 1234), (12.0, 12),
    ("1.2K", 1200), ("3M", 3_000_000), ("12,345", 12345),
    ("n/a", None),
])
def test_coerce_count(value, expected):
    assert st._coerce_count(value) == expected


class _FakeConn:
    def __init__(self, capture):
        self._capture = capture

    async def execute(self, sql, *args):
        self._capture["sql"] = sql
        self._capture["args"] = args


class _FakeAcquire:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return False


class _FakePool:
    def __init__(self, conn):
        self._conn = conn

    def acquire(self):
        return _FakeAcquire(self._conn)

    async def close(self):
        pass


@pytest.fixture
def capture(monkeypatch):
    """Patch `_db_pool` onto a fake pool and hand back the captured SQL/args.

    Every test in this module opened with the same four lines; a change to
    `_FakePool`'s shape had to be re-verified at each site.
    """
    cap = {}

    async def fake_pool(*_args):
        return _FakePool(_FakeConn(cap))

    monkeypatch.setattr(st, "_db_pool", fake_pool)
    return cap


@pytest.mark.asyncio
async def test_signal_record_upserts_with_coerced_counts(capture):

    # Call the task's underlying function directly (bypass Prefect runtime).
    await st.social_signal_record.fn(
        platform="instagram", profile_key="acme", content_id="c1", content_type="video",
        published_at="2026-07-01T00:00:00Z", caption="halo", hashtags="#promo",
        views="1.2K", likes="3,400", comments="56",
        subtitle="sub", content_flow="hook", summary="ringkas",
    )

    sql = capture["sql"]
    assert "INSERT INTO harvested_signals" in sql
    assert "ON CONFLICT (platform, content_id) DO UPDATE" in sql  # idempotent (FR-013)
    args = capture["args"]
    # views/likes/comments coerced to ints (positional: see task signature order).
    assert 1200 in args and 3400 in args and 56 in args


# ==========================================================================
# Feature 005-signal-field-coverage: share counts + capture outcomes
# ==========================================================================

@pytest.mark.asyncio
async def test_signal_record_persists_shares(capture):
    """
    roach has always parsed TikTok's shareCount; it was discarded at this
    boundary. This asserts it now reaches the INSERT column list.
    """

    await st.social_signal_record.fn(
        platform="tiktok", profile_key="acme", content_id="t1", content_type="video",
        published_at="2026-07-01T00:00:00Z", caption="halo", hashtags="#promo",
        views="400", likes="12", comments="3",
        subtitle="sub", content_flow="hook", summary="ringkas",
        shares="1.2K",
    )

    assert "shares" in capture["sql"]
    assert 1200 in capture["args"], "shares must be coerced and passed to the insert"


@pytest.mark.asyncio
async def test_signal_record_zero_shares_is_not_absence(capture):
    """
    Edge case "a field is present but zero": a post genuinely shared zero times
    must store 0, never NULL. Conflating them would make a real observation
    indistinguishable from a platform limit — the exact ambiguity this feature
    removes.
    """

    await st.social_signal_record.fn(
        platform="tiktok", profile_key="acme", content_id="t2", content_type="video",
        published_at=None, caption="", hashtags="",
        views=None, likes=None, comments=None,
        subtitle=None, content_flow=None, summary=None,
        shares=0,
    )

    assert capture["args"][-1] == 0, "0 shares must be stored as 0, not coerced to None"


@pytest.mark.asyncio
async def test_signal_record_shares_absent_defaults_to_none(capture):
    """Instagram publishes no share count; it must arrive as absence, not zero."""

    await st.social_signal_record.fn(
        platform="instagram", profile_key="acme", content_id="i1", content_type="carousel",
        published_at=None, caption="", hashtags="",
        views=None, likes=10, comments=None,
        subtitle=None, content_flow=None, summary=None,
    )

    assert capture["args"][-1] is None


@pytest.mark.asyncio
async def test_signal_record_shares_upsert_does_not_erase(capture):
    """
    Unlike the other metrics, shares uses COALESCE on conflict: a platform that
    stops returning a share count must not erase one already observed. Absence
    is not evidence of zero (constitution VI).
    """

    await st.social_signal_record.fn(
        platform="tiktok", profile_key="acme", content_id="t3", content_type="video",
        published_at=None, caption="", hashtags="",
        views=1, likes=1, comments=1,
        subtitle=None, content_flow=None, summary=None, shares=5,
    )

    assert "shares = COALESCE(EXCLUDED.shares, harvested_signals.shares)" in capture["sql"]


@pytest.mark.asyncio
async def test_capture_record_outcome_rejects_unknown_vocabulary():

    with pytest.raises(ValueError, match="unknown capture_kind"):
        await st.social_capture_record_outcome.fn(
            platform="instagram", content_id="x", capture_kind="vibes", outcome="success")

    with pytest.raises(ValueError, match="unknown outcome"):
        await st.social_capture_record_outcome.fn(
            platform="instagram", content_id="x",
            capture_kind="instagram_clip_stats", outcome="probably_fine")


@pytest.mark.asyncio
async def test_capture_record_outcome_requires_classified_failure_reason():
    """FR-004: never defaulted. An unclassified failure must be refused, loudly."""

    with pytest.raises(ValueError, match="classified reason"):
        await st.social_capture_record_outcome.fn(
            platform="instagram", content_id="x",
            capture_kind="instagram_clip_stats", outcome="failed")

    with pytest.raises(ValueError, match="classified reason"):
        await st.social_capture_record_outcome.fn(
            platform="instagram", content_id="x", capture_kind="instagram_clip_stats",
            outcome="failed", reason="it broke somehow")


@pytest.mark.asyncio
async def test_capture_record_outcome_is_insert_only(capture):
    """
    FR-003a. If this ever becomes an upsert the attempt history is destroyed and
    velocity work (constitution VII) loses its only input.
    """

    await st.social_capture_record_outcome.fn(
        platform="instagram", content_id="c1",
        capture_kind="instagram_clip_stats", outcome="no_match")

    assert "INSERT INTO capture_outcomes" in capture["sql"]
    assert "ON CONFLICT" not in capture["sql"], "capture outcomes are append-only"
