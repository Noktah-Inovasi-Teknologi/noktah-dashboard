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
- **Account metadata (follower count).** `_instagram_profile_info` queries `web_profile_info` through
  `_instagram_api_session` (the shared cookie + impersonation helper) and fills `public_metadata`;
  the `user_id` it resolves is passed to `_instagram_clip_stats` so the two passes cost one lookup.
  The follower count is the **denominator** — without it IG engagement is only comparable in
  absolute terms, which ranks account size rather than content, and it can never be backfilled
  (counts are only ever observable *now*). It is the last web-reachable source: `users/<id>/info/`
  returns a trimmed object with no counts, and the profile HTML `302`s. Failures print their HTTP
  status — an empty `public_metadata` must be distinguishable from an expired cookie, a throttle, and
  an Instagram-side outage. **Known upstream breakage (2026-07-31):** `web_profile_info` returns
  `400 "Asset asset://laser.provider/ig_business_category_subvertical has been deleted"` — a Meta
  serializer regression, not auth or throttling. **Still broken as of 2026-08-01** and outside our
  control.
- **Follower count: GraphQL fallback (`_instagram_profile_info_graphql`), added 2026-08-01.** When
  `web_profile_info` yields no follower count but a `user_id` is available (from
  `_owner_id_from_entries`, which does not depend on the broken endpoint), roach falls back to
  Instagram's persisted GraphQL profile query. Measured live: `lasikasyik` went from no count at all
  to `follower_count=1342`, with the primary route still returning its 400 in the same request.
  **The critical detail — and what cost two wasted probes before it was found:** the doc_id
  (`27937681195819736`) has NOT rotated. What changed is that the query now **requires** a set of
  `__relay_internal__pv__*` Relay feature-flag variables. Omit any of them and the server replies
  `HTTP 200` with `{"errors": [... "execution error", "severity": "CRITICAL" ...], "data": null}` —
  indistinguishable at a glance from a dead doc_id, an auth failure, or a throttle. If this breaks
  again, **re-check the variable set against instaloader's `Profile._obtain_metadata` before
  assuming the doc_id rotated.** Both constants live at the top of `_instagram_profile_info_graphql`.
  The fallback only runs on the failure path, so a working `web_profile_info` still costs one request.
- **Cookies** (`secrets/cookies.txt`, gitignored) are required for IG depth; pools live in
  `secrets/cookies.d/<platform>/*.txt`.

## What each listing costs (feature 005)

`/list` returns a `request_stats` object counting this listing's network passes.
**Read the observability boundary before quoting it**: `gallery_dl_invocations`
and `yt_dlp_invocations` count *process invocations*, each of which pages
internally — the true HTTP request count is not observable from outside the
subprocess and is deliberately **not** reported as one. Only
`direct_api_requests` (our own `curl_cffi` calls) is an exact count. Do not sum
them into a single "requests" number: that would present an estimate and an
observation in the same field, which constitution VI forbids.

This exists so a change's marginal collection volume can be stated against a
measured baseline (FR-024) — instrumenting runs that were going to happen anyway,
rather than spending real requests to measure how many we spend.

### gallery-dl drops Instagram comment counts

