"""The AI cap is enforced before a call, and the 80% alert fires once a month (G-6)."""
from datetime import datetime, timezone

import pytest

from app.ai import budget
from app.errors import AiCapReached
from app.settings import get_settings


def test_over_cap_boundary():
    assert not budget.over_cap(4.99, 5.0)
    assert budget.over_cap(5.0, 5.0)


def test_month_key():
    assert budget.month_key(datetime(2026, 10, 1, tzinfo=timezone.utc)) == "2026-10"


@pytest.mark.schema
async def test_cap_blocks_and_alert_sends_once(hub_db, monkeypatch):
    monkeypatch.setenv("HUB_AI_MONTHLY_CAP_USD", "0.01")
    get_settings.cache_clear()
    sent = []

    async def fake_slack(url, text):
        sent.append(text)
        return True

    monkeypatch.setattr(budget, "post_slack", fake_slack)
    async with hub_db.acquire() as conn:
        await budget.check_budget(conn)  # nothing spent yet
        for _ in range(3):
            await budget.record(conn, call_site="hub.intake", client_id=None, intake_id=None, model="m",
                                provider="p", prompt_tokens=10, completion_tokens=5, cost_usd=0.004)
        assert len(sent) == 1, "the 80% alert must fire exactly once per month"
        with pytest.raises(AiCapReached):
            await budget.check_budget(conn)
    get_settings.cache_clear()
