"""The OpenRouter client's 429 handling, against a fake transport (no network)."""
import json

import httpx
import pytest

from app.ai.openrouter import AiFailure, chat_json

THROTTLED = {"error": {"code": 429, "message": "Provider returned error", "metadata": {
    "provider_name": "DeepInfra", "limit_source": "upstream_provider_shared_pool"}}}
ANSWER = {"model": "xiaomi/mimo-v2.5", "provider": "DeepInfra",
          "choices": [{"finish_reason": "stop", "message": {"content": json.dumps({"ok": True})}}],
          "usage": {"prompt_tokens": 10, "completion_tokens": 5, "cost": 0.0001}}


def _transport(responses):
    seen = []

    def handler(request):
        seen.append(request)
        status, body = responses[min(len(seen), len(responses)) - 1]
        return httpx.Response(status, json=body)

    return httpx.MockTransport(handler), seen


async def _call(transport, backoff):
    return await chat_json(api_key="k", model="m", messages=[{"role": "user", "content": "x"}],
                           schema={"type": "object"}, validate=lambda v: None, call_site="test",
                           transport=transport, rate_limit_backoff=backoff)


async def test_a_429_is_retried_as_often_as_the_backoff_allows():
    transport, seen = _transport([(429, THROTTLED), (429, THROTTLED), (200, ANSWER)])
    result = await _call(transport, (0, 0))
    assert result.value == {"ok": True} and len(seen) == 3


async def test_a_429_that_outlasts_the_retries_names_who_throttled():
    transport, seen = _transport([(429, THROTTLED)])
    with pytest.raises(AiFailure) as e:
        await _call(transport, (0,))
    assert len(seen) == 2
    # provider_error: the vocabulary intakes.failure_reason allows; the detail says why.
    assert e.value.reason == "provider_error"
    assert e.value.detail == "HTTP 429 from DeepInfra (upstream_provider_shared_pool)"
