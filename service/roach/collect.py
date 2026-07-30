"""Collection primitives: video path (yt-dlp) and non-video path (gallery-dl).

Video path covers Reels/TikTok videos on both platforms. Non-video path covers
image posts, multi-item carousels, stories, and profile metadata via gallery-dl.
Instagram uses a burner-account cookie session (anonymous access only surfaces
the first few posts and no stories); TikTok is collected anonymously. See
specs/002-social-content-harvest/research.md R3/R4.
"""
import http.cookiejar
import os
import random
import re
import subprocess
import json
import time
from pathlib import Path
from urllib.parse import urlparse

import yt_dlp

DATA_DIR = Path(__file__).parent / "data" if not Path("/data").exists() else Path("/data")
SECRETS_DIR = Path(__file__).parent / "secrets"
COOKIES_FILE = SECRETS_DIR / "cookies.txt"                 # Instagram burner session
TIKTOK_COOKIES_FILE = SECRETS_DIR / "tiktok_cookies.txt"   # TikTok login session (photos/stories)
# Optional per-platform cookie pools: secrets/cookies.d/<platform>/*.txt. When a
# platform's directory holds multiple burner sessions, one is picked
# deterministically per profile so load spreads across accounts and a single
# stale/challenged cookie doesn't block every profile. Falls back to the single
# files above when the directory is absent or empty.
COOKIES_POOL_DIR = SECRETS_DIR / "cookies.d"

# yt-dlp TLS/HTTP impersonation (needs the curl_cffi backend, installed via the
# yt-dlp[curl-cffi] extra). TikTok/Cloudflare fingerprint the plain-Python HTTP
# stack and 403 it; impersonating a real Chrome handshake bypasses that class of
# block and hardens the (working) video path against throttling. Set
# YT_DLP_IMPERSONATE="" to disable, or to another target from
# `yt-dlp --list-impersonate-targets` (e.g. "safari").
IMPERSONATE_TARGET = os.environ.get("YT_DLP_IMPERSONATE", "chrome")

# Pool of impersonation targets to rotate across profiles/runs so every request
# doesn't carry an identical TLS fingerprint. One target is chosen per
# `list_profile`/`download_item` call (seeded on the profile handle) so a single
# profile's traffic looks like one consistent browser, while different profiles
# and runs vary. Empty string disables rotation and falls back to
# IMPERSONATE_TARGET. Comma-separated; entries must be valid
# `yt-dlp --list-impersonate-targets` names.
IMPERSONATE_POOL = [
    t.strip()
    for t in os.environ.get("YT_DLP_IMPERSONATE_POOL", "chrome,chrome-124,safari,edge-99").split(",")
    if t.strip()
]

# Optional proxy for all egress (yt-dlp + gallery-dl). Off by default: the
# service normally scrapes from the host's direct egress (e.g. a mobile
# hotspot). Set PROXY_URL to route everything through one proxy, or the
# per-platform vars to route only one platform. Empty = no proxy (unchanged).
PROXY_URL = os.environ.get("PROXY_URL", "").strip()
INSTAGRAM_PROXY_URL = os.environ.get("INSTAGRAM_PROXY_URL", "").strip()
TIKTOK_PROXY_URL = os.environ.get("TIKTOK_PROXY_URL", "").strip()

# Accept-Language header paired with the impersonated browser, for header
# realism on the gallery-dl path (yt-dlp's impersonation sets its own).
ACCEPT_LANGUAGE = os.environ.get("HARVEST_ACCEPT_LANGUAGE", "en-US,en;q=0.9")

# When a gallery-dl listing returns this many or more '403' responses, treat the
# profile as throttled/challenged (raise RateLimitedError) instead of silently
# returning partial results — so the flow's back-off/defer engages and the
# operator gets a clear signal to rotate the egress IP (e.g. the hotspot).
GALLERY_DL_403_THRESHOLD = int(os.environ.get("GALLERY_DL_403_THRESHOLD", "3"))

# --- TikTok request pacing (stay under the per-IP throttle) ---
# gallery-dl's TikTok extractor makes one HTTP request *per post*, so an
# unpaced listing is a burst of dozens of requests — exactly the pattern
# TikTok's throttle 403s. A randomized sleep between requests makes the
# traffic look like a human browsing. "min-max" seconds (gallery-dl syntax).
TIKTOK_SLEEP_REQUEST = os.environ.get("TIKTOK_SLEEP_REQUEST", "1.5-3.0")
# Seconds of randomized sleep between yt-dlp's pagination requests (TikTok).
TIKTOK_YTDLP_SLEEP = float(os.environ.get("TIKTOK_YTDLP_SLEEP", "1.5"))
# Jittered pause between the yt-dlp video pass and the gallery-dl photo/story
# passes, so the two tools' request bursts don't stack back-to-back. Env-tunable
# as "min-max" seconds; widen it to spread traffic further under scrutiny.
def _parse_range(value: str, default: tuple[float, float]) -> tuple[float, float]:
    try:
        lo, hi = (float(x) for x in value.split("-", 1))
        return (lo, hi) if lo <= hi else (hi, lo)
    except (ValueError, AttributeError):
        return default


TIKTOK_PASS_DELAY_RANGE = _parse_range(os.environ.get("TIKTOK_PASS_DELAY_RANGE", "5.0-10.0"), (5.0, 10.0))

# Seconds to pause between the (heavy) yt-dlp video burst and gallery-dl's
# per-post photo/story requests, so they don't hammer TikTok back-to-back and
# trip its per-IP throttle (which 403s gallery-dl). Photos/stories are
# best-effort, so this only paces the secondary passes.
TIKTOK_PASS_DELAY = float(os.environ.get("TIKTOK_PASS_DELAY", "5"))

