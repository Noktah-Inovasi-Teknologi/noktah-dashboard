# Feature Specification: Social Profile Content Harvest & Analysis

**Feature Branch**: `002-social-content-harvest`

**Created**: 2026-07-12

**Status**: Draft

**Input**: User description: "Extend the roach service into an automated capability that scrapes and analyzes content from public Instagram and TikTok profiles, run manually or on a schedule via Prefect, delivering downloaded content and a Google Sheet of metadata + analysis to Google Drive, within rate/safety limits and with a completion notification."

## Clarifications

### Session 2026-07-12

- Q: Per-profile collection depth for a single run (most-recent-N vs time window vs everything)? → A: Provide two flows — one that collects most-recent-N (reverse-chronological, per-profile cap) and a separate flow that collects by time window (items published in the last X days). Both share the same collection/analysis/delivery internals and differ only in the depth-selection bound.
- Q: Analysis Sheet delivery shape (one aggregated Sheet per run vs one per profile)? → A: One aggregated Google Sheet per run containing all profiles' items as rows, with a `profile` column identifying each row's source profile.
- Q: Re-run/idempotency behavior (de-dupe vs overwrite vs append)? → A: De-duplicate by the platform's content identifier — items already collected for the same Drive delivery target are skipped (not re-downloaded and not duplicated as Sheet rows).
- Q: Default bounds for the two flow variants (N most-recent items, X time-window days)? → A: Default N = 10 most-recent items per profile; default X = 7 days. Both remain operator-configurable.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Harvest and analyze a batch of public profiles (Priority: P1)

An operator supplies up to five public Instagram or TikTok profile accounts and starts a run.
With no per-post manual work, the system collects every available content format from each
profile (short videos/Reels, image posts, multi-item carousels, and stories), records each
item's public metadata, analyzes each item, and delivers the downloaded content plus an
analysis Google Sheet to Google Drive — the content sorted into one Drive folder per profile,
named after the account.

**Why this priority**: This is the entire reason the capability exists. Without the end-to-end
harvest → analyze → deliver path, nothing else has value. It is the MVP.

**Independent Test**: Provide one public profile, run the flow, and confirm that a Drive folder
named after that account contains the downloaded content and that a Google Sheet lists each
item's metadata and analysis (transcript, content-flow breakdown, summary).

**Acceptance Scenarios**:

1. **Given** one public TikTok profile, **When** the operator runs the flow, **Then** a Drive
   folder named after the account contains the profile's downloaded content across every
   available format, and a Google Sheet contains one row per content item with its metadata and
   analysis.
2. **Given** one public Instagram profile with videos, images, carousels, and stories, **When**
   the flow runs, **Then** all four formats are downloaded and appear in that profile's Drive
   folder, with video items fetched via the video path and non-video items via the non-video
   path.
3. **Given** up to five public profiles in a single run, **When** the flow completes, **Then**
   each profile has its own named Drive folder and every collected item is represented as a row
   in the analysis Google Sheet.
4. **Given** a content item, **When** it is analyzed, **Then** its Sheet row includes at minimum
   a subtitle transcript, a content-flow breakdown, and a content summary, in addition to its
   captured metadata.

---

### User Story 2 - Safe, resilient collection without tripping bot detection (Priority: P1)

The system collects content in a way that avoids platform bot-detection: it paces requests,
inserts human-like randomized delays, collects sequentially rather than in parallel bursts,
detects rate-limit or verification-challenge responses, and backs off and resumes rather than
hammering. A single profile being temporarily blocked does not fail the whole run or discard
content already collected from other profiles.

**Why this priority**: Without this, real runs get blocked or banned and produce nothing — the
P1 harvest path cannot succeed in practice. Resilience is what makes the capability usable
beyond a single lucky run.

**Independent Test**: Run against multiple profiles where one is made to return a
block/challenge response; confirm the run continues, the other profiles complete, and content
already collected from the blocked profile is retained.

**Acceptance Scenarios**:

1. **Given** a run over multiple profiles, **When** one profile returns a rate-limit or
   verification-challenge response, **Then** the system backs off, skips or defers that profile,
   and continues collecting the remaining profiles without losing already-collected content.
2. **Given** any run, **When** items are collected, **Then** requests are issued sequentially
   (not in parallel bursts) with randomized delays between them.
