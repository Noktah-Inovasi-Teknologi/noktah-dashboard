"""Tests for collect.py, notably handle resolution (FR-009 Drive folder naming).

Regression test: yt-dlp's `uploader_id` for TikTok returns the internal secUid,
not the readable @handle. The profile URL itself is the reliable source.
"""
import os

os.environ.setdefault("ROACH_API_KEY", "test-key")

import collect


def test_handle_from_url_tiktok():
    assert collect._handle_from_url("https://www.tiktok.com/@rsmatadryap") == "rsmatadryap"


def test_handle_from_url_instagram():
    assert collect._handle_from_url("https://www.instagram.com/klinikutama.sumenep/") == "klinikutama.sumenep"


def test_handle_from_url_no_path_returns_none():
    assert collect._handle_from_url("https://www.tiktok.com/") is None


def test_derive_content_type_single_video_is_video():
    # A Reel/TikTok downloads to one .mp4 — must classify as "video" even when
    # the listing could only guess "image" or "carousel".
    assert collect._derive_content_type(["/data/1_1.mp4"], "carousel") == "video"


def test_derive_content_type_single_image_is_image():
    assert collect._derive_content_type(["/data/1_1.jpg"], "carousel") == "image"


def test_derive_content_type_multi_file_is_carousel():
    assert collect._derive_content_type(["/data/1_1.jpg", "/data/1_2.jpg"], "image") == "carousel"


def test_derive_content_type_story_preserved():
    assert collect._derive_content_type(["/data/1_1.mp4"], "story") == "story"


def test_derive_content_type_ignores_audio_track():
    # TikTok photo posts ship a soundtrack .mp3 next to the images; it must not
    # inflate a single-image post to "carousel" nor count as media.
    assert collect._derive_content_type(["/data/1_0.mp3", "/data/1_1.jpg"], "image") == "image"
    assert collect._derive_content_type(
        ["/data/1_0.mp3", "/data/1_1.jpg", "/data/1_2.jpg"], "image"
    ) == "carousel"


def test_tiktok_counts_maps_stats():
    meta = {"stats": {"diggCount": "12", "commentCount": 3, "playCount": 400, "shareCount": 1}}
    assert collect._tiktok_counts(meta) == {"likes": 12, "comments": 3, "views": 400}


def test_tiktok_counts_missing_stats():
    assert collect._tiktok_counts({}) == {"likes": None, "comments": None, "views": None}


def test_tiktok_photo_overrides_mislabeled_ytdlp_video(monkeypatch):
    """yt-dlp lists TikTok photo posts as /video/ entries (is_video=True, which
    would fail at download). gallery-dl's imagePost identification must replace
    that entry, not lose the dedup race to it."""

    class FakeYDL:
        def __init__(self, opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download=False):
            return {
                "entries": [
                    {"id": "111", "url": "https://www.tiktok.com/@acct/video/111", "timestamp": 1700000000},
                    # actually a photo post, mislabeled by yt-dlp as a video
                    {"id": "222", "url": "https://www.tiktok.com/@acct/video/222", "timestamp": 1700000100},
                ],
            }

    photo_msgs = [
        [2, {"id": "222", "imagePost": {"images": [{}, {}, {}]}, "createTime": 1700000100,
             "desc": "a photo post", "author": {"uniqueId": "acct"},
             "stats": {"diggCount": 5, "commentCount": 1, "playCount": 100}}],
    ]

    monkeypatch.setattr(collect.yt_dlp, "YoutubeDL", FakeYDL)
    monkeypatch.setattr(collect, "_gallery_dl_json", lambda url, platform, n, fp=None: photo_msgs if "/posts" in url else [])
    monkeypatch.setattr(collect.time, "sleep", lambda s: None)

    _, items = collect._list_tiktok("https://www.tiktok.com/@acct", 30)
    by_id = {i["content_id"]: i for i in items}
    assert len(items) == 2
    assert by_id["111"]["content_type"] == "video" and by_id["111"]["is_video"] is True
    assert by_id["222"]["content_type"] == "carousel"
    assert by_id["222"]["is_video"] is False
    assert "/photo/222" in by_id["222"]["source_url"]


def test_derive_content_type_empty_falls_back():
    assert collect._derive_content_type([], "image") == "image"


def test_list_profile_prefers_url_handle_over_secuid(monkeypatch):
    """yt-dlp's uploader_id (an internal secUid) must not override the URL-derived handle."""

    class FakeYDL:
        def __init__(self, opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download=False):
            return {
                "uploader_id": "MS4wLjABAAAAbSCgATHrl-SzAZ8n9B1Up_QTp2tEdIY1xm4T3CGUrbpF-Y51s2woFGy2z1gLaawf",
                "entries": [],
            }

    monkeypatch.setattr(collect.yt_dlp, "YoutubeDL", FakeYDL)
    monkeypatch.setattr(
        collect.subprocess, "run",
        lambda *a, **kw: type("P", (), {"returncode": 1, "stdout": "", "stderr": "no gallery-dl extractor"})(),
    )
    # Skip the real inter-pass pacing sleeps in tests
    monkeypatch.setattr(collect.time, "sleep", lambda s: None)

    profile_meta, items = collect.list_profile("https://www.tiktok.com/@rsmatadryap", "tiktok")
    assert profile_meta["handle"] == "rsmatadryap"