# Which downloaded file extensions are video vs image. The downloaded files are
# the *authoritative* signal for content_type: Instagram's post-level listing
# metadata has no reliable video flag (video_url is only populated per-file), so
# a single Reel is indistinguishable from an image at listing time. After
# download we know for certain — a Reel yields one .mp4, a carousel yields N
# files — so `_derive_content_type` reclassifies from the files on disk.
VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv", ".m4v"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".gif"}
# TikTok photo posts ship a soundtrack file alongside the images; it is neither
# a carousel "slide" nor analyzable as an image, so it is excluded from the
# media count and from analysis (but still delivered to Drive).
AUDIO_EXTS = {".mp3", ".m4a", ".aac", ".wav", ".ogg", ".opus"}


def _derive_content_type(paths: list[str], fallback: str) -> str:
    """Authoritatively classify an item from the files actually downloaded.

    Counts only real media (images/videos), ignoring soundtrack files:
    - more than one media file  -> "carousel" (multi-media post)
    - single video file         -> "video"    (Reel / TikTok / single video post)
    - single image file         -> "image"
    Stories keep their listing type (they are foldered separately); anything
    unrecognized falls back to the listing's content_type.
    """
    if fallback == "story":
        return "story"
    media = [p for p in paths if Path(p).suffix.lower() in (VIDEO_EXTS | IMAGE_EXTS)]
    if not media:
        return fallback
    if len(media) > 1:
        return "carousel"
    return "video" if Path(media[0]).suffix.lower() in VIDEO_EXTS else "image"


class ProfileNotFoundError(Exception):
    """Profile is private, non-existent, or otherwise inaccessible."""


class ContentGoneError(Exception):
    """A previously-listed item is no longer available (e.g. an expired story)."""


class RateLimitedError(Exception):
    """Platform returned a rate-limit or verification-challenge response."""

    def __init__(self, message: str, code: str = "rate_limited"):
        super().__init__(message)
        self.code = code


_RATE_LIMIT_MARKERS = ("429", "rate limit", "rate-limit", "too many requests")
_CHALLENGE_MARKERS = ("challenge", "checkpoint", "verify your", "login_required", "please log in")
_NOT_FOUND_MARKERS = ("private", "does not exist", "not found", "unable to find", "404")


def _classify_error(exc: Exception) -> None:
    """Re-raise `exc` as one of our typed errors based on its message, or let it propagate."""
    text = str(exc).lower()
    if any(marker in text for marker in _CHALLENGE_MARKERS):
        raise RateLimitedError(str(exc), code="challenge") from exc
    if any(marker in text for marker in _RATE_LIMIT_MARKERS):
        raise RateLimitedError(str(exc), code="rate_limited") from exc
    if any(marker in text for marker in _NOT_FOUND_MARKERS):
        raise ProfileNotFoundError(str(exc)) from exc


def _cookies_file(platform: str, seed: str | None = None) -> Path | None:
    """The cookie file to use for gallery-dl on this platform, if present.

    Instagram: burner-account session (anonymous only surfaces a few posts).
    TikTok: login session — required for photo posts and Stories (anonymous is
    video-only via yt-dlp; gallery-dl's photo/story endpoints 403 without it).

    When a pool exists at secrets/cookies.d/<platform>/*.txt, one file is chosen
    deterministically from `seed` (the profile handle) so different profiles use
    different burner sessions. Falls back to the single legacy file.
    """
    pool_dir = COOKIES_POOL_DIR / platform
    if pool_dir.is_dir():
        pool = sorted(p for p in pool_dir.glob("*.txt") if p.stat().st_size > 0)
        if pool:
            idx = _seed_index(seed, len(pool))
            return pool[idx]
    if platform == "instagram" and COOKIES_FILE.exists():
        return COOKIES_FILE
    if platform == "tiktok" and TIKTOK_COOKIES_FILE.exists():
        return TIKTOK_COOKIES_FILE
    return None


def _seed_index(seed: str | None, n: int) -> int:
    """Stable, uniform index in [0, n) derived from `seed` (or random if None)."""
    if n <= 1:
        return 0
    if not seed:
        return random.randrange(n)
    import hashlib

    return int(hashlib.sha256(seed.encode()).hexdigest(), 16) % n


def _session_fingerprint(seed: str | None) -> dict:
    """Pick a browser identity for one call, stable across that call's passes.

    Returns {"target": <impersonate name>, "browser": <gallery-dl browser>,
    "accept_language": <str>}. Seeded on the profile handle so one profile's
    yt-dlp and gallery-dl passes present the same browser, while different
    profiles/runs rotate through IMPERSONATE_POOL.
    """
    pool = IMPERSONATE_POOL or ([IMPERSONATE_TARGET] if IMPERSONATE_TARGET else [])
    target = pool[_seed_index(seed, len(pool))] if pool else ""
    # gallery-dl's `browser` option takes a bare browser name (chrome/firefox/
    # safari); strip any version suffix from the impersonate target.
    browser = re.split(r"[-:]", target)[0] if target else "chrome"
    if browser not in {"chrome", "firefox", "safari", "edge"}:
        browser = "chrome"
    return {"target": target, "browser": browser, "accept_language": ACCEPT_LANGUAGE, "_seed": seed}


def _proxy_for(platform: str) -> str:
    """Resolve the proxy URL for a platform, or "" for direct egress (default)."""
    per_platform = INSTAGRAM_PROXY_URL if platform == "instagram" else TIKTOK_PROXY_URL
    return per_platform or PROXY_URL


