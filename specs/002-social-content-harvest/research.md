# Phase 0 Research: Social Profile Content Harvest & Analysis

All NEEDS CLARIFICATION items from Technical Context are resolved below. Each entry records the
Decision, Rationale, and Alternatives considered.

## R1 — Orchestration boundary: where does automation logic run?

**Decision**: All orchestration (per-profile loop, sequential pacing, randomized delays,
rate-limit/challenge back-off, 100-items/hour cap, de-duplication, Drive delivery, logging,
completion notification) runs as **Prefect flows/tasks** in `service/prefect/`. The `roach`
service is promoted from an idle scaffold to a small **internal HTTP API** exposing three stateless
primitives — `list(profile)`, `download(item)`, `analyze(item)` — over `dashboard-networks`,
authenticated with an `X-API-KEY` header. Prefect tasks (`social.*`) call roach via `httpx`.

**Rationale**: Constitution I mandates Prefect for all automation logic and requires flows to be
runnable via `docker exec prefect python flows/<name>.py`. Keeping the loop/pacing/back-off/dedupe
in Prefect satisfies this. The scraper-specific toolchain (`curl-cffi`, `ffmpeg`, `gallery-dl`)
stays isolated in roach, whose README explicitly defers the interface decision ("Prefect task, MCP
tool, internal HTTP API") until the consumer is known — this feature is that consumer. Prefect
owns the loop and interprets roach's responses (e.g., a 429/challenge status) to drive back-off.

**Alternatives considered**:
- *Install yt-dlp/gallery-dl/ffmpeg into the Prefect image and scrape in-process* — rejected:
  bloats the orchestrator image and couples Prefect upgrades to scraper-tool upgrades (Complexity
  Tracking).
- *`docker exec roach ...` from the flow* — rejected: brittle, no structured error contract, hard
  to test.
- *Roach as an MCP tool server* — rejected: MCP suits interactive chatbot tool-calls (the
  knowledge-base service), not a batch Prefect flow; a plain HTTP API is simpler here.

## R2 — Google Drive **write** access (delivery)

**Decision**: Widen the Google OAuth scopes from `drive.readonly` to
`https://www.googleapis.com/auth/drive.file` **plus**
`https://www.googleapis.com/auth/spreadsheets`. Extend `GoogleClient` with write helpers:
`ensure_folder(name, parent)`, `upload_file(local_path, folder_id, mime)`, and
`create_spreadsheet(title, parent)` / `append_rows(spreadsheet_id, rows)`. A **new refresh token**
must be minted via `run_google_oauth.py` with the widened scopes (re-consent required).

**Rationale**: The feature must create per-profile folders, upload media, and create/populate a
Sheet — none possible with `drive.readonly`. `drive.file` is least-privilege: the app can only
touch files it creates, which is exactly the delivery model (new per-run folders and Sheet). This
reuses the existing three-method OAuth pattern; only the scope list and a re-consent change.

**Delivery layout (stable parent)**: A single **configured parent folder** id
(`HARVEST_DRIVE_PARENT_ID`, env) is the root for all output. Per-profile folders (named after the
account, FR-009) are ensured *inside* that parent, and the per-run analysis Sheet is created there
too. This (a) disambiguates `ensure_folder(name)` — Drive allows duplicate folder names, so a fixed
parent + name is what makes "the account's folder" resolvable and reusable across runs, and
(b) provides the **stable `drive_target`** (the per-profile folder id) that the dedupe ledger keys
on (see R6). `drive.file` still applies because the app created that parent folder (or it was
created by this OAuth client).

**Alternatives considered**:
- *Full `drive` scope* — rejected: broader than needed; `drive.file` covers all delivery
  operations because the app owns every folder/file it makes.
- *Service account* — rejected: project standard is the OAuth refresh-token pattern (constitution
  II); introducing a service account diverges without benefit and complicates Drive ownership.

## R3 — Anonymous-first collection vs burner-account session (FR-012)

**Decision**: TikTok public profiles are collected **anonymously** (no session). Instagram
listing/stories are collected using a **burner-account cookie session** (`secrets/cookies.txt`,
Netscape format) supplied to both yt-dlp (`cookiefile`) and gallery-dl (`--cookies`), because
anonymous Instagram access only exposes the first few posts and no stories. Roach selects
anonymous vs cookie automatically per platform, falling back to cookies only where anonymous
access is blocked.

**Rationale**: Matches the roach README's documented Instagram limitation and FR-012's
"anonymous-first, burner fallback" rule. Cookies live in the gitignored `secrets/` dir (constitution
IV). The burner account is a dedicated throwaway, never a real/owned account (FR-003).

**Alternatives considered**:
- *Official platform APIs* — rejected: FR-003 forbids API-authorized/owner access; also requires
  app review and exposes only owner data.
- *Anonymous-only everywhere* — rejected: fails to collect Instagram carousels/stories and most of
  the back-catalog.

## R4 — Format coverage: video path vs non-video path (FR-004/FR-005)

**Decision**: Two collection paths inside roach.
- **Video path** = `yt-dlp[default,curl-cffi]` (+ ffmpeg) → Reels, TikTok videos (already proven
  in the scaffold).
- **Non-video path** = `gallery-dl` → image posts, multi-item carousels, stories, and profile
  metadata, for **both** Instagram and TikTok (TikTok photo-mode posts included).
Roach's `list` merges both paths into a unified item list tagged with `content_type` and
`is_video`; `download` dispatches to the correct tool by that tag.

> **Instagram caveat (see R11):** the gallery-dl profile feed (`/posts/`) does **not** include the
> Reels tab, so a second `/reels/` listing pass is merged in — otherwise Reels-heavy accounts surface
> zero video. Instagram Stories are **not** listed (TikTok stories only).

**Rationale**: yt-dlp is the mature choice for video + anti-bot (curl-cffi TLS fingerprinting);
gallery-dl has the broadest non-video coverage across both platforms (images, carousels, stories)
and shares the cookie-file mechanism, so one non-video tool covers FR-004's four formats.

**Alternatives considered**:
- *Instaloader for non-video* — rejected: Instagram-only, so TikTok photo posts would need yet
  another tool; gallery-dl covers both.
- *yt-dlp alone* — rejected: unreliable/incomplete for IG image carousels and stories.

## R5 — Anti-bot pacing, hourly cap, and back-off (FR-011/FR-013/FR-014)

**Decision**: Implemented in the shared Prefect harvest engine (`flows/common/social_harvest.py`):
- **Sequential** collection — no `asyncio.gather` fan-out across items/profiles.
- **Randomized delay** of 5–10 s (`random.uniform`) between item requests, **omitted after the
  last item** (constitution II).
- **Rolling-window limiter** — a `deque` of collection timestamps; before each collection, evict
  entries older than 3600 s and block/pause if the window already holds 100 items (FR-011).
- **Back-off** — roach surfaces rate-limit/verification-challenge responses as HTTP `429` (with a
  `reason` body); the engine applies exponential back-off (e.g., 60 s → 120 s → 240 s, capped),
  then **defers/skips** that profile and continues others (FR-014, FR-015).

**Rationale**: Keeps all pacing logic in Prefect (constitution I & V), reuses the project's
established random-delay convention, and enforces the hard cap deterministically within a single
flow run (in-memory state is sufficient because a run is one flow execution).

**Alternatives considered**:
- *Fixed delays* — rejected: predictable cadence is easier for bot-detection to fingerprint.
- *Distributed rate limiter (Redis)* — rejected: Redis was removed from the stack; a run is
  single-process so an in-memory window suffices.

## R6 — Cross-run de-duplication persistence (FR-021)

**Decision**: New PostgreSQL table `harvested_items` (in the existing `postgres` service) keyed by
`(platform, profile_key, content_id, drive_target)` recording `collected_at`, `drive_file_id`, and
`analysis_status`. **`drive_target` is the stable per-profile folder id** under
`HARVEST_DRIVE_PARENT_ID` (R2) — *not* the per-run Sheet, which is new each run and would defeat
de-duplication. Before downloading an item, the engine resolves the profile's stable folder id,
then checks the ledger for the same `(content_id, drive_target)` and **skips** it if present; after
successful delivery it inserts the row. Created via `config/postgres/init.sql` plus an idempotent
`CREATE TABLE IF NOT EXISTS` migration.

**Rationale**: Durable, queryable, survives container/volume resets, and records analysis-failure
status (edge case: retained download + failed analysis). Matches the knowledge-base precedent of
adding a structured table to the existing Postgres rather than inventing new storage.

**Alternatives considered**:
- *Local JSON manifest* — rejected: lost on volume reset; no concurrency safety.
- *Infer from existing Drive files* — rejected: slow, fragile (renames/moves), and can't encode
  analysis status.

## R7 — Completion notification via Prefect built-ins (FR-018)

**Decision**: Use a **configurable Prefect notification block** (e.g.,
`AppriseNotificationBlock` / a webhook/email block) loaded by name and sent at flow end with an
outcome summary (profiles processed, items collected, failures). Additionally register a Prefect
**Automation** on flow-run `Completed`/`Failed` so scheduled/unattended runs notify identically.
The destination is a deployment configuration detail, not fixed by code (spec Assumption).

**Rationale**: Satisfies "orchestrator's built-in notification/automation features" (FR-018) and
produces the same notification for manual and scheduled runs (SC-007). Block-by-name keeps the
channel swappable per environment.

**Alternatives considered**:
- *Custom SMTP/webhook code in the flow* — rejected: reinvents Prefect's notification blocks and
  hardcodes a channel.

## R8 — Manual + scheduled execution from one flow definition (FR-016/FR-016a)

**Decision**: Each of the two flow files is a single `@flow` runnable directly
(`docker exec prefect python flows/social_harvest_recent.py`) **and** deployed with a cron schedule
via a Prefect deployment (`prefect.yaml` / `flow.serve`). Manual and scheduled invocations use the
identical flow function; only the trigger differs. Depth params (`n` default 10, `days` default 7)
are flow parameters overridable per run/deployment.

**Rationale**: One definition, two triggers → guarantees identical outputs (SC-007) and satisfies
FR-016a's two variants without code duplication (shared engine in `flows/common/`).

**Alternatives considered**:
- *One parametrized flow with a `mode` switch* — viable, but two thin flow files reading one shared
  engine are clearer to schedule and to run standalone, and map 1:1 to FR-016a.

## R9 — Analyzing non-video items (images/carousels) (FR-007, Assumptions)

**Decision**: Extend `analyze.py` to branch by media type. Videos keep the current single-call
video+audio path. Image posts/carousels are sent to the same OpenRouter model as `image_url`
input(s); the `subtitle` (transcript) field is left **empty** for items with no spoken audio
(spec Assumption). Stories are analyzed by their media type (video or image). Every item still
yields `flow` and `summary`; N/A fields are empty, not errors.

**Rationale**: FR-007 requires transcript + content-flow + summary "at minimum" while the
Assumptions allow non-applicable fields (e.g., a still image has no transcript) to be empty. Reuses
the existing OpenRouter analysis contract and error handling (retained download on analysis
failure).

**Alternatives considered**:
- *Skip analysis for non-video* — rejected: FR-004 requires all formats collected and FR-007
  requires each item analyzed; images still get flow/summary.
- *Separate OCR/vision pipeline* — rejected: the chosen model already accepts image input; no extra
  dependency needed.

**Verify during implementation**: confirm `xiaomi/mimo-v2.5` accepts `image_url` content parts; if
not, fall back to a vision-capable OpenRouter model for the non-video branch only.

## R10 — Download staging lifecycle (ephemeral `social_data`)

**Decision**: Downloaded media is staged on the shared `social_data` volume only long enough to
upload to Drive, then **deleted**. Each item's local file(s) are removed immediately after a
successful `drive.file.upload`; a best-effort retention sweep at flow end removes any residue from
skipped/failed items so the volume does not grow unbounded across scheduled runs.

**Rationale**: Drive is the system of record for delivered content; keeping local copies serves no
purpose and would fill the volume on recurring runs. Per-item cleanup keeps peak disk bounded to a
few in-flight items rather than an entire run's media.

**Alternatives considered**:
- *Keep all files until run end* — rejected: a 5-profile run can stage large volumes of video
  simultaneously; per-item cleanup caps peak usage.
- *No cleanup (rely on volume growth / manual prune)* — rejected: unbounded growth breaks unattended
  scheduled operation.

## R11 — Instagram Reels listing (post-implementation, 2026-07-16)

**Decision**: `_list_instagram` runs a **second gallery-dl pass** against the Reels tab
(`/<user>/reels/`) and merges it into the profile-feed (`/posts/`) listing, de-duplicated by content
id (posts win), best-effort. Per-post `content_type` is inferred from the count of **distinct
file-level `num`s**, not the post-level `count` meta (a single Reel reports `count: 2` for its cover
thumbnail, which mislabels Reels as carousels); `has_video` wins for single-item posts. `download_item`
still re-derives the authoritative type from the files.

**Rationale**: Instagram's profile feed extractor does not return the Reels tab, so a Reels-heavy
account (verified live: `lasikasyik` → 0 videos before, 25 after) surfaced no video at all — breaking
the "collect every available format" requirement (FR-004). A separate `/reels/` pass is the same
multi-pass shape TikTok already uses for photos/stories.

**Alternatives considered**:
- *gallery-dl `include=posts,reels` on the bare profile URL* — rejected: with `-j` the user extractor
  emits queue nodes, not per-post metadata, so it doesn't recurse into item detail.
- *Trust the post-level `count` for carousel detection* — rejected: unreliable for Reels (`count: 2`).

## R12 — Instagram views/comments enrichment via the clips endpoint (post-implementation, 2026-07-16)

**Decision**: For Instagram, roach enriches video items with **view (play) and comment counts** from
the **Reels-grid API** (`/api/v1/clips/user/`) — the same endpoint the browser's Reels tab calls —
via **`curl_cffi` browser impersonation** (`_instagram_clip_stats`), one paged request per account,
patched onto `public_counts.views`/`comments`. Works for **any public account** (competitors
included). Best-effort: a failure leaves counts as-is and never breaks the listing.

**Rationale**: The gallery-dl/feed listing exposes only `likes`; Instagram withholds view/play and
comment counts from it. Verified empirically that **gallery-dl, yt-dlp, and instaloader all fail** to
surface IG views (instaloader additionally 400s on IG's deprecated GraphQL). The clips endpoint returns
`play_count` + `comment_count` and is what the public Reels grid renders. Views proved to be the
decisive engagement signal (e.g. a reel with 108 likes had **545K views**), so likes-only badly
under-ranks reach — this is now the primary "hit" signal for songbird (feature 003).

> **⚠️ Impersonation is mandatory.** A plain `requests`/`httpx` call to this endpoint returns a
> misleading `429` even with valid cookies — the failure is the missing browser TLS fingerprint, not
> the IP or auth. The call MUST use `curl_cffi` with `impersonate=<browser>` and the per-profile
> fingerprint. This is the general rule for **all** Instagram/TikTok calls in roach — see
> `.claude/rules/backend/roach.md` and the roach README.

**Alternatives considered**:
- *Instagram Graph API* — rejected as the primary path: gives full metrics (views/reach/saves) but
  only for **owned** Business/Creator accounts, so it can't cover competitors; viable as a future
  enrichment for owned accounts only.
- *Scrape per-post detail pages / another library (instaloader)* — rejected: IG doesn't serve views to
  any of these; instaloader is currently broken against IG's GraphQL.

## R13 — Stories collection via a dedicated flow (post-implementation, 2026-07-16)

**Decision**: Stories (Instagram + TikTok) are collected by a **separate `social-harvest-stories`
flow**, not mixed into the recent-N / time-window feed runs. roach's `/list` gains a `stories_only`
flag: when set it returns **only currently-active stories** — Instagram via the Stories extractor
(`instagram.com/stories/<handle>/`, `_list_instagram_stories`, each item keyed by `media_id` with a
per-item Stories download URL), TikTok via its existing Stories pass. The flow uses an "all items"
depth selector (no N/time bound — active stories are already a small, inherently-recent set) and the
shared harvest engine (download/analyze/deliver/dedup/signal) unchanged. Left **unscheduled** for now;
intended to be run frequently once a cadence is chosen.

**Rationale**: Stories expire ~24h after posting, so catching them needs a *frequent, cheap* check —
a full feed+Reels+clips scrape on every check would be wasteful and raise bot-detection exposure. A
dedicated `stories_only` path is one fast gallery-dl call. Keeping stories out of the feed flows also
preserves those flows' existing behavior. Cross-run de-dup means re-running while a story is still live
won't re-harvest it. Validated live against `lasikasyik` (2 active stories → downloaded, analyzed,
delivered to the "Story" folder, recorded).

**Alternatives considered**:
- *Include stories in the normal feed listing* — rejected: feed runs are infrequent, so most stories
  would expire uncollected; and it changes existing flow behavior.
- *Schedule the stories flow now* — deferred: cadence (e.g. every few hours) is an operational choice;
  ship it manual/unscheduled and add a schedule later.

## R14 — Reviewer-edited fields: sheet → DB sync (post-implementation, 2026-07-16)

**Decision**: The **Account Social Harvest sheet is the canonical source of truth** for reviewer-edited
fields (currently the manual `advertisement` TRUE/FALSE flag). A scheduled **`social-harvest-sync`**
flow (daily cron) reads every account sheet's quarter tabs → writes the value into
`harvested_signals.advertisement` (only changed rows) → then **overwrites the per-run detail sheets to
match** the account sheet, so the whole system converges. Machine-written fields (engagement, analysis)
still flow DB→sheet at harvest and are not part of this sync.

**Rationale**: Staff need an easy place to mark ads, and a sheet is easiest; but songbird reads the DB,
so the two must reconcile. The account sheet (one persistent per-account ledger) is the natural
canonical source — cheaper to scan than every historical run sheet, and unambiguous when a detail sheet
disagrees (account wins). The harvest de-dup means counts freeze at first harvest, but `advertisement`
is intentionally reviewer-owned, so a dedicated sheet→DB reconciler (not the harvest path) owns it.
Validated live: an edit in the account sheet propagated to the DB row and the matching detail-sheet cell.

**Alternatives considered**:
- *Detail sheet canonical* — rejected: many historical per-run sheets to scan, and a single item can be
  edited in several places; the account sheet is the single aggregation point.
- *Merge (TRUE wins)* — rejected: can't un-mark to FALSE once any sheet says TRUE.
- *Sync inside every harvest run* — rejected: couples reconciliation to harvesting (which is
  IP-throttled/infrequent); a separate cheap Sheets-API job can run far more often.
