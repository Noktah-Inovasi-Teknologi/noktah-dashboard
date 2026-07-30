"""Tests for analyze.py: OpenRouter payload shape (provider routing, reasoning
off, structured output), robust JSON parsing, and ffmpeg-compression fallback.

The OpenRouter HTTP call (`requests.post`) and ffmpeg are mocked; no network or
media processing happens here.
"""
import json
import os

os.environ.setdefault("ROACH_API_KEY", "test-key")
os.environ.setdefault("OPENROUTER_API_KEY", "test-or-key")

import pytest

import analyze


class FakeResp:
    def __init__(self, content, status_code=200, headers=None):
        self._content = content
        self.status_code = status_code
        self.headers = headers or {}
        self.text = content if isinstance(content, str) else ""

    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}


def test_build_payload_has_provider_reasoning_and_schema(monkeypatch):
    monkeypatch.setattr(analyze, "PROVIDER_ORDER", ["xiaomi", "digitalocean"])
    monkeypatch.setattr(analyze, "ALLOW_FALLBACKS", True)
    payload = analyze._build_payload("prompt", [], "xiaomi/mimo-v2.5", ["flow", "summary"], 3000)
    assert payload["provider"] == {"order": ["xiaomi", "digitalocean"], "allow_fallbacks": True}
    assert payload["reasoning"] == {"enabled": False}
    assert payload["max_tokens"] == 3000
    schema = payload["response_format"]["json_schema"]["schema"]
    assert set(schema["required"]) == {"flow", "summary"}
    # Usage accounting must be requested, or the response carries no `cost`.
    assert payload["usage"] == {"include": True}


def test_call_model_sends_attribution_headers_and_logs_usage(monkeypatch, capsys):
    """OpenRouter returns token usage on every call; it must be written down.

    Also asserts the X-Title/HTTP-Referer attribution headers, without which
    OpenRouter's dashboard cannot split roach's spend from songbird's.
    """
    captured = {}

    class UsageResp(FakeResp):
        def json(self):
            return {
                "model": "xiaomi/mimo-v2.5",
                "choices": [{"message": {"content": '{"flow": "f", "summary": "s"}'}}],
                "usage": {"prompt_tokens": 1200, "completion_tokens": 340, "total_tokens": 1540, "cost": 0.0021},
            }

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["headers"] = headers
        return UsageResp("")

    monkeypatch.setattr(analyze.requests, "post", fake_post)
    result = analyze._call_model(
        "prompt", [], "xiaomi/mimo-v2.5", ["flow", "summary"], 3000,
        call_site="analyze.images", client="Ecky Dental Center",
    )
    assert result == {"flow": "f", "summary": "s"}
    assert captured["headers"]["X-Title"] == analyze.APP_TITLE
    assert captured["headers"]["HTTP-Referer"] == analyze.APP_REFERER

    logged = capsys.readouterr().out
    assert "prompt_tokens=1200" in logged
    assert "completion_tokens=340" in logged
    assert "call_site=analyze.images" in logged
    assert "client=Ecky Dental Center" in logged
    assert "cost_usd=0.0021" in logged


def test_provider_order_defaults_to_xiaomi_first():
    # Module default parses "Xiaomi,DigitalOcean,NovitaAI,Parasail" -> slugs.
    assert analyze.PROVIDER_ORDER[0] == "xiaomi"
    assert "novita" in analyze.PROVIDER_ORDER


def test_extract_json_plain():
    assert analyze._extract_json('{"summary": "x", "flow": "y"}') == {"summary": "x", "flow": "y"}


def test_extract_json_strips_fences():
    assert analyze._extract_json('```json\n{"summary": "x"}\n```') == {"summary": "x"}


def test_extract_json_recovers_from_surrounding_prose():
    raw = 'Sure, here you go:\n{"summary": "x", "flow": "z"} \nHope that helps!'
    assert analyze._extract_json(raw) == {"summary": "x", "flow": "z"}


def test_extract_json_raises_when_absent():
    with pytest.raises(json.JSONDecodeError):
        analyze._extract_json("no json here")


def test_call_model_retries_on_empty_then_parses(monkeypatch):
    responses = [FakeResp(""), FakeResp('{"flow": "a", "summary": "b"}')]
    monkeypatch.setattr(analyze.requests, "post", lambda *a, **kw: responses.pop(0))
    out = analyze._call_model("p", [], "m", ["flow", "summary"], 1000)
    assert out == {"flow": "a", "summary": "b"}


def test_compress_video_falls_back_when_ffmpeg_missing(monkeypatch, tmp_path):
    src = tmp_path / "v.mp4"
    src.write_bytes(b"fake")
    monkeypatch.setattr(analyze.shutil, "which", lambda name: None)
    path, is_temp = analyze._compress_video(src)
    assert path == src and is_temp is False


def test_compress_video_falls_back_when_ffmpeg_errors(monkeypatch, tmp_path):
    src = tmp_path / "v.mp4"
    src.write_bytes(b"fake")
    monkeypatch.setattr(analyze.shutil, "which", lambda name: "/usr/bin/ffmpeg")

    def boom(*a, **kw):
        raise analyze.subprocess.SubprocessError("ffmpeg failed")

    monkeypatch.setattr(analyze.subprocess, "run", boom)
    path, is_temp = analyze._compress_video(src)
    assert path == src and is_temp is False


def test_analyze_images_caps_and_cleans_temps(monkeypatch, tmp_path):
    # More images than MAX_IMAGES should be truncated; temp files cleaned up.
    monkeypatch.setattr(analyze, "MAX_IMAGES", 2)
    imgs = []
    for i in range(5):
        p = tmp_path / f"{i}.jpg"
        p.write_bytes(b"img")
        imgs.append(p)
    # No real compression — pretend ffmpeg is absent so originals are used.
    monkeypatch.setattr(analyze.shutil, "which", lambda name: None)
    captured = {}

    def fake_call(prompt, parts, model, keys, max_tokens, call_site="analyze", client=None):
        captured["n_parts"] = len(parts)
        captured["call_site"] = call_site
        captured["client"] = client
        return {"flow": "f", "summary": "s"}

    monkeypatch.setattr(analyze, "_call_model", fake_call)
    result = analyze.analyze_images(imgs, client="Ecky Dental Center")
    assert captured["n_parts"] == 2  # capped
    assert result["subtitle"] == ""  # image path has no transcript
    # Attribution reaches the call so token usage can be logged per client/call site.
    assert captured["call_site"] == "analyze.images"
    assert captured["client"] == "Ecky Dental Center"


def test_analyze_item_never_raises_on_model_failure(monkeypatch, tmp_path):
    p = tmp_path / "1.jpg"
    p.write_bytes(b"img")

    def boom(*a, **kw):
        raise RuntimeError("provider down")

    monkeypatch.setattr(analyze, "analyze_images", boom)
    out = analyze.analyze_item([str(p)], "image")
    assert out["status"] == "failed" and out["error"]