def _impersonate_target(target: str | None = None):
    """Resolve an impersonate target name to an ImpersonateTarget, or None.

    `target` defaults to the module-level IMPERSONATE_TARGET; callers pass the
    per-call choice from `_session_fingerprint`.
    """
    name = IMPERSONATE_TARGET if target is None else target
    if not name:
        return None
    try:
        from yt_dlp.networking.impersonate import ImpersonateTarget

        return ImpersonateTarget.from_str(name)
    except Exception:
        # curl_cffi backend missing or bad target string — fall back to the
        # default HTTP stack rather than failing the whole call.
        return None


def _yt_dlp_opts(platform: str, fingerprint: dict | None = None) -> dict:
    opts: dict = {"quiet": True, "no_warnings": True, "noprogress": True}
    # yt-dlp uses cookies for Instagram only. TikTok login cookies break yt-dlp's
    # user extractor ("Unable to extract secondary user ID"), so its video path
    # stays cookie-less — TLS impersonation alone is enough for TikTok videos.
    if platform == "instagram" and COOKIES_FILE.exists():
        opts["cookiefile"] = str(COOKIES_FILE)
    if platform == "tiktok" and TIKTOK_YTDLP_SLEEP > 0:
        # Pace pagination requests so the profile listing isn't a burst.
        opts["sleep_interval_requests"] = TIKTOK_YTDLP_SLEEP
    target = _impersonate_target((fingerprint or {}).get("target"))
    if target is not None:
        opts["impersonate"] = target
    proxy = _proxy_for(platform)
    if proxy:
        opts["proxy"] = proxy
    return opts


def _gallery_dl_cmd(url: str, platform: str, extra: list[str], fingerprint: dict | None = None) -> list[str]:
    fp = fingerprint or _session_fingerprint(None)
    cmd = ["gallery-dl", "--quiet"]
    ck = _cookies_file(platform, fp.get("_seed"))
    if ck is not None:
        cmd += ["--cookies", str(ck)]
    proxy = _proxy_for(platform)
    if proxy:
        cmd += ["--proxy", proxy]
    if platform == "tiktok":
        # TikTok/Cloudflare 403s the plain-Python HTTP stack; gallery-dl's
        # `browser` option impersonates a real browser's TLS handshake, which
        # (with the login cookies) unlocks deep listing + photo posts + stories.
        # sleep-request paces the one-request-per-post extractor so the listing
        # doesn't look like a bot burst (see TIKTOK_SLEEP_REQUEST).
        cmd += ["-o", f"browser={fp.get('browser', 'chrome')}", "-o", f"sleep-request={TIKTOK_SLEEP_REQUEST}"]
    # Header realism: pair an Accept-Language with the impersonated browser.
    cmd += ["-o", f"extractor.{platform}.headers.Accept-Language={fp.get('accept_language', ACCEPT_LANGUAGE)}"]
    cmd += extra + [url]
    return cmd


def _gallery_dl_list_url(profile_url: str, platform: str) -> str:
    """URL to hand gallery-dl for *listing*.

    For Instagram, target the `/posts/` sub-URL so `gallery-dl -j` uses the
    posts extractor directly and emits per-post metadata, instead of the bare
    user URL which only yields a single queue node that `-j` does not recurse.
    """
    if platform == "instagram":
        base = profile_url if profile_url.endswith("/") else profile_url + "/"
        if not base.rstrip("/").endswith("posts"):
            base = base + "posts/"
        return base
    return profile_url


def _parse_gdl_date(value) -> str | None:
    """Parse gallery-dl's 'YYYY-MM-DD HH:MM:SS' string into ISO-8601 UTC."""
    if not value:
        return None
    if hasattr(value, "timestamp"):
        return _epoch_to_iso(value.timestamp())
    from datetime import datetime, timezone

    try:
        dt = datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None


def list_profile(profile_url: str, platform: str, max_items: int = 30, stories_only: bool = False) -> tuple[dict, list[dict]]:
    """List publicly visible items for a profile via the tool that fits the platform.

    Per-platform tooling:
      - TikTok    -> yt-dlp for videos (deep, fast, TLS-impersonated, cookie-less)
                     + gallery-dl for photo posts and Stories (needs login cookies
                     + browser impersonation; yt-dlp is video-only for TikTok)
      - Instagram -> gallery-dl (reliable with a cookie session; yt-dlp is
                     unreliable for IG even with cookies — research R4)

    `max_items` bounds how far back the gallery-dl listings page — increase it to
    reach older content for date-range backfills. Returns (profile_metadata,
    items); items may be empty. Raises ProfileNotFoundError / RateLimitedError on
    access problems.

    When `stories_only` is set, only the account's currently-active Stories are
    returned (a fast, single-endpoint call suited to frequent checks) — Instagram
    via the Stories extractor, TikTok via its Stories pass. Stories are ephemeral
    (~24h) and carry no public counts.

    Note: content_type here is *provisional*. Neither platform's listing
    metadata reliably distinguishes a single video from an image, so the
    authoritative content_type is re-derived from the downloaded files in
    `download_item` (`_derive_content_type`).
    """
    fingerprint = _session_fingerprint(_handle_from_url(profile_url))
    if platform == "instagram":
        if stories_only:
            handle = _handle_from_url(profile_url)
            profile_meta = {"handle": handle, "platform": "instagram", "public_metadata": {}}
            return profile_meta, _list_instagram_stories(profile_url, fingerprint)
        return _list_instagram(profile_url, max_items, fingerprint)
    profile_meta, items = _list_tiktok(profile_url, max_items, fingerprint)
    if stories_only:
        items = [i for i in items if i.get("content_type") == "story"]
    return profile_meta, items


