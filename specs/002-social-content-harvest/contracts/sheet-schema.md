# Contract: Drive delivery layout & Google Sheets

Delivery redesign (2026-07-13). Everything lives under the configured parent folder
`HARVEST_DRIVE_PARENT_ID`.

## Folder hierarchy

```
{HARVEST_DRIVE_PARENT_ID}/
├── {Platform}/                         # "TikTok", "Instagram", "X", "Threads", "Facebook"
│   └── {username}/                     # account handle, no leading @ (e.g. "thisisusername")
│       ├── Short Video/                # media by content format (created on demand)
│       ├── Story/
│       ├── Post/
│       ├── Text/
│       └── "{username} - Social Harvest"   # per-account Google Sheet (one tab per quarter)
└── Social Harvest Detail/
    └── "{harvest_name} - {harvest_date}"   # one Google Sheet per harvest run
```

- Folders are created if missing (idempotent `ensure_folder`).
- **Content-format → folder** mapping: `video → Short Video`, `story → Story`,
  `image`/`carousel → Post`, `text → Text` (unknown → `Post`).
- **Platform → folder** display names come from `PLATFORM_DISPLAY`.

## Per-account workbook: `"{username} - Social Harvest"`

Lives inside `{Platform}/{username}/`. One **tab per quarter**, named `Q{n} - {year}` (based on the
harvest **run** date, e.g. `Q3 - 2026`). Each collected item is one row; rows accumulate across
runs within the quarter.

### Columns (in order)

| # | Column | Source |
|---|--------|--------|
| 1 | `id` | sequential row number within the tab |
| 2 | `username` | account handle |
| 3 | `platform` | `instagram` \| `tiktok` \| … |
| 4 | `content_id` | platform-native id (dedupe key) |
| 5 | `content_type` | `video`\|`image`\|`carousel`\|`story`\|`text` |
| 6 | `source_url` | public link |
| 7 | `published_at` | ISO-8601 (`YYYY-MM-DDTHH:MM:SSZ`) |
| 8 | `caption` | normalized whitespace |
| 9 | `hashtags` | space-joined |
| 10 | `views` | public_counts.views (blank if not visible) |
| 11 | `likes` | public_counts.likes |
| 12 | `comments` | public_counts.comments |
| 13 | `drive_file_ids` | uploaded media file id(s), comma-joined |
| 14 | `subtitle` | Analysis.subtitle (empty for no-audio items) |
| 15 | `content_flow` | Analysis.flow |
| 16 | `summary` | Analysis.summary |
| 17 | `harvest_name` | the run's harvest name (defaults to the Prefect flow-**run** name, e.g. `bronze-mussel`, unless `--harvest-name` overrides it) |
| 18 | `harvest_date` | the run date (`YYYY-MM-DD`) |
| 19 | `advertisement` | **manual** `TRUE`/`FALSE` flag (written `FALSE` at harvest time) marking content run as a paid ad/boost — Instagram does not expose this, so it is reviewer-assigned. **This account sheet is the canonical source**; the scheduled `social-harvest-sync` flow reconciles the DB and the detail sheets from it (research R14). Edit it here. |

## Per-run detail workbook: `"{harvest_name} - {harvest_date}"`

Lives inside `Social Harvest Detail/`. One **new file per run**, a single tab, listing every item
collected in that run. Columns = the 19 account columns **plus** a 20th column `account_folder_id`
(the `{Platform}/{username}` folder id). `id` is sequential within this file.

## Rules

- **Never** include reach, impressions, or saves (FR-003) — only publicly visible counts.
- **De-dup is per account** (`platform` + `username` + `content_id`, keyed on the account folder id
  as `drive_target`): a prior `success` is skipped; a prior `failed` is **purged** (its media files,
  its account-sheet row(s), and its ledger record) and then re-harvested if in scope (FR-021 +
  failed-retry).
- Timestamps use ISO-8601 UTC with trailing `Z`; `harvest_date` uses `YYYY-MM-DD`.
- An item whose analysis fails still gets a row (analysis columns blank) and a ledger record with
  `analysis_status=failed`, so a later run purges and retries it.