3. **Given** a run in progress, **When** collection is under way, **Then** no more than 100
   content items are collected within any rolling one-hour window.
4. **Given** more than five profiles are supplied, **When** the run starts, **Then** the run is
   rejected or truncated to at most five profiles with a clear message.
5. **Given** a platform allows anonymous access, **When** collecting from it, **Then** the system
   collects without a logged-in session, only falling back to a dedicated burner-account session
   where anonymous access is blocked.

---

### User Story 3 - Observability and completion notification (Priority: P2)

The operator can see structured, progress-level logging throughout the run (which profile, which
content item, running counts, applied delays, back-offs, and failures) using the orchestrator's
built-in run logging, and receives a notification when the batch run completes.

**Why this priority**: The harvest can technically succeed without notifications, but operators
running this manually or on a schedule need to know when it finished and to diagnose partial
failures. It builds on P1 rather than blocking it.

**Independent Test**: Run the flow and confirm that the orchestrator's run logs show
per-profile/per-item progress with counts, delays, and back-offs, and that a completion
notification is delivered when the batch ends.

**Acceptance Scenarios**:

1. **Given** a run, **When** it executes, **Then** the orchestrator's run logs contain structured
   entries identifying the current profile, current content item, cumulative counts, applied
   delays, back-off events, and any failures.
2. **Given** a batch run, **When** it completes (whether all-success or partial), **Then** a
   completion notification is delivered summarizing the outcome.
3. **Given** the flow is triggered on a schedule, **When** it runs unattended, **Then** the same
   logging and completion notification are produced as for a manual run.

---

### Edge Cases

- **Private or non-existent profile supplied**: the profile is skipped with a logged reason and
  does not fail the run; other profiles still complete.
- **Profile with zero public content**: an empty (or header-only) Drive folder and no analysis
  rows are produced for it, with a logged note; the run still completes.
- **A single content item fails to download or analyze**: the item is skipped and logged; its
  metadata may still be recorded with an error note, and the rest of the profile continues.
- **Analysis fails for a downloaded item** (e.g., model returns unusable output): the download is
  retained and the Sheet row records the failure without discarding the content.
- **Hourly cap reached mid-run**: collection pauses/backs off until the rolling window allows more,
  or the run ends gracefully having preserved everything collected so far.
- **Duplicate/re-run of the same profile**: already-collected items are not re-downloaded or
  duplicated in the Sheet (idempotent by content identity). *(See Assumptions.)*
- **Stories expiring mid-run**: stories available at listing time may vanish before download; a
  missing story is logged and skipped, not treated as a run failure.
- **Owner-only analytics requested**: reach, impressions, and saves are never collected — only
  publicly visible counts (e.g., likes, comments, views where shown).
- **Google Drive/Sheet delivery fails for one profile**: the failure is logged and does not
  prevent delivery for the other profiles.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST accept one or more public Instagram or TikTok profile accounts as
  input for a run.
- **FR-002**: The system MUST reject or truncate any run that specifies more than five profiles,
  enforcing a hard maximum of five profiles per run.
- **FR-003**: The system MUST operate only on public profiles and MUST NOT attempt to access
  private, owned, or API-authorized accounts, nor retrieve owner-only analytics (reach,
  impressions, saves).
- **FR-004**: For each profile, the system MUST collect content across every format the platform
  offers that is publicly visible — short videos/Reels, image posts, multi-item carousels, and
  stories.
- **FR-005**: The system MUST fetch video-related content on both Instagram and TikTok via one
  collection path, and fetch all non-video formats (images, carousels, stories, profile metadata)
  via a separate collection path.
- **FR-006**: For each content item, the system MUST capture its publicly visible metadata,
  including at least caption, hashtags, content type, publish timestamp, and any visible public
  counts (e.g., likes, comments, views).
- **FR-007**: The system MUST analyze each collected content item, producing at minimum a subtitle
  transcript, a content-flow breakdown, and a content summary.
- **FR-008**: The system MUST deliver both the downloaded content and the analysis to Google
  Drive, creating any missing folders/files as it goes.
- **FR-009**: The system MUST organize downloaded content under a nested folder hierarchy:
  `{Platform}/{username}/{Content Format}/`, where Platform is the human platform name (TikTok,
  Instagram, …), username is the account handle without a leading `@`, and Content Format is one of
  `Short Video`, `Story`, `Post`, `Text`.
