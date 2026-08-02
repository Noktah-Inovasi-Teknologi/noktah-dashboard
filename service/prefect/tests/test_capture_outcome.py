"""
Constraint tests for `capture_outcomes` (feature 005-signal-field-coverage).

This table exists to make absence legible: an empty engagement value must be
resolvable to exactly one cause. Every rule below is asserted against a real
PostgreSQL database rather than at the application layer, because the defect
this feature fixes is precisely a rule that lived only in code and was
therefore silently unenforced.

Requires a real disposable PostgreSQL database (see tests/conftest.py). Skipped
automatically when SPINE_TEST_DATABASE_URL is unreachable.
"""
import asyncpg
import pytest

pytestmark = pytest.mark.schema


async def _insert_outcome(conn, **kw):
    params = {
        "platform": "instagram",
        "content_id": "ABC123",
        "capture_kind": "instagram_clip_stats",
        "outcome": "success",
        "reason": None,
        **kw,
    }
    return await conn.fetchrow(
        """
        INSERT INTO capture_outcomes (platform, content_id, capture_kind, outcome, reason)
        VALUES ($1, $2, $3, $4, $5) RETURNING id, observed_at
        """,
        params["platform"], params["content_id"], params["capture_kind"],
        params["outcome"], params["reason"],
    )


# --------------------------------------------------------------------------
# T010 / FR-002a-equivalent: closed vocabularies enforced at the DB boundary
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unknown_outcome_rejected(spine_db):
    with pytest.raises(asyncpg.CheckViolationError):
        await _insert_outcome(spine_db, outcome="probably_fine")


@pytest.mark.asyncio
async def test_unknown_capture_kind_rejected(spine_db):
    with pytest.raises(asyncpg.CheckViolationError):
        await _insert_outcome(spine_db, capture_kind="instagram_vibes")


@pytest.mark.asyncio
async def test_all_four_outcomes_accepted(spine_db):
    """The vocabulary is closed, but it must actually admit its own members."""
    for outcome, reason in [
        ("success", None),
        ("no_match", None),
        ("not_attempted", None),
        ("failed", "timeout"),
    ]:
        row = await _insert_outcome(spine_db, outcome=outcome, reason=reason, content_id=f"c-{outcome}")
        assert row["id"] is not None


# --------------------------------------------------------------------------
# T010: an outcome is recordable without a harvested_signals row
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_outcome_recordable_before_any_signal_row_exists(spine_db):
    """
    The keying decision (data-model.md section 3): capture_outcomes is keyed by
    (platform, content_id), NOT harvested_signals.id, because a capture can fail
    BEFORE a signal row exists. Keying to the signal row would make exactly those
    failures unrecordable — silently dropping the failures Constitution V says
    must never be dropped.
    """
    assert await spine_db.fetchval("SELECT count(*) FROM harvested_signals") == 0

    row = await _insert_outcome(
        spine_db, content_id="never-delivered", outcome="failed", reason="blocked",
    )
    assert row["id"] is not None


# --------------------------------------------------------------------------
# T014 / FR-003b, FR-004
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_match_and_failed_are_stored_distinctly(spine_db):
    """
    FR-003b. `no_match` means the pass ran fine and returned nothing for THIS
    item — the live case where a carousel is absent from the Reels-keyed clips
    response. `failed` means the pass itself broke. Collapsing them would leave
    the corpus exactly as ambiguous as before this feature, with more tables.
    """
    await _insert_outcome(spine_db, content_id="carousel-1", outcome="no_match")
    await _insert_outcome(spine_db, content_id="reel-1", outcome="failed", reason="timeout")

    rows = {
        r["content_id"]: r["outcome"]
        for r in await spine_db.fetch("SELECT content_id, outcome FROM capture_outcomes")
    }
    assert rows["carousel-1"] == "no_match"
    assert rows["reel-1"] == "failed"


