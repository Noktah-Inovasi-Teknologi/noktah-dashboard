"""
Spec 009 against a real disposable database (migration 015 applied by `spine_db`):
`harvest.attempt.record` writes a `harvest_attempts` row the table's constraints accept,
and songbird's exemplar query leaves out every post with an active Hub review mark.
"""
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from tasks import harvest_attempt_tasks as hat  # noqa: E402
from tasks import songbird_tasks as st  # noqa: E402
from tests.conftest import pool_from_connection  # noqa: E402

pytestmark = pytest.mark.schema


@pytest.mark.asyncio
async def test_attempt_row_is_written(spine_db, monkeypatch):
    account = await spine_db.fetchval("INSERT INTO accounts (platform, is_active) VALUES ('instagram', true) RETURNING id")
    monkeypatch.setattr(hat, "_db_pool", pool_from_connection(spine_db))
    started = datetime.now(timezone.utc) - timedelta(minutes=3)

    attempt_id = await hat.harvest_attempt_record.fn(
        account_id=str(account), trigger="monthly", window_days=90, started_at=started,
        ended_at=started + timedelta(minutes=2), outcome="collected", posts_collected=4, reason=None,
        flow_run_id="run-1")

    row = await spine_db.fetchrow("SELECT * FROM harvest_attempts WHERE id = $1", attempt_id)
    assert (row["account_id"], row["trigger"], row["window_days"], row["outcome"], row["posts_collected"],
            row["flow_run_id"]) == (account, "monthly", 90, "collected", 4, "run-1")


@pytest.mark.asyncio
async def test_unknown_outcome_is_refused_before_the_database():
    with pytest.raises(ValueError):
        await hat.harvest_attempt_record.fn(account_id="x", trigger="monthly", window_days=31,
                                            started_at=datetime.now(timezone.utc), ended_at=None, outcome="partial")


@pytest.mark.asyncio
async def test_songbird_skips_posts_with_an_active_mark(spine_db, monkeypatch):
    for cid, likes in (("plain", 50), ("tidak-relevan", 900), ("cleared", 70), ("iklan", 800)):
        await spine_db.execute(
            """INSERT INTO harvested_signals (platform, profile_key, content_id, content_type, published_at,
                                              likes, comments, views, advertisement, caption)
               VALUES ('instagram', 'acct', $1, 'image', now() - interval '3 days', $2, 1, NULL, $3, $1)""",
            cid, likes, cid == "iklan")
    await spine_db.execute(
        """INSERT INTO post_review_marks (platform, content_id, mark) VALUES
               ('instagram', 'tidak-relevan', 'tidak_relevan'), ('instagram', 'iklan', 'iklan')""")
    await spine_db.execute(
        """INSERT INTO post_review_marks (platform, content_id, mark, cleared_at)
           VALUES ('instagram', 'cleared', 'bukan_konten_akun_ini', now())""")
    monkeypatch.setattr(st, "_db_pool", pool_from_connection(spine_db))

    picked = await st.songbird_top_performers.fn(["acct"], limit=10)

    assert sorted(p["caption"] for p in picked) == ["cleared", "plain"]
