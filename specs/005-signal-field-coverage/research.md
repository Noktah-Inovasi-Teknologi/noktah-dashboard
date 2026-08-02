# Phase 0 Research: Signal Field Coverage

**Feature**: `005-signal-field-coverage` | **Date**: 2026-08-02

All findings below were obtained **offline** — from the installed source of the collection
libraries already in use, from roach's own source, and from the live datastore — per FR-012a.
**Zero live platform requests were issued during this research.** The one place where offline
evidence cannot settle the question (R1) ends in a single capped probe, budgeted under FR-012b.

---

## R0. Measured baseline coverage (supersedes the audit's figures)

**Decision**: Use the live datastore as the coverage baseline, not AUDIT.md §3.

Measured 2026-08-02 against `noktah_dashboard`:

| platform | content_type | rows | views | likes | comments |
|---|---|---:|---:|---:|---:|
| instagram | carousel | 291 | 0 | 291 | 0 |
| instagram | image | 86 | 0 | 86 | 0 |
| instagram | story | 2 | 0 | **0** | 0 |
| instagram | video | 291 | 291 | 291 | 291 |
| tiktok | video | 26 | 26 | 26 | 26 |

**Rationale**: The corpus has grown since the audit (696 rows, not 656; 377 Instagram non-video
rows, not 361). Three facts change the plan and none are in the brief:

1. **Instagram video is at 100% comment coverage** (291/291), not partial. The clips enrichment
   path is fully reliable, which makes it a sound template to copy rather than a flaky one.