def _gallery_dl_json(url: str, platform: str, max_items: int, fingerprint: dict | None = None) -> list:
    """Run `gallery-dl -j --range 1-max_items` and return the parsed message list.

    Raises RateLimitedError on timeout, or when 403 responses cross
    GALLERY_DL_403_THRESHOLD (throttled/challenged — surfaces to the flow's
    back-off and signals the operator to rotate egress IP). Returns [] on a clean
    failure (so an optional secondary source, e.g. TikTok photos, never aborts
    the whole list).
    """
    cmd = _gallery_dl_cmd(url, platform, ["-j", "--range", f"1-{max_items}"], fingerprint)
    # TikTok pacing (~1 request/post + sleep-request) makes deep listings slow
    # by design — budget generously so pacing never masquerades as a hang.
    pace = 8 if platform == "tiktok" else 2
    timeout = min(900, 120 + max_items * pace)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise RateLimitedError(str(e)) from e
    if proc.returncode != 0 and not proc.stdout.strip():
        _classify_error(RuntimeError(proc.stderr or proc.stdout))
        return []
    n403 = (proc.stderr or "").count("403")
    if n403:
        # Partial throttling: results parsed below may be incomplete. Logged so
        # a "0 photos" outcome is diagnosable as throttle vs genuinely none.
        print(f"[collect] gallery-dl {url}: {n403} '403' responses in stderr (throttled?)", flush=True)
        if n403 >= GALLERY_DL_403_THRESHOLD:
            # Heavy throttling: don't return silent-partial results — raise so the
            # flow defers the profile and the operator is prompted to rotate the IP.
            ck = _cookies_file(platform, (fingerprint or {}).get("_seed"))
            print(
                f"[collect] throttle threshold hit for {url} (cookie={ck.name if ck else 'none'}) "
                "— rotate egress IP / refresh cookie",
                flush=True,
            )
            raise RateLimitedError(
                f"{n403} 403 responses from gallery-dl ({url}) — egress likely throttled",
                code="challenge",
            )
    try:
        return json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        return []


def _list_tiktok(profile_url: str, max_items: int, fingerprint: dict | None = None) -> tuple[dict, list[dict]]:
    """TikTok listing: yt-dlp videos + gallery-dl photo posts + gallery-dl Stories.

    yt-dlp lists videos deeply and fast (TLS-impersonated, cookie-less) but is
    video-only for TikTok. gallery-dl (with login cookies + browser impersonation)
    surfaces photo posts and Stories that yt-dlp can't see; we take photo/story
    items from it and dedup videos by content_id.
    """
    fingerprint = fingerprint or _session_fingerprint(_handle_from_url(profile_url))
    items: list[dict] = []
    seen: set[str] = set()
    handle = _handle_from_url(profile_url)
    profile_meta: dict = {}

    # 1) Videos via yt-dlp (deep, fast). Failure here is non-fatal — gallery-dl
    #    photos/stories below may still yield content.
    try:
        opts = {**_yt_dlp_opts("tiktok", fingerprint), "extract_flat": True, "dump_single_json": True}
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(profile_url, download=False)
    except Exception as e:
        _classify_error(e)
        info = None
    info = info or {}
    entries = info.get("entries", [info]) if info else []
    handle = handle or info.get("uploader") or info.get("uploader_id") or info.get("id")
    if handle:
        profile_meta = {
            "handle": handle,
            "platform": "tiktok",
            "public_metadata": {"follower_count": info.get("channel_follower_count")},
        }
    for e in entries:
        if not e or not e.get("url"):
            continue
        cid = str(e.get("id"))
        if cid in seen:
            continue
        seen.add(cid)
        items.append({
            "content_id": cid,
            "content_type": "video",
            "is_video": True,
            "source_url": e["url"],
            "published_at": _epoch_to_iso(e.get("timestamp")),
            "caption": e.get("title") or e.get("description") or "",
            "hashtags": _extract_hashtags(e.get("title") or e.get("description") or ""),
            "public_counts": {
                "likes": e.get("like_count"),
                "comments": e.get("comment_count"),
                "views": e.get("view_count"),
                # yt-dlp names TikTok's shareCount `repost_count`. Videos are the
                # bulk of a TikTok account, so without this the share signal would
                # only ever exist on the gallery-dl photo-post path.
                "shares": e.get("repost_count"),
            },
        })

    base = profile_url.rstrip("/")

    # Let the yt-dlp burst and gallery-dl's per-post requests breathe apart —
    # stacked back-to-back they read as one large bot burst and get 403'd.
    time.sleep(random.uniform(*TIKTOK_PASS_DELAY_RANGE))

    # 2) Photo posts via gallery-dl. yt-dlp's flat listing also includes photo
    #    posts but mislabels them as /video/ entries (is_video=True, which would
    #    fail at download) — so a gallery-dl photo identification *overrides*
    #    the yt-dlp entry for the same content_id rather than being deduped away.
    try:
        photo_entries = _gallery_dl_json(f"{base}/posts", "tiktok", max_items, fingerprint)
    except RateLimitedError:
        photo_entries = []  # don't lose the videos already collected
    by_id = {item["content_id"]: idx for idx, item in enumerate(items)}
    for m in photo_entries:
        if not (isinstance(m, list) and len(m) == 2 and m[0] == 2 and isinstance(m[1], dict)):
            continue
        meta = m[1]
        if "imagePost" not in meta:
            continue
        cid = str(meta.get("id") or "")
        if not cid:
            continue
        images = (meta.get("imagePost") or {}).get("images") or []
        author = meta.get("author") or {}
        handle = handle or author.get("uniqueId")
        photo_item = {
            "content_id": cid,
            "content_type": "carousel" if len(images) > 1 else "image",
            "is_video": False,
            "source_url": f"https://www.tiktok.com/@{handle}/photo/{cid}",
            "published_at": _epoch_to_iso(meta.get("createTime")),
            "caption": meta.get("desc") or "",
            "hashtags": _extract_hashtags(meta.get("desc") or ""),
            "public_counts": _tiktok_counts(meta),
        }
        if cid in by_id:
            items[by_id[cid]] = photo_item  # correct the mislabeled yt-dlp entry
        else:
            seen.add(cid)
            by_id[cid] = len(items)
            items.append(photo_item)
        if not profile_meta and handle:
            profile_meta = {"handle": handle, "platform": "tiktok", "public_metadata": {}}

    # 3) Stories via gallery-dl (ephemeral; usually none active). Best-effort.
    time.sleep(random.uniform(*TIKTOK_PASS_DELAY_RANGE))
    try:
        story_entries = _gallery_dl_json(f"{base}/stories", "tiktok", min(max_items, 50), fingerprint)
    except Exception:
        story_entries = []
    for m in story_entries:
        if not (isinstance(m, list) and len(m) == 2 and m[0] == 2 and isinstance(m[1], dict)):
            continue
        meta = m[1]
        cid = str(meta.get("id") or "")
        if not cid or cid in seen:
            continue
        seen.add(cid)
        items.append({
            "content_id": cid,
            "content_type": "story",
            # Stories download via gallery-dl (cookies), not yt-dlp.
            "is_video": False,
            "source_url": meta.get("post_url") or f"https://www.tiktok.com/@{handle}/story/{cid}",
            "published_at": _epoch_to_iso(meta.get("createTime")) or _parse_gdl_date(meta.get("date")),
            "caption": meta.get("desc") or "",
            "hashtags": _extract_hashtags(meta.get("desc") or ""),
            "public_counts": _tiktok_counts(meta),
        })

    if not profile_meta:
        raise ProfileNotFoundError(f"could not resolve profile metadata for {profile_url}")
    return profile_meta, items


