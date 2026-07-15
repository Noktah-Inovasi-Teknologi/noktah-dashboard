# Phase 1 Data Model: Social Profile Content Harvest & Analysis

Derived from the spec's Key Entities and the Phase 0 decisions. Two kinds of "data" exist:
(1) **in-flight run structures** passed between Prefect tasks (Python/Pydantic, not persisted), and
(2) the **persisted de-dup ledger** in PostgreSQL. The Google Sheet row shape is the delivery
contract and is specified in `contracts/sheet-schema.md`.

## In-flight structures (Prefect run only)

### Profile
The unit of run input (FR-001, ≤5 per run, FR-002).

| Field | Type | Notes |
|-------|------|-------|
| platform | enum `instagram` \| `tiktok` | Derived from the input URL/handle |
| handle | str | Account name; also the Drive folder name (FR-009) |
| profile_url | str | Canonical public profile URL passed to roach `list` |
| drive_folder_name | str | = handle (resolved account name) |
| drive_folder_id | str | Stable id of the account-named folder ensured under `HARVEST_DRIVE_PARENT_ID`; used as `drive_target` for the dedupe ledger |
| status | enum `pending`\|`collecting`\|`done`\|`skipped`\|`blocked` | Runtime state; `skipped` for private/missing/zero-content, `blocked` for rate-limit/challenge |
| skip_reason | str \| None | Logged reason when skipped/blocked (edge cases) |

Validation: run rejected/truncated to 5 profiles (FR-002); non-public profile → `skipped` with
reason, run continues (edge cases, FR-015).

### ContentItem
A single publicly visible piece of content (FR-004, FR-006).

| Field | Type | Notes |
|-------|------|-------|
| content_id | str | Platform-native unique id (dedupe key, FR-021) |
| profile_handle | str | Owning profile |
| content_type | enum `video`\|`image`\|`carousel`\|`story` | FR-004 formats. Feed runs collect image/carousel + **Reel(video)** (IG Reels via a separate `/reels/` pass — research R11). **Stories** (IG + TikTok) are collected by the dedicated `social-harvest-stories` flow via `stories_only` (research R13), not feed runs. Type is provisional in `/list`; `download` re-derives it from files. |
| is_video | bool | Routes video vs non-video collection path (FR-005) |
| source_url | str | Public source reference |
| published_at | ISO-8601 str | Publish timestamp (FR-006); drives time-window selection |
| caption | str | Public caption (FR-006) |
| hashtags | list[str] | Parsed from caption/metadata |
| public_counts | dict | likes/comments/views where visible (FR-006); **never** reach/impressions/saves (FR-003). IG feed exposes only `likes`; **views + comments for IG Reels are enriched from the Reels-grid API via browser impersonation** (research R12). `views` is video-only. |
| local_paths | list[str] | Downloaded file path(s) on the shared `social_data` volume |
| download_status | enum `ok`\|`failed`\|`skipped` | `skipped` = already in ledger; failed item logged, run continues (FR-020) |

Depth selection: **recent** flow keeps the newest N by `published_at` (default 10); **window** flow
keeps items with `published_at ≥ now − X days` (default 7). Both bounded by the hourly cap (FR-011).

### Analysis
Derived understanding of one ContentItem (FR-007).

| Field | Type | Notes |
|-------|------|-------|
| subtitle | str | Verbatim transcript; **empty** when the item has no spoken audio (Assumptions, R9) |
| flow | str | Content-flow breakdown |
| summary | str | 2–4 sentence summary |
| status | enum `success`\|`failed` | On failure the download is retained and the row records the failure (edge cases) |
| error | str \| None | Analysis error detail when `status = failed` |

### RunResult (flow return value)
Constitution I/V shape returned by both flows (never raises to caller).

| Field | Type |
|-------|------|
| start_time / end_time | ISO-8601 str (end_time set in `finally`) |
| data | list[per-item delivery records] |
| summary | dict: profiles_processed, items_collected, items_skipped_dedup, items_retried_failed, items_failed, profiles_blocked, account_folder_ids (username→folder id), detail_sheet_id |
| error | str \| None |

## Persisted: `harvested_items` (PostgreSQL, existing `postgres` service)

De-dup ledger implementing FR-021 (R6). Created in `config/postgres/init.sql` and via an idempotent
`CREATE TABLE IF NOT EXISTS` migration for existing databases.

| Column | Type | Constraints |
|--------|------|-------------|
| id | BIGSERIAL | PRIMARY KEY |
| platform | TEXT | NOT NULL |
| profile_key | TEXT | NOT NULL (normalized handle) |
| content_id | TEXT | NOT NULL (platform-native id) |
| drive_target | TEXT | NOT NULL — the **stable per-account folder id** (the `{Platform}/{username}` folder under `HARVEST_DRIVE_PARENT_ID`), resolved before download. NOT the per-run detail Sheet (which changes every run and would defeat dedupe). See research R2/R6. |
| content_type | TEXT | NOT NULL |
| collected_at | TIMESTAMPTZ | NOT NULL DEFAULT now() |
| drive_file_id | TEXT | NULL — comma-joined uploaded media file id(s); used to purge media on a failed-retry |
| analysis_status | TEXT | NOT NULL DEFAULT 'pending' — `success`\|`failed`\|`pending` |

**Uniqueness**: `UNIQUE (platform, content_id, drive_target)` — the dedupe guarantee. A pre-download
lookup returning a row with `analysis_status = success` → item skipped. A row with `failed` (or
`pending`) → the item's media, per-account Sheet row(s), and this record are deleted, then the item
is re-harvested (FR-021 failed-retry). Insert happens after successful delivery.

Indexes: the unique constraint above doubles as the lookup index; add
`INDEX (drive_target, profile_key)` for per-target/per-profile queries.

## Relationships

```text
Run (1) ──< Profile (≤5) ──< ContentItem (≤ N or window, ≤100/hr) ──1 Analysis
                                   │
                                   └─ delivered → Drive folder (per profile) + 1 Sheet row (per run)
                                   └─ recorded  → harvested_items (dedupe ledger)
```