# --- Anti-detection: fingerprint rotation, proxy plumbing, cookie pool, 403 escalation ---


def test_session_fingerprint_stable_per_seed_and_varies_across_seeds(monkeypatch):
    monkeypatch.setattr(collect, "IMPERSONATE_POOL", ["chrome", "safari", "edge-99", "firefox"])
    # Same seed -> identical choice on every call (consistent within a profile).
    assert collect._session_fingerprint("acct")["target"] == collect._session_fingerprint("acct")["target"]
    # Across many seeds we should see more than one distinct target (rotation).
    targets = {collect._session_fingerprint(f"acct{i}")["target"] for i in range(20)}
    assert len(targets) > 1


def test_session_fingerprint_browser_strips_version_suffix(monkeypatch):
    monkeypatch.setattr(collect, "IMPERSONATE_POOL", ["edge-99"])
    fp = collect._session_fingerprint("x")
    assert fp["target"] == "edge-99"
    assert fp["browser"] == "edge"  # gallery-dl browser= takes a bare name


def test_yt_dlp_opts_applies_impersonate_and_proxy(monkeypatch):
    monkeypatch.setattr(collect, "_impersonate_target", lambda target=None: f"IMP:{target}")
    monkeypatch.setattr(collect, "PROXY_URL", "http://phone:8888")
    monkeypatch.setattr(collect, "INSTAGRAM_PROXY_URL", "")
    monkeypatch.setattr(collect, "TIKTOK_PROXY_URL", "")
    opts = collect._yt_dlp_opts("tiktok", {"target": "safari"})
    assert opts["impersonate"] == "IMP:safari"
    assert opts["proxy"] == "http://phone:8888"


def test_gallery_dl_cmd_threads_proxy_browser_and_language(monkeypatch):
    monkeypatch.setattr(collect, "PROXY_URL", "http://phone:8888")
    monkeypatch.setattr(collect, "INSTAGRAM_PROXY_URL", "")
    monkeypatch.setattr(collect, "TIKTOK_PROXY_URL", "")
    monkeypatch.setattr(collect, "_cookies_file", lambda platform, seed=None: None)
    fp = {"browser": "safari", "accept_language": "en-US,en;q=0.9", "_seed": "acct"}
    cmd = collect._gallery_dl_cmd("https://www.tiktok.com/@acct/posts", "tiktok", ["-j"], fp)
    assert "--proxy" in cmd and "http://phone:8888" in cmd
    assert "browser=safari" in cmd
    assert any("Accept-Language=en-US" in c for c in cmd)


def test_no_proxy_by_default(monkeypatch):
    monkeypatch.setattr(collect, "PROXY_URL", "")
    monkeypatch.setattr(collect, "INSTAGRAM_PROXY_URL", "")
    monkeypatch.setattr(collect, "TIKTOK_PROXY_URL", "")
    assert collect._proxy_for("tiktok") == ""
    monkeypatch.setattr(collect, "_impersonate_target", lambda target=None: None)
    assert "proxy" not in collect._yt_dlp_opts("tiktok", {"target": "chrome"})


def test_cookies_pool_selection_deterministic(monkeypatch, tmp_path):
    pool_dir = tmp_path / "cookies.d" / "tiktok"
    pool_dir.mkdir(parents=True)
    for name in ("a.txt", "b.txt", "c.txt"):
        (pool_dir / name).write_text("# netscape\n")
    monkeypatch.setattr(collect, "COOKIES_POOL_DIR", tmp_path / "cookies.d")
    first = collect._cookies_file("tiktok", "acct")
    assert first is not None and first.parent == pool_dir
    assert collect._cookies_file("tiktok", "acct") == first  # stable per seed


def test_gallery_dl_json_escalates_on_403_threshold(monkeypatch):
    monkeypatch.setattr(collect, "GALLERY_DL_403_THRESHOLD", 3)
    monkeypatch.setattr(collect, "_cookies_file", lambda platform, seed=None: None)

    class P:
        returncode = 0
        stdout = "[]"
        stderr = "403 Forbidden\n403 Forbidden\n403 Forbidden"

    monkeypatch.setattr(collect.subprocess, "run", lambda *a, **kw: P())
    import pytest

    with pytest.raises(collect.RateLimitedError) as exc:
        collect._gallery_dl_json("https://www.tiktok.com/@acct/posts", "tiktok", 30, {"_seed": "acct"})
    assert exc.value.code == "challenge"


def test_gallery_dl_json_tolerates_few_403s(monkeypatch):
    monkeypatch.setattr(collect, "GALLERY_DL_403_THRESHOLD", 3)
    monkeypatch.setattr(collect, "_cookies_file", lambda platform, seed=None: None)

    class P:
        returncode = 0
        stdout = "[]"
        stderr = "403 Forbidden"  # one 403, below threshold

    monkeypatch.setattr(collect.subprocess, "run", lambda *a, **kw: P())
    assert collect._gallery_dl_json("https://www.tiktok.com/@acct/posts", "tiktok", 30, {"_seed": "acct"}) == []