def _tiktok_counts(meta: dict) -> dict:
    """Public engagement counts from a gallery-dl TikTok post metadata object."""
    stats = meta.get("stats") or meta.get("statsV2") or {}
    def _int(v):
        try:
            return int(v) if v is not None else None
        except (TypeError, ValueError):
            return None
    return {
        "likes": _int(stats.get("diggCount")),
        "comments": _int(stats.get("commentCount")),
        "views": _int(stats.get("playCount")),
        # Shares are the closest public proxy to the distribution signal the
        # platforms actually reward, and they sit in the same stats object.
        "shares": _int(stats.get("shareCount")),
    }


IG_APP_ID = "936619743392459"


def _owner_id_from_entries(entries: list) -> str | None:
    """Pull the numeric owner/user id from gallery-dl `-j` post entries, if present."""
    for e in entries:
        if isinstance(e, list) and len(e) == 2 and e[0] == 2 and isinstance(e[1], dict):
            oid = e[1].get("owner_id")
            if oid:
                return str(oid)
    return None


def _shortcode_from_url(url: str | None) -> str | None:
    m = re.search(r"/(?:p|reel|reels|tv)/([^/?#]+)", url or "")
    return m.group(1) if m else None


def _instagram_api_session(handle: str, fingerprint: dict, referer_path: str = "") -> tuple | None:
    """Build an impersonated session for Instagram's private web API.

    Returns `(curl_cffi.requests, cookies, headers, browser)`, or None when the
    dependency or the cookie session is unavailable. Every IG web-API call needs
    BOTH the burner cookie session and a real browser TLS fingerprint (rule #1) —
    a plain `requests` call is 429'd even with valid cookies.
    """
    try:
        from curl_cffi import requests as creq
    except Exception:
        return None
    ck = _cookies_file("instagram", (fingerprint or {}).get("_seed"))
    if ck is None:
        return None
    try:
        cj = http.cookiejar.MozillaCookieJar(str(ck))
        cj.load(ignore_discard=True, ignore_expires=True)
    except OSError:
        return None
    cookies = {c.name: c.value for c in cj}
    headers = {
        "X-IG-App-ID": IG_APP_ID,
        "X-CSRFToken": cookies.get("csrftoken", ""),
        "X-Requested-With": "XMLHttpRequest",
        "Referer": f"https://www.instagram.com/{handle}/{referer_path}",
        "Accept-Language": (fingerprint or {}).get("accept_language", ACCEPT_LANGUAGE),
    }
    return creq, cookies, headers, (fingerprint or {}).get("browser", "chrome")


