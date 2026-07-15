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