2. **Instagram stories expose no counts at all** — not even likes. This is a fourth availability
   case the brief did not mention, and it is by construction: `_list_instagram_stories`
   ([collect.py:838](../../service/roach/collect.py#L838)) hardcodes all four counts to `None`.
   Stories must appear in the availability matrix as a platform limit across every field, or the
   two story rows will read as a collection failure forever.
3. **TikTok has only video rows.** The carousel/image/story TikTok paths exist in code but have
   never produced a row. Any claim about their field availability is therefore **unverified by
   observation** and must be recorded as `undetermined`, not assumed from the code path.

**Alternatives considered**: Taking the audit's numbers at face value — rejected, they are three
weeks stale and would have hidden the story case entirely.

---

## R1. Is there a comment-count path for Instagram feed posts and carousels?

**Decision**: **CONFIRMED AND SHIPPED.** `/api/v1/feed/user/<user_id>/` returns `comment_count`
for carousel and image posts. This was never a platform limit — it is a mapping gap in gallery-dl.

**Probe result (2026-08-02, `natgeo`, non-client, 4 of a 10-request budget, no throttle):**

| type | items | with `comment_count` | with `play_count` |
|---|---:|---:|---:|
| carousel | 5 | **5** | 0 |
| image | 5 | **5** | 0 |
| video | 2 | **2** | 2 |

**Live verification after shipping** (`lasikasyik`, `max_items=20`): carousel **5/5**, image
**1/1**, video **18/18** now carry comment counts — against **0 of 377** before.

**Outcome B, not A.** The feed also returns `play_count` for video, which suggests it could subsume
the clips pass. It was **not** dropped: the feed is the *posts grid*, and a Reel that never appears
there would silently lose its view count. Risking view data on ~291 video rows to save one request
is a bad trade, and acting on the unverified half of a single probe is what Constitution VIII
forbids. Both passes run; marginal cost **+1 to +2 requests per account per run**.

The original reasoning, which the probe confirmed unchanged:

**Evidence chain (all offline):**

1. roach lists Instagram via `gallery-dl` against `/<user>/posts/`
   ([collect.py:283-296](../../service/roach/collect.py#L283-L296)).
2. gallery-dl's Instagram REST API resolves that to **`/v1/feed/user/{user_id}/` with
   `count: 30`** (`extractor/instagram.py:1151-1154`, gallery-dl 1.32.6 as installed in the roach
   image).
3. gallery-dl's post parser maps **`like_count` → `likes` and nothing else count-shaped**:
   `_parse_post_rest` reads `post.get("like_count", 0)` (`instagram.py:240`) and never reads
   `comment_count`. The GraphQL parser likewise takes only
   `post["edge_media_preview_like"]["count"]` (`instagram.py:423`).
4. Instagram's private-API **media object carries `comment_count`** — proven inside this codebase:
   `_instagram_clip_stats` already reads `m.get("comment_count")` off media objects returned by
   `/v1/clips/user/` ([collect.py:767](../../service/roach/collect.py#L767)), and those items are
   the same media shape the feed endpoint returns.

**Therefore the comment count is almost certainly present in a payload roach already causes to be
fetched, and is discarded by gallery-dl before roach can see it.** This is not a platform limit. It
is a mapping gap in a third-party library.

**Why the fix is still a request, not free**: gallery-dl `-j` emits only its own mapped dict and
offers no raw-passthrough option, so the discarded field cannot be recovered from the existing
subprocess. The path must be re-issued directly — exactly as `_instagram_clip_stats` already does.

**FR-010 compliance**: the endpoint is on an **authenticated surface already in use** (same IG
private web API, same burner cookie session, same curl_cffi impersonation), and it is
**per-account paged**, not per-post. It is the same shape the spec explicitly blesses.

**The upside worth probing for**: IG media objects carry `play_count`/`ig_play_count` and
`comment_count` on the **same** object. If `/v1/feed/user/` returns those for video as well as
carousel/image, it **subsumes `_instagram_clip_stats` entirely** — one pass covering all content
types instead of two passes covering one. That would make this change **net request-negative**.

**The single probe (FR-012b), scoped before it is run:**

- Target: one designated non-client account. Budget: ≤10 requests; **expected 1**.
- Question answered: does one page of `/api/v1/feed/user/<id>/` return items carrying
  `comment_count` for carousel and image posts, and does it also carry `play_count` for video?
- Stop condition: any 429/challenge halts it immediately (FR-012c), yielding `inconclusive`.
- Outcome A (comment_count present, play_count present) → replace the clips pass; marginal
  requests **≤ 0**.
- Outcome B (comment_count present, play_count absent) → add a feed pass alongside clips; marginal
  requests **+1 to +2 per account per run**.
- Outcome C (comment_count absent) → record `unavailable_platform_limit` with evidence, write **no
  collection code** (FR-011).

**Alternatives considered**:
- *Patch/fork gallery-dl's parser to keep `comment_count`* — rejected: forks a fast-moving upstream
  dependency and creates a permanent maintenance burden for one field.
- *Per-post `/v1/media/<id>/info/`* — rejected outright: scales per post (377 requests over the
  current corpus), which FR-010 disqualifies by name.
- *GraphQL `edge_media_to_comment.count`* — viable in principle but requires a doc_id, and the
  2026-07-31 profile-endpoint breakage is a live demonstration of how brittle persisted-query IDs
  are here. Held as fallback only.

---

## R2. Share counts

**Decision**: No collection work. **Storage and delivery work only.**

**Rationale**: roach already parses TikTok share counts on both paths — `stats.shareCount` in
`_tiktok_counts` ([collect.py:545](../../service/roach/collect.py#L545)) and yt-dlp's
`repost_count` ([collect.py:450](../../service/roach/collect.py#L450)) — and already emits an
explicit `shares: None` for Instagram with a comment stating it is unavailable there
([collect.py:949](../../service/roach/collect.py#L949)). Roach's tests already assert the field
(`tests/test_collect.py:53-58`, `tests/test_api.py:60`).

The loss is downstream, at three points, none of which touch the network:

| Boundary | State |
|---|---|
| `harvested_signals` | no `shares` column |
| `social.signal.record` | no `shares` parameter ([social_tasks.py:240-256](../../service/prefect/tasks/social_tasks.py#L240-L256)) |
| `ACCOUNT_HEADER` | no `shares` column ([social_harvest.py:99-104](../../service/prefect/flows/common/social_harvest.py#L99-L104)) |

**Marginal collection volume: exactly zero.** The value is already in memory in the harvest process.

**Instagram**: `unavailable_platform_limit` for every content type. Instagram publishes no share
count on any public surface; the existing code comment already records this determination and this
feature promotes it from a source comment to queryable data.

**Caveat carried forward**: only TikTok *video* rows exist today (R0). The photo-post and story
share paths are code-complete but unobserved, so their availability is `undetermined` until a row
proves otherwise — recording them as `available` on the strength of the code path alone would be
exactly the unverified claim Constitution VIII forbids.

---

## R3. Instagram follower count

**Decision**: **No new capture mechanism.** Verify the existing one and close the miss-recording
gap.

**Rationale**: The brief's premise (`collect.py:808-812` returns `{}`) predates the 2026-08-01
work. Current state:

- `_instagram_profile_info` queries `web_profile_info` ([collect.py:598](../../service/roach/collect.py#L598)).
- `_instagram_profile_info_graphql` is the fallback for the ongoing per-account `400`
  ([collect.py:671](../../service/roach/collect.py#L671)), measured working on `lasikasyik`.
- `public_metadata` is populated at [collect.py:987-993](../../service/roach/collect.py#L987-L993).
- The **time series already exists**: `account_follower_observations` is append-only with
  `observed_at` and a `run_id` (`init.sql:149-158`), written by `social.account.record-followers`
  ([social_tasks.py:442](../../service/prefect/tasks/social_tasks.py#L442)), which returns `False`
  rather than writing when the count is `None`.
- The harvest engine already collects a `follower_capture_missed` list
  ([social_harvest.py:447-456](../../service/prefect/flows/common/social_harvest.py#L447-L456)).

**The remaining gap**: `follower_capture_missed` lives only in the run summary. It is not a
queryable per-account record, so "this account has no observation for August" cannot be
distinguished from "this account was never attempted" by query — which is precisely FR-018.

**Work**: route the miss into the same capture-outcome record as everything else (R4), with a
classified reason. Marginal collection volume: **zero**.

**A conflict that is not a conflict — resolve it in code review, not at runtime.** The
constitution's normative metric table says *"follower count at post time | Derived | Interpolate
between collection points; mark interpolated"*, while FR-020 forbids interpolating a follower
count. These govern **different fields** and must not be collapsed:

| Field | Rule |
|---|---|
| `account_follower_observations.follower_count` | An **observation**. Never interpolated, never zero-filled, never carried forward (FR-020, Constitution VII). |
| follower-count-at-post-time | A **derivation** computed by a later consumer. May interpolate, and MUST be marked interpolated (Constitution VI). Out of scope here. |

This feature writes only the first. Any future consumer computing the second must not write its
result back into the observation table.

---

## R4. Where capture outcomes live

**Decision**: A dedicated append-only `capture_outcomes` table keyed to (platform, content_id) and
capture kind — as clarified — plus reuse of the existing `runs.id` for run attribution.

**Rationale**: Constitution VII forbids updating an observation in place and requires missed
observations be recorded as classified missed attempts. `harvested_signals` is upserted
`ON CONFLICT (platform, content_id) DO UPDATE`, so it structurally cannot hold attempt history. A
side table also keeps `harvested_signals` from gaining two columns per new field, and matches the
`account_follower_observations` precedent already in the schema.

**Granularity**: one row per (observation, capture kind, attempt). Capture kinds are the
supplementary passes that actually exist: `instagram_clip_stats`, `instagram_feed_stats` (if R1
lands), `instagram_profile_info`, `tiktok_stats`. The primary listing is not a supplementary
capture and is not recorded here — its failure is already a harvest-level failure.

**FR-003b** — "succeeded overall but returned nothing for this observation" — maps to a distinct
outcome value (`no_match`) from an outright failure. This is a real case today: the clips pass
returns a shortcode-keyed dict and non-Reel posts simply are not in it
([collect.py:1000-1014](../../service/roach/collect.py#L1000-L1014)).

**Alternatives considered**: JSONB on the signal row (mutated in place, violates VII); per-field
status columns (widens the table per field, fights XII's additive-change principle); run-level only
(cannot express `no_match`).

---

## R5. The per-account request baseline

**Decision**: **Measure by instrumentation on an already-scheduled run. Do not probe to measure.**

**Rationale**: FR-024 requires marginal volume as a percentage of baseline, and the baseline is
currently unmeasured. Running a harvest *to measure it* would spend real requests on bookkeeping.
Counting requests inside a run that was going to happen anyway costs **zero** additional requests
and yields a better number than any estimate.

**Derived estimate, pending that measurement** (Instagram, `max_items=30`), from the code paths:

| Pass | Requests | Source |
|---|---|---|
| gallery-dl posts listing (user_id lookup + 1 page @ count=30) | ~2 | `instagram.py:1151`, `:1119` |
| gallery-dl reels listing (separate process ⇒ repeats user_id lookup) | ~2 | [collect.py:780](../../service/roach/collect.py#L780) |
| `web_profile_info` | 1 | [collect.py:627](../../service/roach/collect.py#L627) |
| GraphQL follower fallback (currently always, due to the 400) | 0–1 | [collect.py:696](../../service/roach/collect.py#L696) |
| `_instagram_clip_stats` (≤4 pages @ 24) | 1–4 | [collect.py:749](../../service/roach/collect.py#L749) |
| **Total** | **~7–10** | |

Against that baseline, R1 Outcome B (+1–2) is **+10% to +29%**; Outcome A is **0% or negative**.

TikTok is deliberately **not** estimated here: `_gallery_dl_cmd` notes the TikTok extractor is
"one-request-per-post" ([collect.py:275](../../service/roach/collect.py#L275)), which puts its
baseline in a different order of magnitude and makes a code-derived guess untrustworthy. It is
measured by the same instrumentation. No TikTok change in this feature adds requests, so nothing
depends on the number.

**Alternatives considered**: A dedicated measurement run — rejected, spends requests to count
requests. Estimating from logs — rejected, roach does not currently log per-pass request counts,
which is the gap the instrumentation closes.

### MEASURED 2026-08-02 — Instagram, `max_items=20`, account `lasikasyik`

```
gallery_dl_invocations: 2      (posts listing + Reels tab)
yt_dlp_invocations:     0      (Instagram does not use yt-dlp)
direct_api_requests:    6      EXACT — web_profile_info 1, graphql fallback 1,
                               clips ≤2, feed ≤2
```

Against the ~7–10 code-derived estimate below, the real shape is **2 subprocess
invocations + 6 exact HTTP requests**. The estimate conflated the two classes;
the measurement does not, which is why it is reported as two numbers.

**Marginal cost of the feed pass added by R1: +1 to +2 direct API requests per
account per run** — the predicted Outcome B figure, confirmed. Everything else in
this feature is +0.

TikTok remains unmeasured (no TikTok harvest has run since the rebuild); no
change in this feature affects it.

### Instrumentation notes

`/list` now returns `request_stats`, and the harvest engine records it per profile
(`summary.request_stats`). **The number itself is still outstanding**: it appears on the next
already-scheduled harvest, which has not run since the rebuild.

The instrumentation deliberately reports **three separate counts, not one total**:

| Field | What it is |
|---|---|
| `gallery_dl_invocations` | process invocations — each pages internally |
| `yt_dlp_invocations` | process invocations — same |
| `direct_api_requests` | **exact** HTTP request count (our own curl_cffi calls) |

Summing them would present an estimate and an observation in the same field, which constitution VI
forbids. The true request count inside a gallery-dl subprocess is not observable from outside it,
so it is not claimed. Until the next harvest reports real numbers, every "% of baseline" figure in
this feature rests on the ~7–10 code-derived **estimate** above and must be labelled as such.

---

## R6. Comment text feasibility

**Decision**: **Recommend against.** Record as `not_collected_by_decision`. Build nothing.

**Rationale — the cost is structural, not incidental.** A comment thread is addressed *per post*
and paged. There is no per-account endpoint that returns comment bodies across a profile's posts,
because comments belong to a media object, not to a user. So the request count scales with **post
count**, not account count:

| | Current | With comment text |
|---|---|---|
| Requests per account per run | ~7–10 (R5) | ~7–10 **+ 1–3 per post** |
| At 30 posts/account | ~7–10 | **~37–100** |
| Multiple of baseline | 1× | **≈4× to 10×** |

That is the exact shape FR-010 disqualifies by name ("MUST NOT issue requests that scale
per-post"), and a 4–10× increase against targets that already throttle on a handful of exploratory
calls is squarely what Constitution X forbids.

**Secondary considerations, none of which rescue it**: comment text is user-generated content from
non-consenting third parties, which raises a privacy exposure the rest of the corpus (public
brand-authored posts) does not; and it is the highest-volume, lowest-signal-density text in the
system, so its per-request analytical yield is the worst available.

**Note on the constitution's metric table**: it lists comment text as *Observable — store as
observation*. That classifies what is *knowable*, not what is *worth collecting*. This
determination does not contradict it: comment text remains observable and is recorded as
deliberately uncollected, with this reasoning attached, so a future reversal is a decision change
rather than a rediscovery.

**Alternatives considered**: Top-N comments on top-N posts only — still per-post, still a new
retrieval surface, and a biased sample of exactly the posts whose comments are least
representative. Rejected as delivering a skewed corpus at meaningful collection risk.

---

## Summary of decisions

| ID | Decision | Marginal requests |
|---|---|---|
| R0 | Live datastore is the coverage baseline; stories and TikTok non-video are `undetermined`/limit cases the brief missed | 0 |
| R1 | `/v1/feed/user/` is the candidate comment-count path; **1 probe** to confirm, may subsume the clips pass | ≤0 to +2 |
| R2 | Share counts: storage + delivery only, no collection change | 0 |
| R3 | Follower capture already exists; route misses into capture outcomes | 0 |
| R4 | Dedicated append-only `capture_outcomes` table | 0 |
| R5 | Measure baseline by instrumenting an already-scheduled run | 0 |
| R6 | Comment text: recommend against, record the decision | 0 |

**No NEEDS CLARIFICATION items remain.**