def _instagram_profile_info(handle: str, fingerprint: dict) -> dict:
    """Best-effort public account stats from IG's `web_profile_info` endpoint.

    Instagram's gallery-dl listing carries no account-level metadata, so IG
    profiles came back with an empty `public_metadata` while TikTok's carried a
    follower count. That absence removes the *denominator*: engagement can then
    only be compared in absolute terms, which ranks account size rather than
    content, and follower growth can never be tracked retroactively (the counts
    are only ever observable now — a backfill is impossible).

    Returns {user_id, follower_count, following_count, post_count} or {} on any
    failure. One request per account, reusing the listing's fingerprint; the
    resolved `user_id` is handed to `_instagram_clip_stats` so the pair costs one
    request, not two.

    `web_profile_info` is the only endpoint that still exposes a follower count
    to a web session — `users/<id>/info/` returns a trimmed object without one,
    and the profile HTML 302s. Failures are logged rather than swallowed: an
    empty `public_metadata` otherwise can't be told apart from an expired cookie
    session, a throttle, or an Instagram-side outage.
    """
    if not handle:
        return {}
    session = _instagram_api_session(handle, fingerprint)
    if session is None:
        print(f"[instagram] {handle}: no cookie session for profile stats", flush=True)
        return {}
    creq, cookies, headers, browser = session
    try:
        r = creq.get(
            f"https://www.instagram.com/api/v1/users/web_profile_info/?username={handle}",
            headers=headers, cookies=cookies, impersonate=browser, timeout=30,
        )
        if r.status_code != 200:
            print(
                f"[instagram] {handle}: profile stats unavailable "
                f"(HTTP {r.status_code}: {r.text[:160]})",
                flush=True,
            )
            return {}
        user = ((r.json().get("data") or {}).get("user")) or {}
    except Exception as e:
        print(f"[instagram] {handle}: profile stats request failed: {e}", flush=True)
        return {}
    if not user:
        print(f"[instagram] {handle}: profile stats response carried no user object", flush=True)
        return {}
    uid = user.get("id")
    return {
        "user_id": str(uid) if uid else None,
        "follower_count": (user.get("edge_followed_by") or {}).get("count"),
        "following_count": (user.get("edge_follow") or {}).get("count"),
        "post_count": (user.get("edge_owner_to_timeline_media") or {}).get("count"),
    }


def _instagram_clip_stats(user_id: str | None, handle: str, fingerprint: dict, max_items: int) -> dict:
    """Best-effort per-account Reel stats from IG's clips endpoint.

    The Reels grid the browser renders shows a view (play) count that the feed /
    gallery-dl listing omits. This queries the same endpoint the grid uses
    (`/api/v1/clips/user/`) via curl_cffi browser impersonation (a plain request is
    TLS-fingerprinted and 429'd), returning {shortcode: {views, comments, likes}}.
    A (paged) request per account; returns {} on any failure so a stats hiccup
    never breaks the listing. Works for any public account (competitors included).
    """
    session = _instagram_api_session(handle, fingerprint, referer_path="reels/")
    if session is None:
        return {}
    creq, cookies, headers, browser = session
    # Resolve the numeric user id if neither the listing nor the profile pass
    # supplied one.
    if not user_id:
        user_id = _instagram_profile_info(handle, fingerprint).get("user_id")
    if not user_id:
        return {}

    stats: dict = {}
    max_id = None
    try:
        for _ in range(4):  # cap pages; ~24 reels/page
            payload = {"target_user_id": str(user_id), "page_size": "24"}
            if max_id:
                payload["max_id"] = max_id
            r = creq.post(
                "https://www.instagram.com/api/v1/clips/user/", data=payload,
                headers=headers, cookies=cookies, impersonate=browser, timeout=30,
            )
            if r.status_code != 200:
                break
            body = r.json()
            for entry in body.get("items", []):
                m = entry.get("media", {}) or {}
                code = m.get("code")
                if not code:
                    continue
                stats[code] = {
                    "views": m.get("play_count") or m.get("ig_play_count"),
                    "comments": m.get("comment_count"),
                    "likes": m.get("like_count"),
                }
            paging = body.get("paging_info") or {}
            max_id = paging.get("max_id")
            if not paging.get("more_available") or not max_id or len(stats) >= max_items:
                break
            time.sleep(random.uniform(1.0, 2.5))  # pace paged requests
    except Exception:
        return stats
    return stats


def _reels_listing_entries(profile_url: str, max_items: int, fingerprint: dict) -> list:
    """Best-effort `gallery-dl -j` of the Instagram Reels tab.

    The Reels tab is a *separate* IG extractor that the profile feed (`/posts/`)
    omits, so Reels-only accounts would otherwise surface zero video. Returns the
    parsed entries, or [] on any failure — a Reels hiccup must never sink the
    primary posts listing.
    """
    base = profile_url if profile_url.endswith("/") else profile_url + "/"
    reels_url = base if base.rstrip("/").endswith("reels") else base + "reels/"
    cmd = _gallery_dl_cmd(reels_url, "instagram", ["-j", "--range", f"1-{max_items}"], fingerprint)
    timeout = min(600, 120 + max_items * 2)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return json.loads(proc.stdout or "[]")
    except (subprocess.TimeoutExpired, subprocess.SubprocessError, json.JSONDecodeError, OSError):
        return []


def _list_instagram_stories(profile_url: str, fingerprint: dict) -> list[dict]:
    """List an account's currently-active Instagram Stories via gallery-dl.

    Uses the Stories extractor (`instagram.com/stories/<handle>/`), which needs the
    burner cookie session + browser impersonation. Each story item is keyed by its
    `media_id`; download routes through the per-item Stories URL. Best-effort:
    returns [] on any failure or when no stories are active (they expire ~24h).
    """
    handle = _handle_from_url(profile_url)
    stories_url = f"https://www.instagram.com/stories/{handle}/"
    cmd = _gallery_dl_cmd(stories_url, "instagram", ["-j"], fingerprint)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        entries = json.loads(proc.stdout or "[]")
    except (subprocess.SubprocessError, json.JSONDecodeError, OSError):
        return []

    items: list[dict] = []
    seen: set[str] = set()
    for e in entries:
        if not (isinstance(e, list) and len(e) == 3 and e[0] == 3 and isinstance(e[2], dict)):
            continue
        fm = e[2]
        media_id = str(fm.get("media_id") or fm.get("shortcode") or "")
        if not media_id or media_id in seen:
            continue
        seen.add(media_id)
        ext = str(fm.get("extension") or "").lower()
        is_video = bool(fm.get("video_url")) or ext in {"mp4", "mov", "webm"}
        items.append({
            "content_id": media_id,
            "content_type": "story",
            "is_video": is_video,
            # Per-item Stories URL — gallery-dl downloads the single story from it.
            "source_url": f"https://www.instagram.com/stories/{handle}/{media_id}/",
            "published_at": _parse_gdl_date(fm.get("date") or fm.get("post_date")),
            "caption": "",
            "hashtags": [],
            # Stories have no public engagement counts.
            "public_counts": {"likes": None, "comments": None, "views": None, "shares": None},
        })
    return items


