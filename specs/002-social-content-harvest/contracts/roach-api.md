# Contract: roach internal HTTP API

Internal-only FastAPI service on `dashboard-networks`, base `http://roach:8080`. All non-health
endpoints require header `X-API-KEY: <ROACH_API_KEY>` (raw check; 401 otherwise). Called by Prefect
`social.*` tasks via `httpx`. Roach is **stateless per call** — the Prefect flow owns the loop,
pacing, hourly cap, back-off, dedupe, and delivery (R1/R5). Downloaded files are written to the
shared `social_data` volume, and roach returns their paths (not bytes).

## GET /health
No auth. → `200 {"status":"ok"}`. Backs the compose health check.

## POST /list
List every publicly visible content item for a profile, merging the video (yt-dlp) and non-video
(gallery-dl) paths (R4). Selects anonymous vs burner-cookie session automatically by platform (R3).

For **Instagram**, `/list` additionally: (a) merges a second **Reels-tab** pass (`/<user>/reels/`)
since the profile feed omits Reels (R11), and (b) **enriches video items with `views` (play count) and
`comments`** from the Reels-grid API (`/api/v1/clips/user/`) via `curl_cffi` browser impersonation
(R12) — the feed itself exposes only `likes`. `views` is video-only (carousels/images have none). IG
Stories are not listed. **⚠️ All Instagram/TikTok calls MUST impersonate a browser** (curl_cffi /
gallery-dl `browser=`); a bare HTTP client is `429`/`403`-flagged (see R12, `.claude/rules/backend/roach.md`).

Request:
```json
{ "profile_url": "https://www.tiktok.com/@name", "platform": "tiktok", "max_items": 30, "stories_only": false }
```
`stories_only: true` returns **only currently-active Stories** (fast single-endpoint call for frequent
story checks; IG via the Stories extractor). Stories are ephemeral (~24h) and carry no public counts.
Response `200`:
```json
{ "ok": true,
  "profile": { "handle": "name", "platform": "tiktok",
               "public_metadata": { "follower_count": 8421, "following_count": 310, "post_count": 194 } },
  "items": [
    { "content_id": "7630040448702385429", "content_type": "video", "is_video": true,
      "source_url": "https://...", "published_at": "2026-07-10T04:12:00Z",
      "caption": "…", "hashtags": ["#x"],
      "public_counts": { "likes": 12, "comments": 3, "views": 900, "shares": 47 } }
  ] }
```
Errors: `404 {"ok":false,"code":"profile_not_found"}` (private/non-existent — flow skips, continues);
`429 {"ok":false,"code":"rate_limited"|"challenge","reason":"…"}` (flow backs off + defers profile).
Items list MAY be empty (zero-content profile → empty folder, logged note, run continues).
`public_counts` MUST never include reach/impressions/saves (FR-003). `shares` is public (visible on
any post) and therefore allowed: TikTok `shareCount` / yt-dlp `repost_count`; Instagram exposes no
public share count, so IG items carry `"shares": null` — the key is always present so `null` reads as
"not available on this platform", not "this listing forgot to look".

`public_metadata` is best-effort account-level data and MAY be `{}`. TikTok fills `follower_count`
from yt-dlp. Instagram queries `web_profile_info` (cookie session + browser impersonation, one request
per listing, `user_id` shared with the Reels-grid pass); the follower count is the **denominator** —
without it IG engagement can only be compared in absolute terms, which ranks account size rather than
content. Failures are logged with their HTTP status so an empty `public_metadata` can be told apart
from an expired cookie session, a throttle, and an Instagram-side outage.

## POST /download
Download a single item to the shared volume using the path implied by `is_video` (yt-dlp for video,
gallery-dl for non-video).

Request:
```json
{ "content_id": "7630…", "source_url": "https://…", "is_video": true, "content_type": "video" }
```
Response `200`:
```json
{ "ok": true, "content_id": "7630…", "local_paths": ["/data/7630….mp4"] }
```
Errors: `404 profile_not_found`/`content_gone` (e.g., expired story → flow logs + skips, not a run
failure); `429 rate_limited|challenge` (back-off); `500 download_failed` (item skipped + logged,
run continues, FR-020).

## POST /analyze
Analyze one already-downloaded item (R9). Video → transcript+flow+summary; image/carousel → flow +
summary with `subtitle` empty (no spoken audio).

Request:
```json
{ "content_id": "7630…", "local_paths": ["/data/7630….mp4"], "content_type": "video",
  "client": "Ecky Dental Center" }
```
`client` is optional and used for **attribution only** — roach is stateless and cannot know whose
harvest it is analyzing, so the caller labels the call and roach echoes it into its OpenRouter
token-usage log (`[openrouter] usage call_site=… client=… prompt_tokens=… cost_usd=…`). The harvest
engine passes the run's `harvest_name`.

Response `200`:
```json
{ "ok": true, "content_id": "7630…",
  "analysis": { "subtitle": "…", "flow": "1. hook … 2. …", "summary": "…", "status": "success" } }
```
On model failure → `200` with `analysis.status = "failed"` and `error` set (download retained, Sheet
row still written — edge case), so the flow does not treat analysis failure as an item loss.

## Error contract
All error bodies use `{ "ok": false, "code": "<slug>", "reason": "<human text>" }`. HTTP status
carries the class (`404` gone/not-found, `429` throttle/challenge, `500` internal). Secrets are
never echoed in `reason` (FR-019).

The envelope describes **platform** failures only. A bug inside roach (`TypeError`, `AttributeError`,
`NameError`, `ImportError`) is deliberately NOT caught — it propagates, logging a traceback, rather
than being returned as a well-formed `500 *_failed`. The flow branches on this classification (`404`
⇒ skip the profile, `429` ⇒ back off and rotate egress), so a bug wearing a platform-failure envelope
is indistinguishable from the real thing; that is exactly how a `list_profile` signature drift
surfaced as three tests asserting `500 == 200`/`404`/`429`. Genuine failures still log their full
traceback before returning the envelope — `reason` alone is often one unattributable line.