@pytest.mark.asyncio
async def test_failed_without_reason_rejected(spine_db):
    """
    FR-004: a failed capture MUST carry a classified reason. "Unknown failure"
    is a classification decision for a human reading a traceback, not something
    to COALESCE into existence.
    """
    with pytest.raises(asyncpg.CheckViolationError):
        await _insert_outcome(spine_db, outcome="failed", reason=None)


@pytest.mark.asyncio
async def test_failed_with_unclassified_reason_rejected(spine_db):
    with pytest.raises(asyncpg.CheckViolationError):
        await _insert_outcome(spine_db, outcome="failed", reason="it broke somehow")


@pytest.mark.asyncio
async def test_non_failed_outcome_may_omit_reason(spine_db):
    """The reason constraint must bind only `failed` — not every row."""
    row = await _insert_outcome(spine_db, outcome="no_match", reason=None)
    assert row["id"] is not None


# --------------------------------------------------------------------------
# T065 / FR-003a: append-only
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_repeated_attempts_append_rather_than_update(spine_db):
    """
    FR-003a. Two attempts for the same (platform, content_id, capture_kind) must
    produce TWO rows, not one updated row — Constitution VII forbids updating an
    observation in place, and velocity work later depends on the history.

    This is the regression test for the specific mistake of "tidying" this table
    with a unique index plus ON CONFLICT DO UPDATE: that would pass every other
    test in this file while destroying the attempt history.
    """
    first = await _insert_outcome(spine_db, content_id="reel-9", outcome="failed", reason="timeout")
    second = await _insert_outcome(spine_db, content_id="reel-9", outcome="success")

    rows = await spine_db.fetch(
        """
        SELECT id, outcome, observed_at FROM capture_outcomes
        WHERE platform = 'instagram' AND content_id = 'reel-9'
          AND capture_kind = 'instagram_clip_stats'
        ORDER BY id
        """
    )
    assert len(rows) == 2, "a re-attempt must append, never overwrite the earlier attempt"
    assert [r["outcome"] for r in rows] == ["failed", "success"]
    assert first["id"] != second["id"]


@pytest.mark.asyncio
async def test_no_unique_constraint_blocks_reattempts(spine_db):
    """
    Guards the same invariant from the schema side: if someone adds a unique
    index on (platform, content_id, capture_kind) the append-only contract is
    broken, and the failure should name that cause rather than surfacing as a
    confusing UniqueViolationError somewhere in the harvest engine.
    """
    indexes = await spine_db.fetch(
        "SELECT indexdef FROM pg_indexes WHERE tablename = 'capture_outcomes'"
    )
    uniques = [i["indexdef"] for i in indexes if "UNIQUE" in i["indexdef"].upper()]
    assert all("capture_kind" not in u for u in uniques), (
        "a unique index covering capture_kind would prevent re-attempts from appending"
    )


# --------------------------------------------------------------------------
# Account-level captures (FR-018 support)
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_account_level_outcome_links_to_account(spine_db):
    """
    A follower-capture miss is recorded here with account_id set, which is what
    makes FR-018 queryable — "attempted and missed" vs "never attempted" — rather
    than trapped in a single run's summary text.
    """
    account_id = await spine_db.fetchval(
        "INSERT INTO accounts (platform) VALUES ('instagram') RETURNING id"
    )
    await spine_db.execute(
        """
        INSERT INTO capture_outcomes (platform, content_id, capture_kind, outcome, reason, account_id)
        VALUES ('instagram', 'acct:lasikasyik', 'instagram_profile_info', 'failed', 'blocked', $1)
        """,
        account_id,
    )
    row = await spine_db.fetchrow(
        "SELECT account_id, outcome, reason FROM capture_outcomes WHERE capture_kind = 'instagram_profile_info'"
    )
    assert row["account_id"] == account_id
    assert (row["outcome"], row["reason"]) == ("failed", "blocked")


@pytest.mark.asyncio
async def test_unknown_platform_rejected(spine_db):
    with pytest.raises(asyncpg.CheckViolationError):
        await _insert_outcome(spine_db, platform="threads")