def _list_instagram(profile_url: str, max_items: int, fingerprint: dict | None = None) -> tuple[dict, list[dict]]:
    """Instagram listing via gallery-dl (`-j`), the primary/reliable IG path.

    `-j` prints one JSON array for the whole run: per-post metadata is a type-2
    Directory message `[2, meta]`; per-file metadata is a type-3 message
    `[3, url, meta]`. The type-2 message has no reliable video flag, so we scan
    the type-3 messages first to learn, per post, whether any file is a video —
    used only for a *provisional* content_type (download re-derives the truth).
    """
    handle = _handle_from_url(profile_url)
    fingerprint = fingerprint or _session_fingerprint(handle)
    list_url = _gallery_dl_list_url(profile_url, "instagram")
    cmd = _gallery_dl_cmd(list_url, "instagram", ["-j", "--range", f"1-{max_items}"], fingerprint)
    # Deeper listings page through more feed requests (each spaced by gallery-dl's
    # own sleep), so scale the timeout with max_items.
    timeout = min(600, 120 + max_items * 2)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise RateLimitedError(str(e)) from e

    if proc.returncode != 0 and not proc.stdout.strip():
        _classify_error(RuntimeError(proc.stderr or proc.stdout))
        raise ProfileNotFoundError(proc.stderr or proc.stdout or f"empty listing for {profile_url}")

    try:
        entries = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        entries = []

    # The posts feed omits the Reels tab (a separate IG extractor), so Reels-only
    # accounts surface zero video. Merge a best-effort Reels listing so a single run
    # spans every feed format. Reels carry their own type-3 video-file messages, so
    # the has_video logic below types them "video" (download re-derives anyway).
    entries += _reels_listing_entries(profile_url, max_items, fingerprint)

    # First pass: per-post video/media info from type-3 (file-level) messages.
    post_files: dict[str, dict] = {}
    for entry in entries:
        if not (isinstance(entry, list) and len(entry) == 3 and entry[0] == 3 and isinstance(entry[2], dict)):
            continue
        fm = entry[2]
        pid = str(fm.get("post_id") or fm.get("post_shortcode") or "")
        if not pid:
            continue
        finfo = post_files.setdefault(pid, {"has_video": False, "nums": set()})
        ext = str(fm.get("extension") or "").lower()
        if fm.get("video_url") or ext in {"mp4", "mov", "webm"}:
            finfo["has_video"] = True
        if fm.get("num") is not None:
            finfo["nums"].add(fm.get("num"))

    # Second pass: build items from type-2 (post-level) messages.
    items: list[dict] = []
    seen_ids: set[str] = set()
    profile_meta: dict = {}
    for entry in entries:
        if not (isinstance(entry, list) and len(entry) == 2 and entry[0] == 2 and isinstance(entry[1], dict)):
            continue
        meta = entry[1]
        if meta.get("type") != "post":
            continue
        content_id = str(meta.get("post_id") or meta.get("post_shortcode") or meta.get("shortcode") or "")
        if not content_id:
            continue
        # A post can appear in both the posts feed and the Reels tab — keep the
        # first (posts) occurrence; download re-derives the authoritative type.
        if content_id in seen_ids:
            continue
        seen_ids.add(content_id)
        media_count = meta.get("count") or 1
        finfo = post_files.get(content_id, {})
        seen_nums = len(finfo.get("nums") or set())
        has_video = bool(finfo.get("has_video"))
        # The number of distinct file-level `num`s is the authoritative media-item
        # count; the post-level `count` meta is unreliable (a single Reel reports
        # count=2 for its cover thumbnail, which mislabels Reels as carousels), so
        # only fall back to it when no file messages were seen.
        media_items = seen_nums or media_count
        # Provisional only — download_item re-derives from the files on disk.
        if has_video and media_items <= 1:
            content_type = "video"
        elif media_items > 1:
            content_type = "carousel"
        elif has_video:
            content_type = "video"
        else:
            content_type = "image"
        shortcode = meta.get("post_shortcode") or meta.get("shortcode")
        items.append({
            "content_id": content_id,
            "content_type": content_type,
            # Instagram downloads route through gallery-dl (reliable with
            # cookies) regardless of media type, so is_video stays False.
            "is_video": False,
            "source_url": meta.get("post_url") or (f"https://www.instagram.com/p/{shortcode}/" if shortcode else profile_url),
            "published_at": _parse_gdl_date(meta.get("date") or meta.get("post_date")),
            "caption": meta.get("description") or meta.get("content") or "",
            "hashtags": [t.lstrip("#") for t in (meta.get("tags") or [])] or _extract_hashtags(meta.get("description") or ""),
            "public_counts": {
                "likes": meta.get("likes"),
                "comments": meta.get("comments"),
                "views": meta.get("video_view_count") or meta.get("views"),
                # Instagram exposes no public share count — the key is kept so the
                # shape is identical across platforms and `None` reads as
                # "not available here", not "this listing forgot to look".
                "shares": None,
            },
        })
        if not profile_meta:
            profile_meta = {
                "handle": handle or meta.get("username") or meta.get("owner_id"),
                "platform": "instagram",
                "public_metadata": {},
            }

    if not profile_meta:
        # No posts parsed (empty/zero-content profile): still resolve the handle
        # from the URL so the caller gets a valid profile with an empty item list.
        if handle:
            profile_meta = {"handle": handle, "platform": "instagram", "public_metadata": {}}
        else:
            raise ProfileNotFoundError(f"could not resolve profile metadata for {profile_url}")

    # Account-level public metadata (follower count + friends). Best-effort — an
    # empty result leaves public_metadata as it was, and never breaks the listing.
    profile_info = _instagram_profile_info(profile_meta.get("handle") or handle, fingerprint)
    if profile_info:
        profile_meta["public_metadata"] = {
            "follower_count": profile_info.get("follower_count"),
            "following_count": profile_info.get("following_count"),
            "post_count": profile_info.get("post_count"),
        }
        print(f"[instagram] {handle}: follower_count={profile_info.get('follower_count')}", flush=True)

    # Enrich Reels with the view (play) count + comment count that IG's Reels grid
    # shows but the feed/gallery-dl listing omits — from the same endpoint the grid
    # uses. Best-effort, one paged request per account; failures leave counts as-is.
    # The user id resolved above is reused so the two passes cost one lookup.
    owner_id = _owner_id_from_entries(entries) or profile_info.get("user_id")
    clip_stats = _instagram_clip_stats(owner_id, handle, fingerprint, max_items)
    if clip_stats:
        matched = 0
        for it in items:
            st = clip_stats.get(_shortcode_from_url(it.get("source_url")))
            if not st:
                continue
            matched += 1
            pc = it["public_counts"]
            if st.get("views") is not None:
                pc["views"] = st["views"]
            if st.get("comments") is not None and not pc.get("comments"):
                pc["comments"] = st["comments"]
            if st.get("likes") is not None and not pc.get("likes"):
                pc["likes"] = st["likes"]
        print(f"[instagram] enriched {matched} item(s) with Reel view/comment counts")

    return profile_meta, items


