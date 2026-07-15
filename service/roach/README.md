# Roach

Lists, downloads, and analyzes content from Instagram and TikTok profiles, using
[yt-dlp](https://github.com/yt-dlp/yt-dlp) (video) and
[gallery-dl](https://github.com/mikf/gallery-dl) (images/carousels/stories).

> ## ⚠️ ALWAYS impersonate a browser when calling Instagram — never a bare HTTP client
>
> Instagram (and TikTok) **flag and `429`/`403` any request that lacks a real browser TLS
> fingerprint**. Every network path to these platforms in roach MUST impersonate a browser:
>
> - **yt-dlp** → `curl_cffi` (already wired via the `curl-cffi` extra + `YT_DLP_IMPERSONATE_POOL`).
> - **gallery-dl** → the `-o browser=<chrome|firefox|...>` option.
> - **Direct API calls** (e.g. the Instagram Reels/clips endpoint) → **`curl_cffi.requests` with
>   `impersonate=<browser>`**, using the per-profile fingerprint. A plain `requests`/`urllib`/`httpx`
>   call WILL be flagged and returns a misleading `429` even when cookies and the endpoint are fine.
>
> If you add any new Instagram/TikTok call, use impersonation from the first line — do not "test with
> plain requests first." See `_instagram_clip_stats` in `collect.py` for the reference pattern.

## Status

Internal HTTP API (FastAPI, listens on port 8080 inside the container; published to the host as
8081 — see `docker-compose.yml`), consumed by the Prefect `social-harvest-recent` and
`social-harvest-window` flows in `service/prefect/flows/` (spec
`specs/002-social-content-harvest/`). Roach is stateless per call — all orchestration (pacing,
rate limiting, back-off, de-duplication, Drive/Sheet delivery) lives in those Prefect flows. See
`specs/002-social-content-harvest/contracts/roach-api.md` for the full API contract.

Every endpoint except `GET /health` requires an `X-API-KEY` header matching `ROACH_API_KEY`.

## CLI (manual/debug use)

```bash
# List every content URL on a profile (no download)
docker exec roach python main.py list "https://www.tiktok.com/@username"
docker exec roach python main.py list "https://www.instagram.com/username/"

# Download a single piece of content
docker exec roach python main.py download "https://www.tiktok.com/@username/video/123..."
```

Downloaded files land in `data/` (gitignored) for the CLI, or the shared `/data` volume
(`social_data`) when called via the API.

## Instagram cookies

Instagram profile listing is unreliable without an authenticated session — anonymous requests
typically only see the first few posts. Export cookies from a logged-in browser session to
`secrets/cookies.txt` (Netscape format, e.g. via the "Get cookies.txt" browser extension). The
`secrets/` directory is gitignored; never commit this file.

TikTok profile listing generally works without cookies for public accounts (photo posts and
stories still need `secrets/tiktok_cookies.txt`).

## Instagram content coverage (Reels + engagement)

A single `/list` spans every feed format. Two things about Instagram are non-obvious:

- **Reels come from a separate extractor.** The profile feed (`/posts/`) does **not** include the
  Reels tab, so roach also lists `/<user>/reels/` and merges the results (de-duplicated by content id,
  posts win). Without this a Reels-heavy account surfaces zero video. Per-post `content_type` is
  provisional (a single Reel reports `count: 2` for its cover thumbnail, so type is inferred from the
  distinct file-level `num`s, not the post `count`); `download_item` re-derives the authoritative type
  from the files on disk.
- **Views + comments are enriched from the Reels grid endpoint.** The feed/gallery-dl listing exposes
  only `likes` — Instagram withholds view/play and comment counts from it (and from yt-dlp and
  instaloader). roach fetches them from the **same endpoint the Reels grid uses**
  (`/api/v1/clips/user/`) via `curl_cffi` browser impersonation (`_instagram_clip_stats`), one paged
  request per account, and patches `public_counts.views`/`comments` onto the matching video items. This
  works for **any public account** (competitors included) — no Instagram Graph API / account ownership
  needed. It is best-effort: a failure leaves counts as-is and never breaks the listing.
- **Stories** (Instagram + TikTok) are fetched on demand via `stories_only: true` in the `/list`
  request — a fast, single-endpoint call (IG uses the Stories extractor; each item keyed by `media_id`)
  that returns only currently-active stories. Stories are ephemeral (~24h) and carry no public counts,
  so they are collected by the dedicated **`social-harvest-stories`** flow (run frequently to catch
  them) rather than mixed into the regular feed listing.

### Cookie pools (optional)

To spread load across several burner sessions, drop multiple Netscape cookie files into
`secrets/cookies.d/<platform>/*.txt` (`instagram` or `tiktok`). One file is picked
deterministically per profile, so different profiles use different sessions and one stale cookie
doesn't block every profile. The single `cookies.txt` / `tiktok_cookies.txt` files remain the
fallback. On boot, roach logs a warning for any platform with no usable cookie session.

## Anti-detection

Roach relies on TLS-fingerprint impersonation (curl_cffi via yt-dlp; `browser=` via gallery-dl),
cookie sessions, and randomized request pacing — not proxies. Behavioral pacing (100/hr cap,
5–10s delays, 60/120/240s back-off) lives on the Prefect side.

- **Fingerprint rotation** — `YT_DLP_IMPERSONATE_POOL` is a comma list of impersonate targets. One
  is chosen per profile (seeded on the handle) and used consistently across that profile's yt-dlp,
  gallery-dl, **and direct-API (`curl_cffi` clips) passes**, but varies across profiles/runs, so
  traffic isn't one identical fingerprint. The Reels/clips call reuses this same per-profile
  fingerprint + browser — see the impersonation warning at the top of this file.
- **Egress / mobile hotspot** — by default all requests use the host's direct egress. Running the
  Docker host behind a **mobile hotspot** gives a shared, trusted carrier IP that's hard to block;
  to rotate it, toggle the phone's mobile data / airplane mode for a fresh IP. Set `PROXY_URL`
  (or `INSTAGRAM_PROXY_URL` / `TIKTOK_PROXY_URL`) only if you route through a proxy instead.
- **Throttle signal** — when a gallery-dl listing returns ≥ `GALLERY_DL_403_THRESHOLD` `403`s, roach
  raises a `rate_limited`/`challenge` response instead of returning partial results, so the flow
  defers the profile. If several profiles are blocked in a run, the completion notification prompts
  the operator to rotate the hotspot IP (`HARVEST_ROTATE_IP_THRESHOLD` on the Prefect side).

## Analysis efficiency & accuracy

- Videos are compressed with ffmpeg before analysis (downscaled to `ANALYZE_VIDEO_MAX_HEIGHT`,
  `ANALYZE_VIDEO_FPS`, `ANALYZE_VIDEO_CRF`; **audio kept** for the transcript) — ~5–10x fewer tokens
  with negligible accuracy loss. Carousel images are capped (`ANALYZE_MAX_IMAGES`) and downscaled
  (`ANALYZE_IMAGE_MAX_DIM`). On any ffmpeg failure it falls back to the original file.
- OpenRouter requests disable internal reasoning (fixes the empty-content failure), request
  structured JSON output, and pin provider routing (`OPENROUTER_PROVIDER_ORDER`, primary Xiaomi).

See `.env.example` for the full list of tuning variables.