- **FR-010**: The system MUST deliver analysis as Google Sheets at two levels:
  (a) a **per-account** workbook `"{username} - Social Harvest"` inside each account folder, with
  one tab per calendar quarter named `Q{n} - {year}` (by run date) accumulating that account's items
  across runs; and (b) a **per-run** detail workbook `"{harvest_name} - {harvest_date}"` inside a
  `Social Harvest Detail` folder, listing every item collected in that run. Both use the column set
  in `contracts/sheet-schema.md`; the detail workbook adds an `account_folder_id` column.
- **FR-011**: The system MUST enforce a hard limit of at most 100 content items collected within
  any rolling one-hour window across the run.
- **FR-012**: The system MUST prefer anonymous (non-logged-in) collection wherever the platform
  permits it, falling back to a dedicated burner-account session only where anonymous access is
  blocked.
- **FR-013**: The system MUST collect sequentially (not in parallel bursts) and space requests
  with human-like randomized delays to reduce the chance of triggering bot detection.
- **FR-014**: The system MUST detect rate-limit and verification-challenge responses and respond
  by backing off and later resuming, rather than continuing to issue requests.
- **FR-015**: The system MUST ensure that one profile being temporarily blocked or failing does
  not fail the whole run and does not discard content already collected from other profiles.
- **FR-016**: The system MUST be runnable both manually (on demand) and on a schedule; each flow
  definition MUST use the same definition for its manual and scheduled invocations.
- **FR-016a**: The system MUST provide two flow variants that share the same
  collection/analysis/delivery internals and differ only in per-profile depth selection:
  (1) a most-recent-N flow that collects the N most recent items per profile in
  reverse-chronological order (configurable N, default N=10, per-profile cap), and (2) a
  time-window flow that collects items published within a time window. The time-window flow MUST
  support both a **relative** window — items published within the last X days (configurable,
  default X=7; suited to recurring schedules) — and an **absolute** window — items published
  between an explicit start and end date, inclusive (suited to manual backfills). When start/end
  dates are supplied they take precedence over the relative day count. Both variants remain bounded
  by the 100-items/hour cap and the five-profile-per-run limit.
- **FR-017**: The system MUST emit structured, progress-level logging identifying the current
  profile, current content item, cumulative counts, applied delays, back-off events, and
  failures, using the orchestrator's built-in run logging rather than a separate mechanism.
- **FR-018**: The system MUST send a completion notification when a batch run finishes, using the
  orchestrator's built-in notification/automation features, summarizing the outcome.
- **FR-019**: The system MUST never place platform, Google, or model credentials in source or
  version control; all secrets MUST come from environment variables or gitignored secret files.
- **FR-020**: When a single content item fails to download or analyze, the system MUST skip that
  item, log the failure, and continue with the remainder of the run.
- **FR-021**: The system MUST de-duplicate per account by the platform's content identifier
  (platform + username + content_id, anchored on the account folder). An item whose prior harvest
  **succeeded** MUST NOT be re-downloaded or re-added. An item whose prior harvest **failed** MUST
  be purged (its downloaded media, its per-account Sheet row(s), and its ledger record deleted) and
  then re-harvested, provided it is within the current run's selection scope (e.g. the window/date
  range). This requires persisting a per-account record of collected content identifiers and their
  success/failure status.

### Key Entities *(include if data involves data)*

- **Profile**: A public Instagram or TikTok account supplied as run input. Attributes: platform,
  account handle/name, public profile metadata, resolved Drive folder name (the account name).
- **Content Item**: A single publicly visible piece of content belonging to a profile. Attributes:
  content type (short video/Reel, image post, carousel, story), publish timestamp, caption,
  hashtags, visible public counts (likes, comments, views), source reference, and downloaded
  file(s).
- **Analysis**: The derived understanding of one content item. Attributes: subtitle transcript,
  content-flow breakdown, content summary, and any additional analysis outputs; plus a status
  indicating success or failure.
- **Run (Batch)**: A single manual or scheduled execution. Attributes: the set of input profiles
  (≤5), start/end time, per-profile and per-item counts, delays and back-off events, failures,
  and overall outcome for the completion notification.
