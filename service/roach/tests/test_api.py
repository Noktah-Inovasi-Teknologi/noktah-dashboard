"""Contract tests for the roach internal HTTP API (contracts/roach-api.md)."""
import os

os.environ.setdefault("ROACH_API_KEY", "test-key")
os.environ.setdefault("OPENROUTER_API_KEY", "test-openrouter-key")

import pytest
from fastapi.testclient import TestClient

import api
import collect

client = TestClient(api.app)
HEADERS = {"X-API-KEY": "test-key"}


def test_health_requires_no_auth():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_list_requires_api_key():
    resp = client.post("/list", json={"profile_url": "https://www.tiktok.com/@x", "platform": "tiktok"})
    assert resp.status_code == 401


def test_list_rejects_wrong_api_key():
    resp = client.post(
        "/list",
        json={"profile_url": "https://www.tiktok.com/@x", "platform": "tiktok"},
        headers={"X-API-KEY": "wrong"},
    )
    assert resp.status_code == 401


def test_list_success_shape_and_no_owner_only_analytics(monkeypatch):
    def fake_list_profile(profile_url, platform, max_items=30):
        return (
            {"handle": "x", "platform": "tiktok", "public_metadata": {}},
            [{
                "content_id": "123", "content_type": "video", "is_video": True,
                "source_url": "https://tiktok.com/@x/video/123", "published_at": "2026-07-10T04:12:00Z",
                "caption": "hi", "hashtags": ["x"],
                "public_counts": {"likes": 5, "comments": 1, "views": 10},
            }],
        )

    monkeypatch.setattr(collect, "list_profile", fake_list_profile)
    resp = client.post("/list", json={"profile_url": "https://www.tiktok.com/@x", "platform": "tiktok"}, headers=HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["profile"]["handle"] == "x"
    item = body["items"][0]
    counts = item["public_counts"]
    # FR-003: owner-only analytics (reach/impressions/saves) must never appear
    assert "reach" not in counts and "impressions" not in counts and "saves" not in counts
    assert set(counts) <= {"likes", "comments", "views"}


def test_list_empty_items_for_zero_content_profile(monkeypatch):
    monkeypatch.setattr(collect, "list_profile", lambda u, p, m=30: ({"handle": "x", "platform": "tiktok"}, []))
    resp = client.post("/list", json={"profile_url": "https://www.tiktok.com/@x", "platform": "tiktok"}, headers=HEADERS)
    assert resp.status_code == 200
    assert resp.json()["items"] == []


def test_list_profile_not_found(monkeypatch):
    def raise_not_found(u, p, m=30):
        raise collect.ProfileNotFoundError("private account")

    monkeypatch.setattr(collect, "list_profile", raise_not_found)
    resp = client.post("/list", json={"profile_url": "https://www.instagram.com/x/", "platform": "instagram"}, headers=HEADERS)
    assert resp.status_code == 404
    assert resp.json()["code"] == "profile_not_found"


def test_list_rate_limited(monkeypatch):
    def raise_rate_limited(u, p, m=30):
        raise collect.RateLimitedError("challenge", code="challenge")

    monkeypatch.setattr(collect, "list_profile", raise_rate_limited)
    resp = client.post("/list", json={"profile_url": "https://www.instagram.com/x/", "platform": "instagram"}, headers=HEADERS)
    assert resp.status_code == 429
    assert resp.json()["code"] == "challenge"


def test_download_success_shape(monkeypatch):
    monkeypatch.setattr(collect, "download_item", lambda **kw: (["/data/123.mp4"], "video"))
    resp = client.post(
        "/download",
        json={"content_id": "123", "source_url": "https://x", "is_video": True, "content_type": "video"},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["local_paths"] == ["/data/123.mp4"]
    assert body["content_type"] == "video"


def test_download_content_gone(monkeypatch):
    def raise_gone(**kw):
        raise collect.ContentGoneError("story expired")

    monkeypatch.setattr(collect, "download_item", raise_gone)
    resp = client.post(
        "/download",
        json={"content_id": "123", "source_url": "https://x", "is_video": False, "content_type": "story"},
        headers=HEADERS,
    )
    assert resp.status_code == 404
    assert resp.json()["code"] == "content_gone"


def test_analyze_success_shape(monkeypatch):
    import analyze as analyze_mod

    monkeypatch.setattr(
        analyze_mod, "analyze_item",
        lambda paths, ct: {"subtitle": "hi", "flow": "1. hook", "summary": "a video", "status": "success", "error": None},
    )
    resp = client.post(
        "/analyze",
        json={"content_id": "123", "local_paths": ["/data/123.mp4"], "content_type": "video"},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["analysis"]["status"] == "success"


def test_analyze_image_leaves_subtitle_empty(monkeypatch):
    import analyze as analyze_mod

    monkeypatch.setattr(
        analyze_mod, "analyze_item",
        lambda paths, ct: {"subtitle": "", "flow": "1. shows a chart", "summary": "a chart", "status": "success", "error": None},
    )
    resp = client.post(
        "/analyze",
        json={"content_id": "456", "local_paths": ["/data/456.jpg"], "content_type": "image"},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["analysis"]["subtitle"] == ""