roach lists Instagram via `gallery-dl`, which resolves to
`/v1/feed/user/{user_id}/` (`extractor/instagram.py:1151`, gallery-dl 1.32.6).
Its post parser maps **`like_count` and nothing else count-shaped** (`:240`) —
`comment_count` is never read, though the same media object shape carries it
(proven by `_instagram_clip_stats`, which reads `comment_count` off the clips
endpoint at [collect.py:767](service/roach/collect.py#L767)).

So the missing comment count on 379 carousel/image rows is very likely a
**mapping gap in a third-party library, not a platform limit** — the payload is
already being fetched. gallery-dl `-j` emits only its own mapped dict and offers
no raw passthrough, so recovering it needs a direct call in the
`_instagram_clip_stats` mould. Confirming this needs ONE live probe; the
determination is recorded in `config/field_availability.yaml`.

## Extraction output is validated, versioned, and structured (feature 007)

`/analyze` no longer returns three opaque strings. It returns beats over a closed vocabulary, a
verbatim subtitle with an explicit absence reason, attributes, and a summary — validated
**client-side** before it leaves this service.

- **Three statuses, all `200 OK`**: `success`, `quarantined` (failed validation twice), `failed`
  (the call never produced a validatable response). **A quarantine is NOT an HTTP error** — see the
  error-contract bullet below; the harvest flow branches on `{ok: false}`, so a model writing bad
  JSON must not arrive looking like a throttle or a dead profile.
- **`finish_reason == "length"` is checked BEFORE parsing.** A truncated beat array is valid JSON
  that satisfies the schema; nothing else can catch it. This is the single most important line in
  the change.
- **Two salvage paths were DELETED, not disabled**: `_extract_json`'s `\{.*\}` regex fallback and
  `_to_text`'s list coercion. `tests/test_analyze_validation.py` parses this module with `ast` and
  asserts `re` is not even imported — a substring check could not tell code from the comment
  explaining the deletion, and that comment must stay.
- **Exactly one retry, carrying the validator's own error text quoted** (not paraphrased), as
  follow-up turns. **The media is not re-sent** — it is already in the conversation, and
  re-uploading base64 video to restate a JSON complaint would roughly double every retry's input
  cost.
- **The schema and vocabulary are mounted, not baked**: `config/extraction/` is bind-mounted
  read-only into roach *and* the Prefect service. The `beats.function` enum is **generated from the
  vocabulary at load time** — the enum in `schema_v1.json` is a placeholder. Editing these files
  does NOT need an image rebuild; editing `analyze.py` still does.
- **`GET /extraction-config`** reports the model routing and versions this service is actually
  using. Callers must ask rather than read their own environment: the model per case comes from
  `config/ai/models.yaml` plus roach's in-memory rotation (`model_rotation.py`: the next model
  after 3 consecutive failures, back to the first after 60 minutes), so guessing locally sees one
  model where there are two and silently skips a real measurement. `models` in the response has
  each case's accepted list and rotation state. `OPENROUTER_MODEL` / `OPENROUTER_IMAGE_MODEL` no
  longer choose anything. `model_override` (calibration) bypasses rotation and is never counted.
- **`usage` is returned, not just logged.** `_call_model` used to print the usage object and discard
  it in the same breath, which is why no cost baseline existed anywhere. `model_served` and
  `provider` are read off the RESPONSE — provider routing runs with `allow_fallbacks`, so what was
  asked for and what answered are different questions.
- **Measured 2026-08-07**: video ~$0.0019/item (`xiaomi/mimo-v2.5`), image ~$0.0004 and carousel
  ~$0.0008 (`google/gemini-2.5-flash-lite`). Re-measured 2026-09-25 for the accepted lists: video
  $0.0007–0.0016 (`xiaomi/mimo-v2.6-flash`), image $0.00008 / carousel $0.00013
  (`qwen/qwen3.7-flash`); per-model figures are in `config/ai/models.yaml`. `ANALYZE_VIDEO_MAX_TOKENS` raised 8000 → 12000 to fit
  a full transcript alongside the structured fields.

## Conventions

- **Stateless per call** — no DB in roach; persistence + orchestration are on the Prefect side.
- **Error contract** — `429` → `rate_limited`/`challenge`; `404` → not-found; analysis failures return
  `{status: "failed"}` in the body rather than raising.
- **The error envelope is for platform failures, not roach's own bugs.** `api.py`'s catch-alls
  re-raise `INTERNAL_BUG_ERRORS` (`TypeError`/`AttributeError`/`NameError`/`ImportError`) instead of
  returning `500 *_failed`. The harvest flow *branches* on roach's classification (404 ⇒ skip the
  profile, 429 ⇒ back off + rotate egress), so a bug dressed in a platform-failure envelope is
  indistinguishable from the real thing — that is how adding `stories_only` to `list_profile` turned
  into tests asserting `500 == 404` and `500 == 429`. Everything else goes through `_failure()`,
  which prints the full traceback before returning the (unchanged) envelope.
- **Token usage is logged, not discarded.** `_call_model` sends `X-Title`/`HTTP-Referer` (OpenRouter
  attributes dashboard spend by these) and requests `usage: {include: true}` so the response carries a
  resolved USD `cost`; `_log_usage` prints `call_site`, `model`, `client`, and the token counts. The
  `client` label comes in on the `/analyze` request — roach can't know it. Best-effort: accounting
  never breaks an analysis.
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

Probe sparingly. Each of these is a real request from your egress IP, and a handful of exploratory
calls in a row is enough to earn `"Please wait a few minutes before you try again."` — at which point
the next scheduled harvest pays for the debugging session.

---

**Last Updated:** 2026-07-31
**Service:** roach (feature 002-social-content-harvest)