- **Delivery Target**: The Google Drive destination for a run — the per-profile named folders
  holding downloaded content and the analysis Google Sheet.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: An operator can supply up to five public profiles and, with no manual per-post work,
  end a run with each profile's content in its own account-named Drive folder and a Google Sheet
  of metadata and analysis.
- **SC-002**: For every successfully collected content item, the analysis Google Sheet contains a
  row with its metadata plus a subtitle transcript, content-flow breakdown, and content summary.
- **SC-003**: Across all supported formats, video and non-video content from both platforms is
  collected — a run over a profile that has videos, images, carousels, and stories yields items of
  each available format in the output.
- **SC-004**: No more than 100 content items are collected within any rolling one-hour window, and
  no more than five profiles are processed per run.
- **SC-005**: In a run where one profile is temporarily blocked, at least the remaining profiles
  complete successfully and no already-collected content is lost.
- **SC-006**: Every run produces structured progress logs (profile, item, counts, delays,
  back-offs, failures) retrievable from the orchestrator, and a completion notification is
  received when the batch finishes.
- **SC-007**: The identical flow definition produces the same outputs whether triggered manually
  or on a schedule.
- **SC-008**: No credential values appear in source control or logs.

## Assumptions

- **Per-profile collection depth**: Depth is resolved via two flow variants (see FR-016a) — a
  most-recent-N flow (reverse-chronological, configurable per-profile cap, default N=10) and a
  time-window flow (items published within a configurable last-X-days window, default X=7). Both
  are bounded by the 100-items/hour throughput limit. Full lifetime back-catalog collection is out
  of scope for a single run.
- **Notification channel**: The completion notification is delivered via a configurable
  orchestrator notification block; the specific destination (e.g., email or chat webhook) is a
  deployment configuration detail, not fixed by this spec.
- **Idempotency / re-runs**: Re-running against a profile does not re-download or duplicate
  content items already collected in a prior run for the same delivery target; items are
  de-duplicated by the platform's content identifier (confirmed during clarification, see FR-021).
  This implies the system persists previously collected content identifiers per delivery target.
- **Analysis scope**: Analysis operates on what is downloaded; transcripts are produced for items
  that contain audio/spoken content, and non-applicable analysis fields are left empty for items
  where they do not apply (e.g., a still image has no subtitle transcript).
- **Delivery format of the Sheet**: A single analysis Google Sheet per run aggregates all
  profiles' items with a `profile` column (confirmed during clarification).
- **Extends the existing roach service**: This capability builds on the already-scaffolded roach
  service (video download and model-based analysis) and is orchestrated per the project's Prefect
  conventions, reusing the existing Google OAuth refresh-token credential pattern.
- **Platform reliability**: Anonymous access levels, available formats, and visible public counts
  vary by platform and over time; the spec targets what is publicly visible at collection time and
  treats platform-side unavailability as a logged skip, not a failure.
- **Stories collected via a dedicated flow (implementation)**: Regular feed listings (recent-N /
  time-window) collect image/carousel posts + **Reels** for Instagram (Reels need a dedicated
  Reels-tab pass — research R11). **Stories** (Instagram + TikTok) are ephemeral (~24h) and are
  collected separately by the **`social-harvest-stories`** flow via roach's `stories_only` listing
  (research R13) — meant to run frequently to catch them before they expire, not mixed into feed runs.
- **Instagram engagement counts (implementation detail)**: Instagram's feed listing exposes only
  `likes`. **Views (play count) and comments for Reels** are enriched from the Reels-grid API via
  browser impersonation (research R12), so `public_counts.views` is populated for Instagram **video**
  items (carousels/images have no views). Competitor and owned accounts are treated identically (no
  Instagram Graph API dependency).
- **Anti-detection — impersonation is mandatory (implementation)**: Every Instagram/TikTok request
  (yt-dlp, gallery-dl, and the direct Reels/clips API call) MUST impersonate a real browser TLS
  fingerprint; a bare HTTP client is flagged and `429`/`403`ed. See research R12 and
  `.claude/rules/backend/roach.md`.

## Open Questions

- *(Resolved 2026-07-12)* Per-profile depth cap → two flow variants: most-recent-N and
  time-window. See Clarifications and FR-016a.
