# System Audit — roach / songbird / shared infrastructure

**Audit date:** 2026-07-31
**Branch:** `feat/social-harvest-songbird` (working tree contains uncommitted changes; see git status)
**Scope:** description only. No changes proposed, no changes made.

Live-system figures were read from the running stack (`postgres`, `prefect`, `roach`
containers, all up at audit time). Code references are to files in this repository.

---

## 0. Corrections to the audit brief's premises

Two model assignments in the request do not match what is deployed. Everything below
uses the deployed reality.

| Brief says | Actually deployed | Evidence |
|---|---|---|
| MiMo-V2.5 does roach extraction | True **for video only**. `MODEL` = `xiaomi/mimo-v2.5` | [analyze.py:25](service/roach/analyze.py#L25) |
| — | Image/carousel/story analysis runs on **`google/gemini-2.5-flash-lite`** via `OPENROUTER_IMAGE_MODEL` | [analyze.py:26](service/roach/analyze.py#L26); `docker exec roach printenv` → `OPENROUTER_IMAGE_MODEL=google/gemini-2.5-flash-lite` |
| Songbird generates with Gemini 2.5 Flash Lite | Songbird generates with **`xiaomi/mimo-v2.5`** (`OPENROUTER_MODEL`, no Gemini override on the Prefect services) | [openrouter_tasks.py:27](service/prefect/tasks/openrouter_tasks.py#L27); `docker exec prefect printenv` → `OPENROUTER_MODEL=xiaomi/mimo-v2.5` |

Both services call OpenRouter only. There is no direct Anthropic/OpenAI/Google SDK
anywhere in the codebase.

---

# ROACH

Source: [service/roach/](service/roach/) — `api.py`, `collect.py`, `analyze.py`, `main.py`.
Spec: `specs/002-social-content-harvest/`.

## 1. Entry points, scheduling, and how a run is triggered

Roach is a **stateless FastAPI microservice with no scheduler of its own**. It exposes
primitives; all orchestration lives in Prefect.

### Roach entry points

| Entry point | File | Auth | Notes |
|---|---|---|---|
| `GET /health` | [api.py:55](service/roach/api.py#L55) | none | |
| `POST /list` | [api.py:60](service/roach/api.py#L60) | `X-API-KEY` | `{profile_url, platform, max_items=30, stories_only=false}` |
| `POST /download` | [api.py:74](service/roach/api.py#L74) | `X-API-KEY` | `{content_id, source_url, is_video, content_type}` |
| `POST /analyze` | [api.py:100](service/roach/api.py#L100) | `X-API-KEY` | `{content_id, local_paths, content_type}` |
| CLI `python main.py list\|download\|analyze` | [main.py:47](service/roach/main.py#L47) | n/a | Separate, simpler code path than the API; uses its own yt-dlp opts and `build_report()` |

Auth is a single shared static key compared with `!=` ([api.py:29-32](service/roach/api.py#L29-L32)).
The service is on the internal `dashboard-networks` bridge but **is also published on host
port 8081** ([docker-compose.yml](docker-compose.yml)).

### Scheduling (all on the Prefect side)

Defined in [service/prefect/prefect.yaml](service/prefect/prefect.yaml). Roach itself has none.

| Deployment | Schedule (Asia/Jakarta) | Flow |
|---|---|---|
| `social-harvest-recent` | none — manual | [social_harvest_recent.py](service/prefect/flows/social_harvest_recent.py) |
| `social-harvest-window` | none — manual | [social_harvest_window.py](service/prefect/flows/social_harvest_window.py) |
| `social-harvest-stories` | none — manual | [social_harvest_stories.py](service/prefect/flows/social_harvest_stories.py) |
| `social-harvest-sync` | `0 3 * * *` (daily 03:00) | [social_harvest_sync.py](service/prefect/flows/social_harvest_sync.py) |
| `harvest-3mo-<client>` × **18** | monthly, 1st of month, staggered 30 min from 17:30; slots 14–18 spill to the 2nd 00:00–02:00 | `social_harvest_window_flow`, `days: 90` |

The 18 per-client deployments each carry one hard-coded profile URL
([prefect.yaml:149-383](service/prefect/prefect.yaml#L149-L383)). Two clients (Breko,
The StarFit) are deliberately absent — no handle in the Clients sheet.

### Run mechanics

`run_harvest` ([social_harvest.py:300](service/prefect/flows/common/social_harvest.py#L300))
is the loop: for ≤5 profiles per run (`MAX_PROFILES`), it calls `/list`, applies a depth
selector (recent-N / relative window / absolute date range), then per item: dedup check →
`/download` → `/analyze` → Drive upload → 2 sheet appends → dedup ledger write → signal
store write.

## 2. Fields captured per post, as stored

Roach's listing shape is identical across platforms (both branches build the same dict):
`content_id`, `content_type`, `is_video`, `source_url`, `published_at`, `caption`,
`hashtags[]`, `public_counts{likes, comments, views}`.

Where those come from, per platform:

| Stored field | Instagram source | TikTok source |
|---|---|---|
| `content_id` | `post_id` \| `post_shortcode` \| `shortcode` (stories: `media_id`) — [collect.py:764](service/roach/collect.py#L764), [:681](service/roach/collect.py#L681) | yt-dlp `id`, or gallery-dl `id` — [collect.py:431](service/roach/collect.py#L431), [:471](service/roach/collect.py#L471) |
| `content_type` | inferred from distinct file-level `num`s + video flag; **provisional** — [collect.py:782-789](service/roach/collect.py#L782-L789) | `"video"` from yt-dlp; `carousel`/`image` from `imagePost.images` length; `story` — [collect.py:479](service/roach/collect.py#L479) |
| `is_video` | always `False` (IG downloads route through gallery-dl) — [collect.py:796](service/roach/collect.py#L796) | `True` for yt-dlp entries, `False` for photo/story |
| `source_url` | `post_url` \| `/p/<shortcode>/` | entry `url` \| `/@h/photo/<id>` \| `post_url` |
| `published_at` | gallery-dl `date`/`post_date` → ISO-8601 Z — [collect.py:298](service/roach/collect.py#L298) | epoch `timestamp`/`createTime` → ISO-8601 Z |
| `caption` | `description` \| `content` | yt-dlp `title`/`description`, gallery-dl `desc` |
| `hashtags` | gallery-dl `tags[]` (`#` stripped), else regex `#(\w+)` over caption | regex over caption/desc only |
| `public_counts.likes` | `likes`, then patched from clips API `like_count` | `like_count` / `stats.diggCount` |
| `public_counts.comments` | `comments`, then patched from clips API `comment_count` | `comment_count` / `stats.commentCount` |
| `public_counts.views` | `video_view_count`\|`views`, then **overwritten** by clips API `play_count`/`ig_play_count` | `view_count` / `stats.playCount` |

Profile metadata is thin: `{handle, platform, public_metadata}`. `public_metadata` is
`{"follower_count": …}` for TikTok and **`{}` (always empty) for Instagram**
([collect.py:808-812](service/roach/collect.py#L808-L812)).

Instagram Reel view/comment enrichment comes from a separate authenticated call to
`/api/v1/clips/user/` via `curl_cffi` impersonation, max 4 pages × 24
([`_instagram_clip_stats`, collect.py:560](service/roach/collect.py#L560)). Best-effort —
returns `{}` on any failure.

### Storage-layer field names

Three destinations, three (overlapping) schemas.

**Google Sheets** — per-account quarterly tab and per-run detail sheet, `ACCOUNT_HEADER`
([social_harvest.py:95-101](service/prefect/flows/common/social_harvest.py#L95-L101)):

```
id, username, platform, content_id, content_type, source_url, published_at,
caption, hashtags, views, likes, comments, drive_file_ids,
subtitle, content_flow, summary, harvest_name, harvest_date, advertisement
```
(detail sheet adds `account_folder_id`.)

**Postgres `harvested_signals`** — see §Shared. **Postgres `harvested_items`** — ledger
only: no captions, no metrics.

Note the loss at the Postgres boundary: `source_url`, `drive_file_ids`, `harvest_name`,
`harvest_date` and `username`-as-displayed exist only in Sheets. `hashtags` is stored in
Postgres as a **space-joined string**, not an array
([social_harvest.py:536](service/prefect/flows/common/social_harvest.py#L536)).

## 3. Views, shares, comment text

| Signal | Status | Detail |
|---|---|---|
| **View/play counts** | **PARTIAL — video only** | Field exists everywhere; populated only for video. Measured: 293/656 signal rows have `views` — 269 IG video + 24 TikTok video. **0 of 361 IG carousel/image rows** have views. Instagram simply does not expose plays for non-video here. |
| **Share counts** | **ABSENT** | No `shares`/`share_count`/`repost` anywhere — not requested from either platform, no column in any sheet or table. TikTok's gallery-dl `stats` object carries `shareCount`, but `_tiktok_counts` ([collect.py:527](service/roach/collect.py#L527)) reads only `diggCount`/`commentCount`/`playCount`. |
| **Comment *counts*** | **PARTIAL — video only** | Same 293/656 pattern as views. |
| **Comment *text*** | **ABSENT** | No comment bodies are fetched, stored, or analyzed. Nothing in `/list`, `/download` or `/analyze` touches comment threads. |

## 4. Authentication

**Authenticated, with a burner-account cookie session.**

| Platform | Path | Credential | Location |
|---|---|---|---|
| Instagram | gallery-dl listing, download, stories, clips API | required (anonymous surfaces only a few posts) | `service/roach/secrets/cookies.txt`, or pool `secrets/cookies.d/instagram/*.txt` — [collect.py:23,30](service/roach/collect.py#L23) |
| Instagram | yt-dlp | same cookie file | [collect.py:247](service/roach/collect.py#L247) |
| TikTok | gallery-dl (photos, stories) | required — photo/story endpoints 403 without it | `secrets/tiktok_cookies.txt` or `secrets/cookies.d/tiktok/*.txt` |
| TikTok | yt-dlp (videos) | **deliberately cookie-less** — login cookies break the extractor | [collect.py:245-248](service/roach/collect.py#L245-L248) |

Whose credentials: burner accounts, per the module docstring
([collect.py:5-6](service/roach/collect.py#L5-L6)) and `.claude/rules/backend/roach.md`.
No named individual's account is referenced in code.

Storage: plain Netscape cookie files bind-mounted at `/app/secrets`
([docker-compose.yml](docker-compose.yml)), gitignored, **unencrypted**. When a pool
directory exists, a file is picked deterministically per profile handle by SHA-256 modulo
([`_seed_index`, collect.py:188](service/roach/collect.py#L188)) so one profile always
presents the same identity. `validate_secrets()` ([collect.py:911](service/roach/collect.py#L911))
logs a warning at boot for each missing session but does not fail startup.

`ROACH_API_KEY` and `OPENROUTER_API_KEY` come from `service/roach/.env` (separate from the
root `.env`).

## 5. Re-scraping — is capture once-only?

**Capture is effectively once-only for successful items.** The de-dup gate is
[social_harvest.py:414-418](service/prefect/flows/common/social_harvest.py#L414-L418):

```python
record = await social_dedup_check(platform, content_id, drive_target)
if record and record.get("analysis_status") == "success":
    summary["items_skipped_dedup"] += 1
    continue          # <- no download, no analyze, no signal write
```

Consequences:

- A post's `views`/`likes`/`comments` are frozen at the values seen on the **first**
  successful harvest. They are never refreshed, even though the monthly `harvest-3mo-*`
  deployments re-list the same 90-day window every month and the platform numbers keep moving.
- `social_signal_record` uses `ON CONFLICT (platform, content_id) DO UPDATE`
  ([social_tasks.py:260](service/prefect/tasks/social_tasks.py#L260)) — the refresh
  machinery exists but is unreachable for successful items, because the `continue` fires first.
- The **only** re-scrape path is failure retry: a `failed` (or `pending`) ledger record is
  purged — Drive files deleted, sheet rows deleted, ledger row deleted — and the item is
  harvested fresh ([social_harvest.py:419-433](service/prefect/flows/common/social_harvest.py#L419-L433)).
- The dedup key includes `drive_target`, so the *same* post harvested into two different
  account folders is captured twice. Measured: 6 such (platform, content_id) pairs.
- `social-harvest-sync` writes back only the reviewer-edited `advertisement` boolean
  ([social_harvest_sync.py:114](service/prefect/flows/social_harvest_sync.py#L114)). It
  updates no metric.

Measured: 656 signal rows, all first harvested between 2026-07-15 and 2026-07-28; no row
carries a second observation of any metric because the schema has no room for one.

## 6. Rate limiting, backoff, retry, failure handling

### Roach-side

| Control | Value | Reference |
|---|---|---|
| TLS impersonation pool | `chrome,chrome-124,safari,edge-99`, chosen per profile handle | [collect.py:47](service/roach/collect.py#L47) |
| TikTok gallery-dl inter-request sleep | `1.5-3.0` s (`sleep-request`) | [collect.py:76](service/roach/collect.py#L76) |
| TikTok yt-dlp pagination sleep | `1.5` s | [collect.py:78](service/roach/collect.py#L78) |
| TikTok inter-pass jitter | uniform `5.0-10.0` s, applied twice per profile | [collect.py:90,454,497](service/roach/collect.py#L90) |
| IG clips API paging pause | uniform `1.0-2.5` s | [collect.py:633](service/roach/collect.py#L633) |
| gallery-dl 403 threshold | ≥3 `403`s in stderr ⇒ raise `RateLimitedError(code="challenge")` | [collect.py:69,377](service/roach/collect.py#L69) |
| Listing subprocess timeout | `min(900, 120 + max_items × pace)`, pace 8 (TikTok) / 2 (IG); timeout ⇒ `RateLimitedError` | [collect.py:363-368](service/roach/collect.py#L363-L368) |
| Optional proxy | `PROXY_URL` / per-platform; **off by default** | [collect.py:57-59](service/roach/collect.py#L57-L59) |
| Retries | **none** — roach never retries a scrape itself | |

Error classification is **substring matching on exception text**
([`_classify_error`, collect.py:153](service/roach/collect.py#L153)): `_CHALLENGE_MARKERS`
→ `challenge`, `_RATE_LIMIT_MARKERS` → `rate_limited`, `_NOT_FOUND_MARKERS` →
`ProfileNotFoundError`. Mapped to HTTP 429 / 404 / 500 in `api.py`; unclassified errors
fall through to 500 with the raw reason string.

Best-effort degradation is explicit: TikTok photo pass swallows `RateLimitedError`
([collect.py:462](service/roach/collect.py#L462)), stories pass swallows everything
([collect.py:500](service/roach/collect.py#L500)), Reels listing returns `[]` on any
failure ([collect.py:654](service/roach/collect.py#L654)), clip-stats enrichment returns
`{}` ([collect.py:634](service/roach/collect.py#L634)).

### OpenRouter-side (inside `/analyze`)

`MAX_ATTEMPTS = 4`; 429 back-off `[10, 20, 40]` s, honouring `Retry-After` when numeric
([analyze.py:88-91,157-165](service/roach/analyze.py#L88-L91)). Any other `>= 400` raises
immediately — no retry. Empty content and JSON parse failures consume an attempt and
retry. HTTP timeout 180 s.

### Prefect-side

| Control | Value | Reference |
|---|---|---|
| Hourly cap | `RateWindow(limit=100, window_seconds=3600)` — in-memory, per flow run | [social_harvest.py:351](service/prefect/flows/common/social_harvest.py#L351) |
| Inter-item delay | uniform 5–10 s, skipped after the last item | [utility_tasks.py:22](service/prefect/tasks/utility_tasks.py#L22) |
| Download backoff | `[60, 120, 240]` s, 2 attempts; second failure sets `profile_blocked` and defers the rest of the profile | [social_harvest.py:75,438-470](service/prefect/flows/common/social_harvest.py#L75) |
| Task retries | `social.profile.list` 1×/120 s; `social.item.download` 1×/30 s; `social.item.analyze` 1×/30 s; dedup/signal tasks 2×/10 s | [social_tasks.py](service/prefect/tasks/social_tasks.py) |
| HTTP timeouts | list 1500 s, analyze 600 s, default 200 s | [social_tasks.py:60,105,163](service/prefect/tasks/social_tasks.py#L60) |
| Profile cap | 5 per run, silently truncated with a warning | [social_harvest.py:355](service/prefect/flows/common/social_harvest.py#L355) |
| Notification | optional Apprise block; adds an "rotate the hotspot IP" hint at ≥2 blocked profiles | [social_harvest.py:268-297](service/prefect/flows/common/social_harvest.py#L268-L297) |

Failure isolation: an analyze exception is caught and converted to
`{status: "failed", …}` so the download is still delivered
([social_harvest.py:480-487](service/prefect/flows/common/social_harvest.py#L480-L487));
a signal-store write failure is logged and swallowed
([social_harvest.py:544-545](service/prefect/flows/common/social_harvest.py#L544-L545));
`run_harvest` never raises.

## 7. Extraction prompts, verbatim

Both live in [service/roach/analyze.py](service/roach/analyze.py). There are exactly two.

### `VIDEO_PROMPT` — [analyze.py:62-74](service/roach/analyze.py#L62-L74)

```
You are given a short-form social media video (TikTok/Instagram Reel), including its visuals and audio.

Respond with ONLY a JSON object (no markdown fences, no extra text) with exactly these keys:
- "subtitle": full verbatim transcript of every spoken word, in the original spoken language. Also transcribe any on-screen text (captions, overlays) that is not spoken, prefixed with [on-screen]. If there is no speech and no on-screen text, use an empty string.
- "flow": a short numbered breakdown of how the content is structured, e.g. hook, setup, main point, call to action (use whatever stages actually appear in this video). Account for on-screen text, visual cuts, and demonstrations, not just what is spoken.
- "summary": a concise 2-4 sentence summary of what the video is about and its key message.

Base every field only on what is actually present in the video. Do not invent, guess, or add content that is not there.
```

(The source wraps three of these lines with a trailing `\`; the reproduction above is the
assembled string.)

### `IMAGE_PROMPT` — [analyze.py:76-85](service/roach/analyze.py#L76-L85)

```
You are given one or more images from a social media post (image post, carousel, or story). There is no audio.

Respond with ONLY a JSON object (no markdown fences, no extra text) with exactly these keys:
- "flow": a short numbered breakdown of how the content is structured (e.g. what each image shows in sequence, any on-screen text, the overall narrative or message progression).
- "summary": a concise 2-4 sentence summary of what the post is about and its key message.

Base every field only on what is actually shown in the images. Do not invent, guess, or add content that is not there.
```

There is **no system prompt** — the prompt is the first element of a single user message,
followed by the media part(s) ([analyze.py:123](service/roach/analyze.py#L123)).

### Output schema

Enforced server-side by OpenRouter `json_schema`, `strict: true`
([`_response_format`, analyze.py:94-112](service/roach/analyze.py#L94-L112)):

```json
{
  "type": "json_schema",
  "json_schema": {
    "name": "analysis",
    "strict": true,
    "schema": {
      "type": "object",
      "properties": { "<key>": {"type": "string"}, ... },
      "required": ["<key>", ...],
      "additionalProperties": false
    }
  }
}
```

- Video call: `keys = ["subtitle", "flow", "summary"]`, `max_tokens` 8000.
- Image call: `keys = ["flow", "summary"]`, `max_tokens` 3000; `subtitle` is
  back-filled as `""` client-side ([analyze.py:264](service/roach/analyze.py#L264)).

`flow` and `summary` are declared as **plain strings with no structure, length bound, or
enumeration**. "A short numbered breakdown" is prose guidance only — nothing checks that
`flow` contains stages, numbering, or anything at all.

### Is output validated?

**Structurally yes (by the provider), semantically no.** Layers:

1. `strict` json_schema — only for providers that honour it.
2. `_extract_json` ([analyze.py:130](service/roach/analyze.py#L130)) — tolerates fences,
   then falls back to a `\{.*\}` regex over the raw text. A truncated response can pass
   this and lose content silently.
3. `_to_text` ([analyze.py:320](service/roach/analyze.py#L320)) — flattens list-valued
   `flow`/`subtitle` to newline-joined strings, because the model sometimes returns arrays
   despite the schema.
4. `analyze_item` ([analyze.py:275](service/roach/analyze.py#L275)) returns
   `{subtitle, flow, summary, status, error}`; **any** exception becomes
   `status: "failed"` with all three fields `""`.

There is **no** check that the transcript matches the audio, that `flow` is non-empty,
that `summary` is 2–4 sentences, or that the content is in a plausible language. An empty
string is an entirely valid result at every layer.

Client-side pre-processing before the call: ffmpeg re-encode to ≤480p/15fps/CRF 30 with
AAC 64k audio ([analyze.py:182](service/roach/analyze.py#L182)); images downscaled to
≤1024px, max 8 per carousel ([analyze.py:59-60,215](service/roach/analyze.py#L59-L60)).
Both fall back to the original file on any ffmpeg failure.

Sampling parameters: **none set** — no `temperature`, `top_p`, `seed`, or `stop`. Only
`max_tokens`, `reasoning: {enabled: false}`, and provider order
`["xiaomi", "digitalocean", "novita", "parasail"]` with `allow_fallbacks: true`
([analyze.py:39-45,115-127](service/roach/analyze.py#L39-L45)).

## 8. Where extraction output is stored, and its queryability

Three destinations, written in this order per item:

| Destination | Fields | Queryable as structured fields? |
|---|---|---|
| Per-account Google Sheet, quarterly tab | `subtitle`, `content_flow`, `summary` (cols 14–16) | No — spreadsheet cells |
| Per-run "Social Harvest Detail" Sheet | same | No |
| Postgres `harvested_signals` | `subtitle TEXT`, `content_flow TEXT`, `summary TEXT` | **Rows are queryable; the analysis itself is free text.** |

So: the three analysis outputs are addressable as named columns and can be filtered with
SQL `LIKE`/trigram, but **nothing inside them is structured**. There is no table of flow
stages, no hook/CTA extraction, no topic or entity table, no vector index. The `flow`
column holds whatever prose the model produced.

Downstream, the only programmatic consumer treats them as opaque text: songbird truncates
them to 220 characters each for the prompt
([songbird.py:305-311](service/prefect/flows/common/songbird.py#L305-L311)), and theme
induction feeds `summary` (first 200 chars) to another LLM call
([songbird_themes.py:221-224](service/prefect/tasks/songbird_themes.py#L221-L224)).

Measured coverage over 656 signal rows:

| Column | Non-empty | Note |
|---|---|---|
| `summary` | 643 (98.0%) | |
| `content_flow` | 643 (98.0%) | |
| `subtitle` | 284 (43.3%) | Expected — images/carousels get `""` by design; 293 rows are video |

`build_report()` ([analyze.py:326](service/roach/analyze.py#L326)) writes a `report.md`
but is only reachable from the CLI, not the API path.

## 9. Failure rate, last 30 days

The audit window (2026-07-01 → 2026-07-31) contains the entire harvest history — the
first harvest is 2026-07-12, the last 2026-07-28. So "last 30 days" = all data.

**Analysis outcome (`harvested_items.analysis_status`), n=691:**

| Status | Count | Rate |
|---|---|---|
| `success` | 673 | 97.40% |
| `failed` | 18 | **2.60%** |
| `pending` | 0 | — |

This counts items where the model call ultimately failed but the media was still
delivered. It does **not** count semantic failures (empty or wrong analysis), which are
not detectable — see §7.

**Prefect task-run level:**

| Task | COMPLETED | FAILED | Rate |
|---|---|---|---|
| `social.item.analyze` | 700 | 1 | 0.14% |
| `openrouter.chat.complete` (songbird) | 90 | 10 | **10.0%** |

**Flow-run level, last 30 days:**

| Flow | COMPLETED | FAILED | CRASHED | CANCELLED |
|---|---|---|---|---|
| `social-harvest-window` | 23 | 1 | 2 | 1 |
| `social-harvest-recent` | 11 | 0 | 0 | 0 |
| `social-harvest-stories` | 1 | 0 | 0 | 0 |
| `social-harvest-sync` | 1 | 0 | 0 | 0 |
| `songbird-batch-plan` | 8 | 0 | 1 | 1 (CANCELLING) |
| `content-plan-to-jira-pipeline` | 2 | 1 | 0 | 0 |

Scrape-level failures (profiles skipped, items rate-limited, blocked profiles) are
counted in the per-run `summary` dict and written to `service/prefect/data/*.json`, but
are **not aggregated anywhere** — there is no failure-rate metric, dashboard, or alert.
The figures above were reconstructed by querying the Prefect and application databases
directly.

---

# SONGBIRD

Source: [service/prefect/flows/common/songbird.py](service/prefect/flows/common/songbird.py)
(engine, 997 lines), three flow entrypoints, four task modules.
Spec: `specs/003-songbird-content-generation/`.

## 1. Full pipeline, trigger → stored brief

Trigger surfaces:

| Flow | Deployment schedule | Dates | Target |
|---|---|---|---|
| `songbird-monthly-plan` | `0 6 1 * *` (monthly, 06:00 WIB, 1st) — but ships with `client: ""`, `month: ""`, so the scheduled run cannot succeed unattended | yes | `draft` \| `live` |
| `songbird-batch-plan` | none — manual; `month` defaults to next month | yes | `draft` \| `live` |
| `songbird-generate` | none — manual | no | `draft` only |

All three delegate to `run_generation` ([songbird.py:643](service/prefect/flows/common/songbird.py#L643)).
Steps as numbered in the source:

1. **Resolve quantity** — explicit `quantity` wins; else `songbird_config_content_mix`
   reads the Clients worksheet's `Post`/`Story`/`Short Video` columns
   ([songbird_tasks.py:379](service/prefect/tasks/songbird_tasks.py#L379)). Zero/blank ⇒
   that type is never generated. All-zero or missing row ⇒ raises (fail fast, FR-003b).
2. **Assign dates** — `_distribute_dates` spreads `quantity` dates evenly over the month
   ([songbird.py:193](service/prefect/flows/common/songbird.py#L193)). Engine-owned; the
   prompt explicitly forbids the model from emitting dates.
3. **Gather signals** —
   - exemplar budget = `max(8, min(2 × quantity, 24))`, ×1.5 (capped at 24) when no KB;
   - own handles from Clients-sheet `Instagram`/`TikTok` URLs + `CLIENT_SOCIAL.own` + run param;
   - competitors from the Hashmaps `CLIENT_SOCIAL` block + run param;
   - `songbird_client_context` fetches ≤40 current `knowledge_records`;
   - `songbird_top_performers` runs twice (own budget = `ceil(0.4 × budget)`, competitors get the remainder).
4. **Grade grounding** — one of `knowledge_base+signal` / `signal_only` /
   `knowledge_base_only` / `params_only`, recorded in `summary.grounding`.
5. **Strategy layers** — `detect_trends` (burst detection) and
   `songbird_theme_induction` (**a second LLM call**) → `allocate_slots` (Thompson
   sampling). All three are wrapped in try/except and are non-fatal.
6. **Generate** — one `_generate_bucket` call **per content type**, each retried up to
   `MAX_TOPUP_ATTEMPTS = 3` until its quota is met. Buckets are then round-robin
   interleaved so publish dates alternate types.
7. **Build rows** — `_idea_cells` maps each idea into the 20-column v5 layout.
8. **Deliver** — draft: `drive_folder_ensure("Songbird Drafts", …)` under the client's
   `Content Plan Folder ID` (falling back to `SONGBIRD_DRIVE_PARENT_ID`), new Sheet per
   run. Live: name-aligned append to the live worksheet.

Then the CLI writes the full result to `service/prefect/data/songbird_*_<ts>.json`.

**Where the brief is "stored":** a Google Sheet. Nothing songbird produces is written to
Postgres — no briefs table, no run table, no idea table. The rationale fields
(`adapted_pattern`, `source_exemplar`, `rationale`) are **generated but deliberately not
written to the sheet** (`RATIONALE_COLUMNS = []`,
[songbird.py:142](service/prefect/flows/common/songbird.py#L142)); they survive only in
the returned dict, the Prefect run result, and the local JSON file.

## 2. Every prompt, verbatim

Model: `xiaomi/mimo-v2.5` (not Gemini — see §0). Three distinct prompts exist.

### 2a. Generation system prompt — [songbird.py:441-449](service/prefect/flows/common/songbird.py#L441-L449)

```
Anda adalah content strategist berpengalaman untuk brand Indonesia. Tulis ide konten dalam Bahasa Indonesia yang natural, dengan code-mixing Bahasa Inggris seperlunya (nama brand, hashtag, istilah baku seperti 'reels'/'engagement', frasa/tagline yang sedang tren) — JANGAN menerjemahkan paksa istilah tersebut. Tiru gaya code-mixing yang terlihat pada knowledge base klien dan konten yang di-harvest. Adaptasi POLA yang terbukti perform (hook, format, pacing), JANGAN menyalin konten contoh secara verbatim. Performa tidak dijamin.
```

### 2b. Generation user prompt — assembled at [songbird.py:489-514](service/prefect/flows/common/songbird.py#L489-L514)

Template with substitutions marked `{…}`:

```
Klien: {client_name}
Platform: {platform or '(umum)'}
Target audiens: {audience or '(umum)'}
Tujuan kampanye: {goal or '(brand awareness)'}
Tone: {tone or '(sesuai brand)'}
Content pillars: {pillars or '(bebas, sesuai brand)'}

{kb_section}Konten yang terbukti perform (pelajari polanya, jangan salin):
{exemplars}

{trend_block}{plan_section}Buatkan TEPAT {quantity} ide konten yang berbeda-beda. {mix_instruction}Untuk setiap ide isi:
- topik: judul ide yang spesifik dan deskriptif (bukan satu kata).
- purpose_theme: 1-2 kalimat tujuan edukatif/persuasif konten ini.
- strategic_application: Awareness | Consideration | Conversion.
{format_brief}- caption: caption siap posting dalam Bahasa Indonesia — buka dengan hook kuat di kalimat pertama, badan yang jelas tapi ringkas, tutup dengan CTA, lalu hashtag relevan di baris terakhir.
- adapted_pattern: pola menang yang diadaptasi (atau 'umum' bila tanpa sinyal).
- source_exemplar: handle/konten sumber (kosong bila tanpa sinyal).
- rationale: alasan ide ini cocok untuk klien + tujuannya.

Tandai fakta spesifik yang belum pasti (angka, nama dokter, harga, testimoni) sebagai [PLACEHOLDER: ...] untuk diisi editor — jangan mengarang. JANGAN menyertakan tanggal.
```

On a top-up attempt this is appended ([songbird.py:599](service/prefect/flows/common/songbird.py#L599)):

```
\n\nHINDARI mengulang topik yang sudah ada: {existing topics, "; "-joined}
```

**`{kb_section}`** — with KB records ([songbird.py:455-456](service/prefect/flows/common/songbird.py#L455-L456)):

```
Pengetahuan brand (knowledge base):
- {subject}: {information}
  ... (one line per record, up to 40)

```

Without KB records ([songbird.py:458-465](service/prefect/flows/common/songbird.py#L458-L465)):

```
Pengetahuan brand (knowledge base): TIDAK TERSEDIA untuk klien ini.
Karena itu, jadikan konten yang di-harvest di bawah sebagai SATU-SATUNYA acuan brand: simpulkan gaya bahasa, positioning, layanan, dan istilah yang dipakai dari konten milik klien terlebih dahulu, lalu konten kompetitor sebagai pembanding pola. JANGAN mengarang fakta spesifik (nama dokter, harga, alamat, jam buka, klaim medis) — tulis sebagai [PLACEHOLDER: ...] agar diisi editor.

```

**`{mix_instruction}`** — per-type call ([songbird.py:474](service/prefect/flows/common/songbird.py#L474)):

```
SEMUA ide harus berbentuk **{bucket}**.
```

or, when no mix is configured ([songbird.py:477-480](service/prefect/flows/common/songbird.py#L477-L480)):

```
Pilih `bentuk` yang paling cocok per ide, ditulis persis salah satu dari: Post | Story | Short Video.
```

**`{format_brief}`** — `_FORMAT_BRIEFS` ([songbird.py:112-137](service/prefect/flows/common/songbird.py#L112-L137)), one of:

*Post:*
```
- shoot_guide: isi tanda '-' (Post tidak butuh pengambilan footage).
- reference: rancang sebagai CAROUSEL, tulis slide demi slide dengan struktur 'SLIDE n: <judul>' lalu 'Visual:', 'Headline:', 'Body Text:', dan CTA. Slide 1 = hook utama; SLIDE 2 HARUS berdiri sendiri sebagai hook kedua, karena Instagram menyajikan ulang carousel dengan slide 2 di depan bagi yang belum swipe. Slide terakhir = CTA.
```

*Story:*
```
- shoot_guide: rencana pengambilan NYATA per scene ('Scene n (x detik): ...') — shot, angle, blocking, dan tekankan ambience nyata yang perlu direkam (lokasi, cahaya, mood, b-roll, tekstur) agar tidak terasa 100% AI.
- reference: alur Story antar-frame + elemen interaktif (polling/kuis/sticker) bila cocok.
```

*Short Video:*
```
- shoot_guide: KONSEP + durasi + talent, lalu breakdown per scene ('SCENE n: <nama> (Lokasi - x detik)') dengan Shot, Blocking, teks overlay, dan pacing. Scene 1 wajib hook 3 detik pertama.
- reference: link konten referensi bila ada, atau deskripsi alur/gaya editing.
```

*Fallback for any other type ([songbird.py:134-137](service/prefect/flows/common/songbird.py#L134-L137)):*
```
- shoot_guide: panduan pengambilan footage nyata bila relevan, selain itu '-'.
- reference: alur konten atau referensi visual.
```

**`{exemplars}`** — grouped by content-type bucket, each line
([songbird.py:308-311](service/prefect/flows/common/songbird.py#L308-L311)):

```
[{bucket}]
- [{milik klien|kompetitor}] @{profile_key} · ~{engagement} interaksi · {views}rb views · ER {rate}% · {n} hari lalu
  hook: "{first 140 chars of caption before the first #}"
  alur: {content_flow, 220 chars} | ringkasan: {summary, 220 chars}
```

Or, with nothing harvested ([songbird.py:325](service/prefect/flows/common/songbird.py#L325)):
```
(belum ada sinyal konten yang di-harvest — bertumpu pada KB + parameter)
```

**`{trend_block}`** ([songbird.py:483](service/prefect/flows/common/songbird.py#L483)) — omitted when empty:
```
Tema yang sedang naik (pertimbangkan, jangan dipaksakan):
{token} ({n}x), {token} ({n}x), ...

```

**`{plan_section}`** ([songbird.py:404-426,485-487](service/prefect/flows/common/songbird.py#L404-L426)) — omitted when empty:
```
Rencana tema per slot (ikuti urutan ini):
{i}. Tema '{theme}' — TERBUKTI perform. Adaptasi polanya dengan SUDUT PANDANG BARU (angle/hook berbeda), jangan mengulang konten yang sama.
{i}. Tema '{theme}' — belum teruji. Eksplorasi angle baru untuk menguji minat audiens.
...

```

### 2c. Theme-induction prompts — [songbird_themes.py:227-237](service/prefect/tasks/songbird_themes.py#L227-L237)

System:
```
Anda adalah content strategist. Kelompokkan konten ke dalam tema besar berbahasa Indonesia yang ringkas (2-4 kata per tema). Setiap konten masuk ke TEPAT satu tema.
```

User:
```
Kelompokkan {n} konten berikut menjadi maksimal {max_themes} tema.
Balas dengan daftar tema beserta index kontennya.

0. {summary or caption, whitespace-collapsed, 200 chars}
1. {…}
...
```

`max_themes` defaults to 8, `max_tokens` 1500.

That is the complete inventory. No other prompt strings are sent to any model from the
songbird path.

## 3. Retrieval step

Two independent retrievals feed the prompt.

### 3a. Knowledge base — `songbird_client_context` ([songbird_tasks.py:110](service/prefect/tasks/songbird_tasks.py#L110))

```sql
SELECT client_name, subject, information
FROM knowledge_records
WHERE superseded_by IS NULL
  AND (client_key = $1 OR client_name ILIKE $2 OR similarity(client_key, $1) > 0.3)
ORDER BY similarity(client_key, $1) DESC, "timestamp" DESC
LIMIT $3            -- default 40
```

Trigram similarity gathers candidates only; identity is then confirmed by
`_is_same_client` ([songbird_tasks.py:79](service/prefect/tasks/songbird_tasks.py#L79)),
which requires one name's token set to be a **subset** of the other's. A client with no KB
gets zero records rather than a near-miss client's. No relevance filter beyond that — all
accepted records go into the prompt.

### 3b. Performance signals — `songbird_top_performers` ([songbird_tasks.py:161](service/prefect/tasks/songbird_tasks.py#L161))

Phase 1 (ranking columns for the whole window population):

```sql
SELECT id, platform, profile_key, content_type, published_at,
       views, likes, comments, advertisement, caption, hashtags
FROM harvested_signals
WHERE lower(profile_key) = ANY($1::text[])
  AND (published_at IS NULL OR published_at >= now() - make_interval(days => $2))
  AND advertisement = false
```

Phase 2 hydrates only the selected `id`s with `subtitle`, `content_flow`, `summary`.

**How many items:** budget = `max(8, min(2 × quantity, 24))`, boosted ×1.5 (cap 24) when
no KB. Own share = `ceil(0.4 × budget)`; competitors get the remainder. Called twice —
own and competitor sets are ranked independently.

**Filters applied:**

| Filter | Where | Effect |
|---|---|---|
| `lower(profile_key) = ANY(handles)` | SQL | exact-lowercase handle match; the trigram index on `profile_key` is not used here |
| rolling window, default **180 days** | SQL | undated rows are deliberately kept |
| `advertisement = false` | SQL | reviewer-flagged paid/boosted posts excluded |
| `advertisement is True` | [songbird_ranking.py:207](service/prefect/tasks/songbird_ranking.py#L207) | belt-and-braces re-check |
| `engagement < MIN_ENGAGEMENT (3)` | [songbird_ranking.py:213](service/prefect/tasks/songbird_ranking.py#L213) | dropped as noise |
| bucket quota | `exemplar_quota` | budget apportioned to the content mix, floor of 1 per requested type |
| per-account cap | `select_exemplars` | `max(2, ceil(quota/accounts))`, relaxed to `cap+1` then `want` if the bucket underfills |

**Is a performance/outcome filter applied?** Yes, and it is the core of the retrieval —
but it is a *ranking* over engagement counts, not an outcome measurement. `score_rows`
([songbird_ranking.py:186](service/prefect/tasks/songbird_ranking.py#L186)):

```
p_* = midrank / (n+1), shrunk toward 0.5 by n/(n+SHRINK_K)      # SHRINK_K = 3
video ("Short Video" bucket):  base = 0.5·p_eng + 0.5·p_views,
                               dampened by  RATE_FLOOR + (1−RATE_FLOOR)·min(1, p_rate/0.5)
non-video:                     base = p_eng
final = ((1−0.30)·base + 0.30·p_global_within_bucket) × recency(half-life 90d, floor 0.65)
```

Partitions are `(profile_key, bucket)` — the question answered is "did this overperform
for *its own account, in its own format*", not "is this the biggest post". Engagement =
`likes + comments`; `views` participate only for the video bucket, and only above
`VIEW_FLOOR = 500` for the rate term.

**Retrieval is top-performers-only.** No underperformers, no random sample, no negative
control set is retrieved — see the PRESENT/ABSENT table.

**Measured state of this retrieval:** the 18 accounts in `harvested_signals` are all
*client-owned*. The 4 competitor handles configured in `CLIENT_SOCIAL`
(`lasikindonesia`, `kmneyecare`, `jeceyehospital`, `sebayadental`) have **never been
harvested**, so every competitor retrieval currently returns zero rows and every plan is
grounded in the client's own content alone.

## 4. Brief output schema and enforcement

Requested keys — `IDEA_KEYS` ([songbird.py:150-154](service/prefect/flows/common/songbird.py#L150-L154)):

```python
["topik", "purpose_theme", "strategic_application",
 "shoot_guide", "reference", "caption",
 "adapted_pattern", "source_exemplar", "rationale"]
```

`+ ["bentuk"]` only when no content mix is configured — otherwise the engine owns `bentuk`
because the call *is* the type.

Wire schema — `array_schema` ([openrouter_tasks.py:104-132](service/prefect/tasks/openrouter_tasks.py#L104-L132)):
a strict `json_schema` object with one `items` array property, each item an object of
**string** properties, all `required`, `additionalProperties: false`.

### Enforcement, layer by layer

| Layer | Strength |
|---|---|
| OpenRouter `strict: true` json_schema | **Strong** — for compliant providers |
| `_extract_json` fence/regex fallback ([openrouter_tasks.py:49](service/prefect/tasks/openrouter_tasks.py#L49)) | **Weak** — a truncated array can parse partially |
| `finish_reason == "length"` | **Log only** — warns, does not fail or retry ([openrouter_tasks.py:201](service/prefect/tasks/openrouter_tasks.py#L201)) |
| `topik` non-empty after strip | **Enforced** — idea dropped otherwise ([songbird.py:615](service/prefect/flows/common/songbird.py#L615)) |
| Duplicate `topik` within a bucket | **Enforced** — dropped ([songbird.py:618-621](service/prefect/flows/common/songbird.py#L618-L621)) |
| `bentuk` | **Enforced** — overwritten by the engine, or normalized via `canonical_bentuk` |
| Quota per type | **Enforced** — up to 3 top-up calls; shortfall logged and counted in `ideas_failed` |
| `strategic_application` ∈ {Awareness, Consideration, Conversion} | **Not enforced** — prompt guidance only, no validation |
| `purpose_theme` "1-2 kalimat" | **Not enforced** |
| `caption` structure (hook / body / CTA / hashtags) | **Not enforced** |
| `shoot_guide` / `reference` per-format structure | **Not enforced** |
| `[PLACEHOLDER: …]` for uncertain facts | **Not enforced** — no check that facts were flagged rather than invented |

So: the *shape* is rigidly enforced (nine string keys, non-empty topic, correct type, correct
count). The *content* of eight of those nine fields is unvalidated free text.

Cross-run duplicate detection: **none**. `seen_topics` is per bucket, per run. The same
topic can be generated for the same client every month.

## 5. Monthly content plan ↔ briefs

They are the same artifact. There is no separate "plan" and "brief" stage — one
`run_generation` call produces rows that are simultaneously the plan and the briefs.

The delivered sheet is `CONTENT_PLAN_COLUMNS`
([songbird.py:88-93](service/prefect/flows/common/songbird.py#L88-L93)) — the "DRAFT v5"
layout, exactly 20 columns in order:

```
No., Tanggal, Waktu, Bentuk, Topik, Creator, Format, Purpose/Theme,
Strategic Application, Kebutuhan Personil, Known Facts, Shoot Guide, Reference,
Asset, Caption, Keterangan, Approval, Link Referensi, TicketID, Key
```

Songbird fills only `GENERATED_COLUMNS` (11 of 20):
`No., Tanggal, Bentuk, Topik, Creator, Format, Purpose/Theme, Strategic Application,
Shoot Guide, Reference, Caption`. `Creator` is hard-coded `"Brand"`; `Format` mirrors
`Bentuk`. The other nine — `Waktu`, `Kebutuhan Personil`, `Known Facts`, `Asset`,
`Keterangan`, `Approval`, `Link Referensi`, `TicketID`, `Key` — are left blank for humans
and the Jira flow.

The brief content lives inside the plan cells: `Shoot Guide` carries the scene-by-scene
capture plan, `Reference` the slide-by-slide carousel design or editing reference,
`Caption` the ready-to-post copy.

Handoff: the draft is reviewed, then (manually, or via `--target live`) lands in the live
content-plan worksheet, which
[content_plan_spreadsheet_to_jira_issue.py](service/prefect/flows/content_plan_spreadsheet_to_jira_issue.py)
reads to create Jira issues. **Songbird never calls Jira.**

Delivery is append-only, both paths. Re-running a month appends a second set of rows —
there is no upsert, no run key, no idempotency guard.

## 6. Brand voice specification per client

**No dedicated brand-voice field exists.** Voice is conveyed to the model by four
indirect routes:

1. **Run parameters** `tone`, `audience`, `goal`, `platform`, `content_pillars`
   ([songbird.py:654-656](service/prefect/flows/common/songbird.py#L654-L656)) — free-text,
   default `""`, **not read from any per-client store**. They must be typed at run time,
   and both scheduled deployments leave them empty.
2. **Knowledge-base records** — free-text `subject`/`information` pairs. Only *one* of 21
   clients has anything voice-shaped: `Klinik Mata Boyolali` has a subject
   `brand persona`. The other 20 have operational subjects (`Services Offered`,
   `Operational Hours`, `daftar dokter`, `social media performance`). Verified against the
   live KB: 62 current records across 21 client names.
3. **Harvested exemplars** — caption hooks, `content_flow`, `summary`. This is the *de
   facto* voice specification. When there is no KB the prompt says so explicitly and
   instructs the model to infer "gaya bahasa, positioning, layanan, dan istilah" from
   harvested content.
4. **The system prompt** — a global Indonesian/code-mixing rule identical for every
   client ([songbird.py:441](service/prefect/flows/common/songbird.py#L441)).

Form, therefore: **absent as a structured artifact; present incidentally as one free-text
KB record for one client, and implicitly as exemplar text for all.**

Note also that KB client names and Clients-sheet client names disagree in several cases
(`Lasik Asyik` vs `LASIK Asyik by SMEC Tebet`; `Klinik Utama GASA` vs
`Klinik Utama Gasa`; `Nirwana Coffee Space` / `Nirwana Pamekasan` / `Nirwana Sumenep` as
three separate KB clients). `_is_same_client`'s token-subset rule is what bridges those.

## 7. Single-pass or multi-step?

**Multi-step, and multi-call.** LLM calls per monthly-plan run:

| # | Call | Model | Optional? |
|---|---|---|---|
| 1 | `songbird_theme_induction` | `xiaomi/mimo-v2.5`, `max_tokens` 1500 | yes — failure is non-fatal |
| 2..N | `_generate_bucket`, **one per content type** | `xiaomi/mimo-v2.5` | no |
| +k | top-up calls, up to 3 attempts **per bucket** | same | on shortfall |

For a client configured `Post=4, Story=4, Short Video=4`: 1 theme call + 3 bucket calls,
worst case 1 + 9 = **10 calls**.

Non-LLM steps: burst detection (`detect_trends`), Thompson allocation (`allocate_slots`),
percentile ranking (`rank_performers`) — all pure Python, no model.

There is **no revision, critique, or self-check pass.** Output of the generation call goes
straight to the sheet after the shape checks in §4. Nothing reads the generated brief back.

## 8. Temperature and sampling parameters

Set in `_build_payload` ([openrouter_tasks.py:63-84](service/prefect/tasks/openrouter_tasks.py#L63-L84)):

| Parameter | Value | Notes |
|---|---|---|
| `temperature` | **0.8** | Default of `openrouter_chat` ([openrouter_tasks.py:142](service/prefect/tasks/openrouter_tasks.py#L142)). **No caller ever overrides it** — content generation and theme induction both run at 0.8. |
| `max_tokens` | generation: `min(1500 × missing + 800, 16000)`; theme induction: 1500 | [songbird.py:552,606](service/prefect/flows/common/songbird.py#L552) |
| `reasoning` | `{"enabled": false}` | Guards the empty-content failure |
| `response_format` | strict `json_schema` | |
| `provider` | `{"order": ["xiaomi","digitalocean","novita","parasail"], "allow_fallbacks": true}` | Env-overridable |
| `top_p`, `top_k`, `frequency_penalty`, `presence_penalty`, `seed`, `stop`, `repetition_penalty` | **not set** — provider defaults apply | |

`allocation_seed` seeds only the Python `random.Random` in `allocate_slots`
([songbird_themes.py:110](service/prefect/tasks/songbird_themes.py#L110)). It does not
reach the model, so a "reproducible plan" reproduces the theme allocation, not the text.

Retry/back-off: `MAX_ATTEMPTS = 4` in-task, plus Prefect `retries=2, retry_delay_seconds=30`
on the task itself — up to 12 HTTP attempts for one logical call.

---

# SHARED

## 1. Postgres schema

Databases on the `postgres` container: `noktah_dashboard` (application),
`noktah_dashboard_dev`, `prefect` (Prefect's own state). Application schema is defined in
[config/postgres/init.sql](config/postgres/init.sql); an idempotent re-runnable copy of the
first table is in [config/postgres/migrations/001_knowledge_records.sql](config/postgres/migrations/001_knowledge_records.sql).
There is **no migration for `harvested_items` or `harvested_signals`** — they exist only in
`init.sql`, which runs once at first container init.

Extensions: `pgcrypto`, `pg_trgm`.

### `knowledge_records` — [init.sql:23-65](config/postgres/init.sql#L23-L65)

| Column | Type | Null | Default / constraint |
|---|---|---|---|
| `id` | UUID | no | PK, `gen_random_uuid()` |
| `client_name` | TEXT | no | `btrim(client_name) <> ''` |
| `client_key` | TEXT | no | |
| `subject` | TEXT | no | `btrim(subject) <> ''` |
| `subject_key` | TEXT | no | |
| `information` | TEXT | no | non-empty, `char_length ≤ 4000` |
| `timestamp` | TIMESTAMPTZ | no | `now()` |
| `superseded_by` | UUID | yes | **FK → `knowledge_records(id)` DEFERRABLE INITIALLY DEFERRED**; `<> id` |
| `source_type` | TEXT | no | CHECK ∈ `plain_text`, `google_doc`, `google_sheet` |
| `source_reference` | TEXT | yes | required unless `source_type = 'plain_text'` |
| `created_by` | TEXT | yes | |

Indexes: `uq_current_client_subject` UNIQUE `(client_key, subject_key) WHERE superseded_by IS NULL`;
`ix_client_current (client_key) WHERE superseded_by IS NULL`;
`ix_client_timestamp (client_key, "timestamp" DESC)`;
GIN trigram on `client_key`, `subject`, `information`.

The deferrable FK is load-bearing: supersession inserts the new row's id into the old
row's `superseded_by` *before* the new row exists. See
`.claude/rules/backend/knowledge-base.md`.

### `harvested_items` — [init.sql:70-87](config/postgres/init.sql#L70-L87)

| Column | Type | Null | Default / constraint |
|---|---|---|---|
| `id` | BIGSERIAL | no | PK |
| `platform` | TEXT | no | |
| `profile_key` | TEXT | no | |
| `content_id` | TEXT | no | |
| `drive_target` | TEXT | no | Drive folder id, **part of the dedup key** |
| `content_type` | TEXT | no | |
| `collected_at` | TIMESTAMPTZ | no | `now()` |
| `drive_file_id` | TEXT | yes | comma-joined list |
| `analysis_status` | TEXT | no | `'pending'`, CHECK ∈ `pending`, `success`, `failed` |

Indexes: `uq_harvested_item` UNIQUE `(platform, content_id, drive_target)`;
`ix_harvested_target_profile (drive_target, profile_key)`.

### `harvested_signals` — [init.sql:95-126](config/postgres/init.sql#L95-L126)

| Column | Type | Null | Default |
|---|---|---|---|
| `id` | BIGSERIAL | no | PK |
| `platform` | TEXT | no | |
| `profile_key` | TEXT | no | |
| `content_id` | TEXT | no | |
| `content_type` | TEXT | no | |
| `published_at` | TIMESTAMPTZ | yes | |
| `caption` | TEXT | yes | |
| `hashtags` | TEXT | yes | space-joined string, not an array |
| `views` | BIGINT | yes | |
| `likes` | BIGINT | yes | |
| `comments` | BIGINT | yes | |
| `subtitle` | TEXT | yes | |
| `content_flow` | TEXT | yes | |
| `summary` | TEXT | yes | |
| `advertisement` | BOOLEAN | no | `false` — reviewer-assigned |
| `harvested_at` | TIMESTAMPTZ | no | `now()`, bumped on upsert |

Indexes: `uq_harvested_signal` UNIQUE `(platform, content_id)`;
`ix_signal_profile_engagement (profile_key, (COALESCE(likes,0)+COALESCE(comments,0)) DESC)`;
`ix_signal_profile_trgm` GIN trigram on `profile_key`.

**Foreign keys across the application schema: exactly one**, `knowledge_records.superseded_by`
→ itself. `harvested_items`, `harvested_signals`, and `knowledge_records` are three
mutually unlinked islands. There is no `clients` table, no `accounts` table, no `runs`
table, no `briefs` table.

`ix_signal_profile_engagement` is a legacy of the flat `likes+comments` ranking that
`songbird_ranking.py` replaced; the current query filters on `lower(profile_key)` and
`published_at` and cannot use it.

## 2. Client vs competitor accounts — distinguished?

**Not in the database. Only in a Google Sheet, and only for 2 of 21 clients.**

`harvested_signals` and `harvested_items` have no `is_client` / `is_competitor` / `role`
column. `profile_key` is a bare handle with no ownership semantics.

The distinction exists at two configuration points, resolved at run time:

- **Own** — derived from the Clients worksheet's `Instagram`/`TikTok` URL columns,
  normalized to a bare handle ([songbird_tasks.py:307](service/prefect/tasks/songbird_tasks.py#L307)).
- **Competitor** — the `CLIENT_SOCIAL` block of the "Hashmaps" worksheet, cols R/S/T, one
  row per competitor ([hashmap.py:88-92](service/prefect/hashmap.py#L88-L92)). The `own`
  key in that block is always `[]`.

`_resolve_handles` ([songbird.py:209](service/prefect/flows/common/songbird.py#L209))
merges both with per-run overrides. The two sets are then ranked in **separate**
`songbird_top_performers` calls with separate budgets (40% own / 60% competitor) and
labelled `milik klien` / `kompetitor` in the prompt — so the distinction is real inside a
run and vanishes the moment the run ends.

Live state:

| | Count |
|---|---|
| Clients in the Clients sheet (COMPONENTS block) | 21 |
| Clients with configured competitors | **2** (LASIK Asyik by SMEC Tebet → 3; Ecky Dental Center → 1) |
| Distinct accounts in `harvested_signals` | 18 — **all client-owned** |
| Competitor accounts harvested | **0** |
| `harvest-3mo-*` deployments pointing at a competitor | **0** |

So the competitor pathway is fully implemented and currently carries no data.

## 3. Performance/metrics table — does it exist, does it have rows?

**`harvested_signals` is the only metrics-bearing table, and it holds one frozen snapshot
per post rather than a time series.**

| | |
|---|---|
| Rows | **656** |
| Distinct accounts | 18 |
| Rows with `views` | 293 (44.7%) — video only |
| Rows with `comments` | 293 (44.7%) — video only |
| Rows with `likes` | 654 (99.7%) — the 2 gaps are Instagram stories |
| Rows flagged `advertisement` | **1** |
| First / last `harvested_at` | 2026-07-15 / 2026-07-28 |

Per platform and type:

| Platform | Type | Rows | with views | with comments | with likes |
|---|---|---|---|---|---|
| instagram | carousel | 276 | 0 | 0 | 276 |
| instagram | image | 85 | 0 | 0 | 85 |
| instagram | video | 269 | 269 | 269 | 269 |
| instagram | story | 2 | 0 | 0 | 0 |
| tiktok | video | 24 | 24 | 24 | 24 |

There is **no** table for: post-level metric history, account-level follower/reach
history, campaign results, brief performance, generated-content outcomes, or model spend.

## 4. Published post → originating brief

**No link exists. The chain is broken in three places.**

The path a piece of content takes:

```
songbird run
  → Google Sheet (draft, or appended into the live content plan)     [no run id, no idea id]
  → human review / production                                        [no identifier carried]
  → content_plan_…_jira flow → Jira issue                            [issue key not written back to the sheet]
  → published to Instagram/TikTok                                    [no marker]
  → harvest picks it up → harvested_signals (content_id = platform post id)
```

Specifics:

- Songbird writes nothing to Postgres. There is no `briefs` table and no idea identifier
  of any kind — not a UUID, not a run name, not a row hash.
- The draft sheet's `TicketID` and `Key` columns are **deliberately left blank**
  ([songbird.py:97-100](service/prefect/flows/common/songbird.py#L97-L100)).
- The Jira flow creates issues but **does not write the issue key back to the sheet** —
  grep across [content_plan_spreadsheet_to_jira_issue.py](service/prefect/flows/content_plan_spreadsheet_to_jira_issue.py)
  and [jira_tasks.py](service/prefect/tasks/jira_tasks.py) finds no sheet update path for
  `TicketID`/`Key`.
- `harvested_signals.content_id` is the platform's post id, which does not exist until
  after publication and is never associated with any upstream row.
- The only join key that could exist — `Topik` text vs `caption` text — is not used
  anywhere, and songbird's captions are edited before posting by design.

The docstring at [songbird_tasks.py:319-320](service/prefect/tasks/songbird_tasks.py#L319-L320)
describes this as "what closes songbird's learning loop: generated content is published,
the next harvest captures it as own content, and it scores in the next ranking." That is
accurate as an *aggregate* effect — generated content does re-enter the exemplar pool as
own content — but it is **statistical, not attributable**. No individual brief can be
traced to its published post or its performance.

## 5. LLM spend, last full month

**No spend was measured, because nothing measures it.**

### Instrumentation: ABSENT

A repository-wide grep for `cost`, `spend`, `usage`, `prompt_tokens`, `completion_tokens`,
`generation_id`, `X-Title`, `HTTP-Referer` across `service/prefect` and `service/roach`
(excluding `.venv`) returns **zero matches**. Neither call site reads the `usage` object
that OpenRouter returns in every response
([analyze.py:170](service/roach/analyze.py#L170) and
[openrouter_tasks.py:197-206](service/prefect/tasks/openrouter_tasks.py#L197-L206) both
read only `choices[0]`). No `X-Title`/`HTTP-Referer` headers are sent, so OpenRouter's own
dashboard cannot attribute spend per app either.

### Last full month = June 2026: **zero activity**

The earliest flow run in the Prefect database is 2026-07-02. No harvest, no analysis, no
generation ran in June 2026. Actual spend for the last full month is therefore **zero**,
though this is a fact about the system's age, not about instrumentation.

### What is countable, for the month in progress (July 2026)

| Call site | File | Model | Observed calls | Notes |
|---|---|---|---|---|
| Video analysis | [analyze.py:235](service/roach/analyze.py#L235) `analyze_video` | `xiaomi/mimo-v2.5` | — | ~293 of the 701 `social.item.analyze` task runs went down this branch (video rows) |
| Image/carousel analysis | [analyze.py:252](service/roach/analyze.py#L252) `analyze_images` | `google/gemini-2.5-flash-lite` | — | ~363 rows (carousel + image + story) |
| **roach total** | | | **701 task runs** (700 COMPLETED, 1 FAILED) | Excludes in-task retries: up to 4 HTTP attempts each |
| Songbird generation | [songbird.py:603](service/prefect/flows/common/songbird.py#L603) `_generate_bucket` | `xiaomi/mimo-v2.5` | — | 1 per content type + up to 3 top-ups each |
| Theme induction | [songbird_themes.py:226](service/prefect/tasks/songbird_themes.py#L226) | `xiaomi/mimo-v2.5` | — | 1 per run, best-effort |
| **songbird total** | | | **100 task runs** (90 COMPLETED, 10 FAILED) | All 2026-07-27 → 2026-07-28 |

Task-run counts are a floor on API calls, not a measure of them: each
`openrouter.chat.complete` task run can make up to 4 in-task HTTP attempts and is itself
Prefect-retried twice. Token volume is not recorded anywhere, so cost cannot be derived
from these counts — the video payloads (base64 MP4, 8000 output tokens) and the songbird
prompts (up to 16000 output tokens) differ by orders of magnitude in cost per call.

**Conclusion: LLM spend is not measurable from this system.** The only source of truth is
the OpenRouter account dashboard, which cannot break spend down by service or call site
because no attribution headers are sent.

## 6. Test coverage and CI status

### CI: ABSENT

No `.github/` directory. No `.gitlab-ci.yml`, `Jenkinsfile`, `.circleci`, or any other CI
configuration in the repository. Tests are run manually.

### Test inventory and measured status

| Suite | Files | Tests | Result (run 2026-07-31) |
|---|---|---|---|
| `service/prefect/tests/` | 9 | 136 defs / 182 collected | **182 passed**, 25 warnings, 114 s |
| `service/roach/tests/` | 3 | 43 | **39 passed, 4 FAILED**, 1.4 s |
| `service/knowledge-base/tests/` | 3 | 34 | **not run** — no `.venv`; requires a live disposable Postgres |

Per-file test counts:

| File | Tests |
|---|---|
| [service/prefect/tests/test_songbird.py](service/prefect/tests/test_songbird.py) | 30 |
| [service/prefect/tests/test_songbird_ranking.py](service/prefect/tests/test_songbird_ranking.py) | 28 |
| [service/prefect/tests/test_social_harvest.py](service/prefect/tests/test_social_harvest.py) | 19 |
| [service/prefect/tests/test_hashmap.py](service/prefect/tests/test_hashmap.py) | 15 |
| [service/prefect/tests/test_songbird_themes.py](service/prefect/tests/test_songbird_themes.py) | 12 |
| [service/prefect/tests/test_songbird_trends.py](service/prefect/tests/test_songbird_trends.py) | 12 |
| [service/prefect/tests/test_songbird_batch.py](service/prefect/tests/test_songbird_batch.py) | 11 |
| [service/prefect/tests/test_openrouter_tasks.py](service/prefect/tests/test_openrouter_tasks.py) | 7 |
| [service/prefect/tests/test_social_tasks.py](service/prefect/tests/test_social_tasks.py) | 2 |
| [service/roach/tests/test_collect.py](service/roach/tests/test_collect.py) | 21 |
| [service/roach/tests/test_analyze.py](service/roach/tests/test_analyze.py) | 11 |
| [service/roach/tests/test_api.py](service/roach/tests/test_api.py) | 11 |
| [service/knowledge-base/tests/test_repository.py](service/knowledge-base/tests/test_repository.py) | 18 |
| [service/knowledge-base/tests/test_tools.py](service/knowledge-base/tests/test_tools.py) | 9 |
| [service/knowledge-base/tests/test_ingestion.py](service/knowledge-base/tests/test_ingestion.py) | 7 |

### The 4 roach failures

```
FAILED tests/test_api.py::test_list_success_shape_and_no_owner_only_analytics
FAILED tests/test_api.py::test_list_empty_items_for_zero_content_profile
FAILED tests/test_api.py::test_list_profile_not_found          - assert 500 == 404
FAILED tests/test_api.py::test_list_rate_limited               - assert 500 == 429
```

Cause: the test doubles declare a three-argument signature
(`def fake_list_profile(profile_url, platform, max_items=30)`,
[test_api.py:38,63](service/roach/tests/test_api.py#L38)) but
[api.py:64](service/roach/api.py#L64) calls `collect.list_profile` with four positional
arguments since `stories_only` was added. The resulting `TypeError` is swallowed by the
endpoint's generic `except Exception` and returned as HTTP 500. The tests were not updated
when the parameter was introduced; production code is unaffected.

### Coverage character

- **Well covered:** ranking arithmetic (28 tests over percentiles, quotas, per-account
  caps, tie handling), trend detection, theme allocation, songbird engine behaviour,
  hashmap parsing, the harvest engine's dedup/backoff/delivery logic, the KB supersession
  race (against a real Postgres, per `.claude/rules/backend/knowledge-base.md`).
- **Not covered:** the Postgres DDL itself (no schema test); prompt content (no golden
  files — the verbatim prompts in §7/§2 above are unasserted by any test); Google Sheets
  and Drive delivery (mocked); real platform behaviour (the roach rules file states IG
  changes must be validated with a live `/list` probe, which is manual); output quality of
  either model; end-to-end draft→live→Jira.
- **No coverage measurement.** No `pytest-cov`, no coverage config, no threshold.

---

# Capability status

For each item: **PRESENT** / **PARTIAL** / **ABSENT**, with references. Descriptive only.

### Longitudinal re-scraping — **ABSENT**

A post's metrics are captured once and never revisited. The dedup gate at
[social_harvest.py:414-418](service/prefect/flows/common/social_harvest.py#L414-L418)
`continue`s past any item with `analysis_status = 'success'` before download, analysis, or
signal write. `harvested_signals` is UNIQUE on `(platform, content_id)` with a single row
per post — the schema has no slot for a second observation, and no `metric_history` table
exists. The `ON CONFLICT … DO UPDATE` at
[social_tasks.py:260](service/prefect/tasks/social_tasks.py#L260) is only reachable via
the failure-retry purge path
([social_harvest.py:419-433](service/prefect/flows/common/social_harvest.py#L419-L433)).
The daily `social-harvest-sync` flow rewrites only the `advertisement` boolean
([social_harvest_sync.py:114](service/prefect/flows/social_harvest_sync.py#L114)).
Measured: 656 rows, one observation each; the monthly 90-day re-harvests skip every
already-successful post.

### Velocity computation — **ABSENT**

Nothing computes a rate of change in engagement, reach, or followers. Impossible by
construction — velocity needs ≥2 observations per post, and there is only ever 1 (see
above). `songbird_ranking.py` uses `age_days` **only** as a decay multiplier
([`_recency_multiplier`, songbird_ranking.py:159](service/prefect/tasks/songbird_ranking.py#L159)),
not as a denominator. `songbird_trends.detect_trends`
([songbird_trends.py:103](service/prefect/tasks/songbird_trends.py#L103)) does compute a
rate — but of *token occurrence across posts* (21-day window vs 69-day baseline), which is
topic frequency, not engagement velocity. No follower/reach series exists to differentiate
(`public_metadata` is `{}` for Instagram, [collect.py:811](service/roach/collect.py#L811)).

### Competitor cohort management — **PARTIAL**

Mechanism exists, data does not. Cohorts are declared in the `CLIENT_SOCIAL` block of the
"Hashmaps" worksheet, one row per competitor grouped by client, parsed by
[hashmap.py:88-92](service/prefect/hashmap.py#L88-L92) into
`{client → {own[], competitors[], competitor_profiles[]}}`, merged with run overrides by
[`_resolve_handles`, songbird.py:209](service/prefect/flows/common/songbird.py#L209), and
ranked in a separate budgeted call from own content
([songbird.py:774-785](service/prefect/flows/common/songbird.py#L774-L785)). But: only
**2 of 21** clients have any competitor configured; the 4 configured handles
(`lasikindonesia`, `kmneyecare`, `jeceyehospital`, `sebayadental`) appear in **zero**
`harvest-3mo-*` deployments; and `harvested_signals` contains **0** competitor rows out of
656. There is no DB representation of a cohort, no per-cohort membership history, and no
validation that a configured competitor is actually being harvested.

### Controlled vocabulary tagging — **PARTIAL**

One controlled vocabulary exists, for content **format** only:
`DEFAULT_VOCABULARY = ["Post", "Story", "Short Video"]` with a synonym map
`_BENTUK_SYNONYMS` (24 entries) resolved by `canonical_bentuk`
([songbird_ranking.py:75-108](service/prefect/tasks/songbird_ranking.py#L75-L108)). It is
enforced in both directions — harvested `content_type` → bucket, and model-written
`bentuk` → canonical type, with unmappable values dropped and counted. `strategic_application`
names a three-value vocabulary in the prompt but is **never validated**
([songbird.py:504](service/prefect/flows/common/songbird.py#L504)). Topics and themes are
uncontrolled: themes are free-text Indonesian phrases invented per run by an LLM
([songbird_themes.py:199](service/prefect/tasks/songbird_themes.py#L199)) and are **not
persisted**, so the same content can be assigned different theme names each month.
Hashtags are stored as a raw space-joined string. No tag table, no taxonomy, no topic ids.

### Performance baselines — **PARTIAL**

Baselines exist as a computation, never as an artifact. `score_rows`
([songbird_ranking.py:186](service/prefect/tasks/songbird_ranking.py#L186)) builds a
within-`(profile_key, bucket)` percentile baseline via `_shrunk_percentiles`
(Weibull position + midranks + shrinkage toward 0.5, `SHRINK_K = 3`), plus a
within-bucket global anchor at `GLOBAL_BLEND = 0.30`, plus a rate baseline
(`RATE_NEUTRAL = 0.5` median-relative dampener). These are exactly "how does this compare
to normal for this account and format". But every one is recomputed in memory from the
current 180-day window on each run and then discarded — no baseline table, no stored
account median/percentile, no snapshot. Two consequences: baselines drift silently as the
window slides, and nothing can answer "was this month above baseline" after the fact.
`ix_signal_profile_engagement` ([init.sql:121-122](config/postgres/init.sql#L121-L122))
is a vestige of the earlier flat-sum baseline.

### Effect estimation — **ABSENT**

Nothing estimates the effect of anything. There is no experiment assignment, no control
group, no holdout, no pre/post comparison, no lift calculation, no significance test, and
no counterfactual. Songbird's own output is never measured: the pipeline terminates at
sheet delivery ([songbird.py:894-910](service/prefect/flows/common/songbird.py#L894-L910))
and no flow reads a delivered draft back. The `HIT_DISCLAIMER`
([songbird.py:83](service/prefect/flows/common/songbird.py#L83)) — *"Performa audiens
adalah bias dari pola historis dan tidak dijamin"* — is carried in every `summary` and is
an accurate statement of this limitation.

### Hypothesis tracking — **ABSENT**

`allocate_slots` ([songbird_themes.py:83](service/prefect/tasks/songbird_themes.py#L83))
labels every slot `exploit` or `explore` and the prompt instructs the model accordingly
([`_allocation_block`, songbird.py:404](service/prefect/flows/common/songbird.py#L404)) —
so each slot carries something hypothesis-shaped. But the label is written only into
`summary.allocation` (an aggregate count, [songbird.py:389](service/prefect/flows/common/songbird.py#L389))
and the local JSON file. It is not stored per idea, not written to the sheet, never read
back, and never resolved. The Thompson posteriors
([`_posterior`, songbird_themes.py:67](service/prefect/tasks/songbird_themes.py#L67)) are
rebuilt from scratch each run out of the current exemplars' ranking percentiles — so an
"explore" bet made in July is not remembered, let alone evaluated, in August. The bandit
has no memory between pulls.

### Negative-example retrieval — **ABSENT**

Only top performers are ever retrieved. Two filters remove weak content before the model
ever sees it: `engagement < MIN_ENGAGEMENT (3)` drops noise rows
([songbird_ranking.py:213](service/prefect/tasks/songbird_ranking.py#L213)), and
`select_exemplars` ([songbird_ranking.py:313](service/prefect/tasks/songbird_ranking.py#L313))
takes only the highest-scored rows per bucket up to quota. The prompt frames every
exemplar positively — *"Konten yang terbukti perform (pelajari polanya, jangan salin)"*
([songbird.py:497](service/prefect/flows/common/songbird.py#L497)) — with no
counter-example section, no "avoid this" block, and no bottom-percentile sampling. The
only negative signal in the whole system is the `advertisement` exclusion, which removes
posts rather than presenting them as examples to avoid.

### Cost tracking — **ABSENT**

No cost, token, or usage instrumentation anywhere. A repo-wide grep for `cost`, `spend`,
`usage`, `prompt_tokens`, `completion_tokens`, `generation_id`, `X-Title`, `HTTP-Referer`
over `service/prefect` and `service/roach` returns zero matches. Both OpenRouter clients
read only `choices[0]` from the response and discard the `usage` object
([analyze.py:170](service/roach/analyze.py#L170),
[openrouter_tasks.py:197](service/prefect/tasks/openrouter_tasks.py#L197)). No attribution
headers are sent, so per-service breakdown is unavailable even from OpenRouter's own
dashboard. No budget, quota, alert, or per-run cost estimate exists. The nearest thing to
a cost control is the comment at
[songbird.py:549-552](service/prefect/flows/common/songbird.py#L549-L552) noting that
`TOKENS_PER_IDEA = 1500` is an upper bound rather than a floor since only generated tokens
are billed — a reasoning about cost, not a measurement of it.

---

## Appendix — how the live figures were obtained

```bash
docker exec postgres psql -U noktah -d noktah_dashboard -c "\dt"
docker exec postgres psql -U noktah -d noktah_dashboard \
  -c "SELECT analysis_status, count(*) FROM harvested_items GROUP BY 1;" \
  -c "SELECT platform, content_type, count(*), count(*) FILTER (WHERE views IS NOT NULL) FROM harvested_signals GROUP BY 1,2;"
docker exec postgres psql -U noktah -d prefect \
  -c "SELECT f.name, fr.state_type, count(*) FROM flow_run fr JOIN flow f ON f.id=fr.flow_id WHERE fr.start_time > now() - interval '30 days' GROUP BY 1,2;"
docker exec prefect python hashmap.py --block CLIENT_SOCIAL
docker exec roach printenv | grep OPENROUTER

cd service/prefect && ./.venv/Scripts/python.exe -m pytest tests/ -q
cd service/roach   && ./.venv/Scripts/python.exe -m pytest tests/ -q
```
