# Roach Service Development Rules

## Overview

Roach (`service/roach/`) is a stateless FastAPI microservice that lists, downloads, and analyzes
short-form content from public Instagram and TikTok profiles (Reels/videos, image posts, carousels,
stories). It exposes low-level primitives (`/list`, `/download`, `/analyze`); all orchestration
(pacing, rate-limiting, back-off, de-dup, Drive/Sheet delivery) lives in the Prefect
`social-harvest-*` flows. Spec: `specs/002-social-content-harvest/`.

## 🚨 Rule #1 — ALWAYS impersonate a browser for Instagram/TikTok (or get flagged)

Instagram and TikTok **`429`/`403` any request without a real browser TLS fingerprint.** This is the
single most important rule for this service. Every network call to these platforms MUST impersonate a
browser:

| Path | How to impersonate |
|------|--------------------|
| yt-dlp (video) | `curl_cffi` via the `curl-cffi` extra + `YT_DLP_IMPERSONATE_POOL` (already wired) |
| gallery-dl (listing/photos/stories) | `-o browser=<chrome\|firefox\|...>` (see `_gallery_dl_cmd`) |
| **Direct API calls** (e.g. IG Reels/clips `/api/v1/clips/user/`) | **`curl_cffi.requests` with `impersonate=<browser>`** + the per-profile fingerprint |

- **Never call Instagram/TikTok with plain `requests` / `urllib` / `httpx`.** It returns a misleading
  `429` even when cookies and the endpoint are correct — which looks like a rate-limit or dead endpoint
  and wastes debugging time. The failure is the TLS fingerprint, not the IP or auth.
- When adding ANY new IG/TikTok call, use impersonation **from the first line** — do not "test with
  plain requests first." Reference implementation: `_instagram_clip_stats` in `collect.py`.
- Reuse the **per-profile fingerprint** (`_session_fingerprint(handle)` → `fp["browser"]`,
  `fp["_seed"]`, `fp["accept_language"]`) so yt-dlp, gallery-dl, and direct-API passes all present one
  consistent identity per profile, varying across profiles.

## Instagram specifics

- **Reels are a separate extractor.** The `/posts/` feed omits the Reels tab, so `_list_instagram`
  also lists `/<user>/reels/` and merges (dedup by content id, posts win). Type is inferred from
  distinct file-level `num`s (a single Reel reports `count: 2` for its cover — do NOT use the post-level
  `count` for carousel detection). `download_item` re-derives the authoritative type from files.
- **Views/comments enrichment.** The feed exposes only `likes`; views/plays and comment counts come
  from the Reels-grid endpoint `/api/v1/clips/user/` (`_instagram_clip_stats`, curl_cffi impersonation),
  one paged request per account, patched onto `public_counts`. Works for any public account (no Graph
  API needed). Best-effort — never breaks the listing. Views are video-only (carousels/images have none).
- **Stories (Instagram + TikTok): on-demand via `stories_only: true`** in the `/list` request. IG uses
  the Stories extractor (`instagram.com/stories/<handle>/`, each item keyed by `media_id`,
  `_list_instagram_stories`); returns only currently-active stories (ephemeral ~24h, no public counts).
  Regular feed listings do NOT include stories — they're collected by the dedicated
  `social-harvest-stories` flow, meant to run frequently.
- **Cookies** (`secrets/cookies.txt`, gitignored) are required for IG depth; pools live in
  `secrets/cookies.d/<platform>/*.txt`.

## Conventions

- **Stateless per call** — no DB in roach; persistence + orchestration are on the Prefect side.
- **Error contract** — `429` → `rate_limited`/`challenge`; `404` → not-found; analysis failures return
  `{status: "failed"}` in the body rather than raising.
- **Best-effort enrichment/side calls must never break the primary listing/download** (wrap in
  try/except → return partial, log).
- **Editing `collect.py`/`api.py`/`analyze.py` requires an image rebuild** (`docker-compose up -d
  --build roach`) — roach source is baked into the image, NOT volume-mounted (unlike the Prefect
  service). Validate IG changes with a live `/list` probe against a real account.
- **Secrets** (`ROACH_API_KEY`, `OPENROUTER_API_KEY`) via env only; cookies gitignored.

## Verifying an Instagram change

Rebuild, then probe `/list` end-to-end (this is the real test — mocks won't catch platform behavior):

```bash
docker-compose up -d --build roach
key=$(grep ROACH_API_KEY service/roach/.env | cut -d= -f2-)
curl -s -X POST http://localhost:8081/list -H "X-API-KEY: $key" -H "Content-Type: application/json" \
  -d '{"profile_url":"https://www.instagram.com/<acct>/","platform":"instagram","max_items":30}' | \
  python -c "import sys,json,collections; d=json.load(sys.stdin); i=d['items']; \
print(collections.Counter(x['content_type'] for x in i)); \
print('with views:', sum(1 for x in i if (x.get('public_counts') or {}).get('views')))"
```

Expect a mix of `video`/`carousel`/`image`, and views populated on video items.

---

**Last Updated:** 2026-07-16
**Service:** roach (feature 002-social-content-harvest)
