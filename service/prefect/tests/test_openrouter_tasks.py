"""
Tests for the reusable OpenRouter task helpers (tasks/openrouter_tasks.py):
JSON extraction (fenced / bare / embedded), strict schema builders, and payload
assembly (provider routing + response_format). No network.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from tasks import openrouter_tasks as ot


def test_extract_json_plain_object():
    assert ot._extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_strips_code_fences():
    assert ot._extract_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_extract_json_finds_embedded_array():
    assert ot._extract_json('here you go: [1, 2, 3] done') == [1, 2, 3]


def test_extract_json_raises_when_absent():
    with pytest.raises(Exception):
        ot._extract_json("no json here")


def test_object_schema_shape():
    schema = ot.object_schema("thing", ["a", "b"])
    js = schema["json_schema"]
    assert js["strict"] is True
    assert js["schema"]["required"] == ["a", "b"]
    assert js["schema"]["additionalProperties"] is False


def test_array_schema_wraps_items():
    schema = ot.array_schema("content_ideas", ["topik"])
    props = schema["json_schema"]["schema"]["properties"]
    assert "items" in props and props["items"]["type"] == "array"
    assert props["items"]["items"]["required"] == ["topik"]


def test_build_payload_includes_provider_and_response_format():
    rf = ot.object_schema("x", ["k"])
    payload = ot._build_payload("sys", "usr", "some/model", 1000, rf, 0.7)
    assert payload["model"] == "some/model"
    assert payload["temperature"] == 0.7
    assert payload["reasoning"] == {"enabled": False}
    assert payload["response_format"] == rf
    # system + user messages present, in order.
    assert [m["role"] for m in payload["messages"]] == ["system", "user"]
    if ot.PROVIDER_ORDER:
        assert payload["provider"]["order"] == ot.PROVIDER_ORDER


def test_build_payload_requests_usage_accounting():
    """Without opting in, the response carries no resolved `cost`."""
    payload = ot._build_payload(None, "usr", "some/model", 1000, None, 0.7)
    assert payload["usage"] == {"include": True}


class _CapturingLogger:
    def __init__(self):
        self.lines = []

    def info(self, message):
        self.lines.append(message)

    warning = info


def test_log_usage_records_tokens_cost_call_site_and_client():
    log = _CapturingLogger()
    body = {
        "model": "xiaomi/mimo-v2.5",
        "usage": {"prompt_tokens": 3100, "completion_tokens": 780, "total_tokens": 3880, "cost": 0.0042},
    }
    ot._log_usage(body, "songbird.generate[Post]", "requested/model", "Klinik Mata Sampang", log)
    line = log.lines[0]
    assert "prompt_tokens=3100" in line
    assert "completion_tokens=780" in line
    assert "call_site=songbird.generate[Post]" in line
    assert "client=Klinik Mata Sampang" in line
    assert "cost_usd=0.0042" in line
    # The model that actually served the call, not the one requested.
    assert "model=xiaomi/mimo-v2.5" in line


def test_log_usage_is_best_effort():
    """Accounting must never break a generation run."""
    log = _CapturingLogger()
    ot._log_usage({}, "x", "m", None, log)          # no usage object at all
    ot._log_usage({"usage": {}}, "x", "m", None, log)
    assert log.lines == []
    ot._log_usage({"usage": {"prompt_tokens": 1}}, "x", "m", None, object())  # logger without .info
    # No exception escaped.


# ── model rotation (config/ai/models.yaml, case `generation`) ──────────────────

def _fake_complete(fail_on):
    seen = []

    async def complete(api_key, system, user, model, *args):
        seen.append(model)
        if model in fail_on:
            raise RuntimeError(f"OpenRouter {model} HTTP 502")
        return {"items": []}
    return complete, seen


def test_generation_rotates_after_repeated_failures_and_a_named_model_never_does(monkeypatch):
    import asyncio

    from tasks import model_rotation

    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    rot = model_rotation.Rotation("generation", ["first", "second"], rotate_after=2)
    monkeypatch.setattr(model_rotation, "_rotations", {"generation": rot})
    complete, seen = _fake_complete({"first", "named"})
    monkeypatch.setattr(ot, "_complete", complete)

    async def run(model=None):
        try:
            return await ot.openrouter_chat.fn(user="x", model=model)
        except RuntimeError:
            return None

    for _ in range(2):
        asyncio.run(run())
    assert asyncio.run(run()) == {"items": []}
    assert seen == ["first", "first", "second"]
    asyncio.run(run("named"))
    assert rot.state() == {"accepted": ["first", "second"], "current": "second", "consecutive_failures": 0}


def test_the_generation_list_has_models():
    from tasks import model_rotation

    assert model_rotation.load()["generation"].models