def download_item(content_id: str, source_url: str, is_video: bool, content_type: str) -> tuple[list[str], str]:
    """Download a single item to DATA_DIR using the path implied by is_video.

    Returns (local_paths, resolved_content_type). The resolved content_type is
    re-derived from the downloaded files (`_derive_content_type`) — the files are
    the authoritative signal, so a single Reel is correctly classified "video"
    even when the listing metadata could only guess "image"/"carousel".
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    platform = "instagram" if "instagram.com" in source_url else "tiktok"
    # Seed the fingerprint on the profile handle in the source URL so a download
    # uses the same browser identity the listing did for that profile.
    fingerprint = _session_fingerprint(_handle_from_url(source_url))

    if is_video:
        opts = {
            **_yt_dlp_opts(platform, fingerprint),
            "outtmpl": str(DATA_DIR / "%(id)s.%(ext)s"),
        }
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(source_url, download=True)
            paths = [ydl.prepare_filename(info)]
            return paths, _derive_content_type(paths, content_type)
        except Exception as e:
            _classify_error(e)
            raise ContentGoneError(str(e)) from e

    cmd = _gallery_dl_cmd(source_url, platform, ["-D", str(DATA_DIR), "-o", f"filename={content_id}_{{num}}.{{extension}}"], fingerprint)
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if proc.returncode != 0:
        _classify_error(RuntimeError(proc.stderr or proc.stdout))
        raise ContentGoneError(proc.stderr or proc.stdout)

    matched = sorted(str(p) for p in DATA_DIR.glob(f"{content_id}_*"))
    if not matched:
        raise ContentGoneError(f"gallery-dl reported success but no files matched {content_id}_*")
    return matched, _derive_content_type(matched, content_type)


def _handle_from_url(profile_url: str) -> str | None:
    """Extract the readable @handle from a profile URL.

    Preferred over yt-dlp's metadata fields: TikTok's `uploader_id` is the
    internal secUid (not the readable handle), so the URL itself is the more
    reliable source for the Drive folder name (FR-009).
    """
    path = urlparse(profile_url).path.strip("/")
    if not path:
        return None
    segment = path.split("/")[0]
    return segment.lstrip("@") or None


def _epoch_to_iso(ts) -> str | None:
    if not ts:
        return None
    from datetime import datetime, timezone
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _extract_hashtags(text: str) -> list[str]:
    import re
    return re.findall(r"#(\w+)", text or "")


def validate_secrets() -> list[str]:
    """Log a warning for each expected cookie source that is missing/empty.

    Surfaces silent auth gaps at startup: without an Instagram cookie the IG
    listing only sees a few posts, and without TikTok cookies photo posts and
    stories 403. Returns the list of warning strings (also printed). A cookie
    pool directory with at least one non-empty file satisfies the platform.
    """
    warnings: list[str] = []

    def _has_pool(platform: str) -> bool:
        pool_dir = COOKIES_POOL_DIR / platform
        return pool_dir.is_dir() and any(p.stat().st_size > 0 for p in pool_dir.glob("*.txt"))

    if not _has_pool("instagram") and not (COOKIES_FILE.exists() and COOKIES_FILE.stat().st_size > 0):
        warnings.append("no Instagram cookie session — IG listings will surface only a few posts")
    if not _has_pool("tiktok") and not (TIKTOK_COOKIES_FILE.exists() and TIKTOK_COOKIES_FILE.stat().st_size > 0):
        warnings.append("no TikTok cookie session — TikTok photo posts and stories will 403")
    for w in warnings:
        print(f"[collect] WARNING: {w}", flush=True)
    return warnings
