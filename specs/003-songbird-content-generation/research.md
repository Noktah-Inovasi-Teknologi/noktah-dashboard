# Phase 0 Research: Songbird — Targeted Content Generation

All Technical Context items resolved. No open `NEEDS CLARIFICATION` markers (the five spec
clarifications settled language, quantity source, on-demand shape, signal window, and draft columns).

## R1 — Generation runtime placement (Prefect task vs companion service)

- **Decision**: Implement generation as Prefect tasks/flows inside the existing `service/prefect` image;
  no companion service.
- **Rationale**: Generation is prompt-string assembly + one OpenRouter HTTP call. It has none of the heavy
  scraper/media dependencies (`yt-dlp`, `gallery-dl`, `ffmpeg`, curl-cffi) that justified roach's
  separate image. Keeping it in-process satisfies constitution I directly and avoids a network hop and a
  new health-checked service.
- **Alternatives considered**: (a) A `songbird` FastAPI/MCP service like knowledge-base — rejected: only
  earns its keep if the AnythingLLM chatbot must generate conversationally, which is explicitly out of
  scope for this feature; adds infra for no functional gain. (b) Embedding generation logic in roach —
  rejected: roach is a scraping/analysis primitive; content generation is an unrelated concern.

## R2 — OpenRouter access from Prefect

- **Decision**: Add a reusable `openrouter.chat.complete` task (`tasks/openrouter_tasks.py`) that ports
  roach's proven pattern — deterministic provider routing (`OPENROUTER_PROVIDER_ORDER`), reasoning
  disabled, strict `json_schema` response formats, and 429 back-off honoring `Retry-After` — but text-only
  and async (`httpx.AsyncClient`). Provide `object_schema()` and `array_schema()` helpers (arrays wrapped
  under an `items` key, since OpenRouter json_schema roots must be objects).
- **Rationale**: Reuses a battle-tested integration (`service/roach/analyze.py`), keeps behaviour
  consistent across services, and needs no new dependency (`httpx` is already used by `social_tasks.py`).
  A shared task lets future flows call the model too.
- **Config**: `OPENROUTER_API_KEY` (required) must be added to the `prefect` and `prefect-worker` compose
  env (today it lives only in `service/roach/.env`); `OPENROUTER_MODEL` optional override.
- **Alternatives considered**: The official `openai` SDK pointed at OpenRouter — rejected: adds a
  dependency and diverges from the in-repo pattern for no benefit.

## R3 — "Hit" signal store (harvested_signals)

- **Decision**: New Postgres table `harvested_signals` mirroring engagement + analysis, populated
  best-effort by the existing social-harvest engine alongside each delivered item; songbird ranks via SQL.
- **Rationale**: The existing `harvested_items` ledger stores only dedup/status — engagement counts and
  roach `{subtitle, flow, summary}` live **only** in per-account Google Sheets, which are awkward and
  brittle to rank across. A dedicated table makes `ORDER BY (likes+comments)` trivial and keeps songbird a
  pure consumer. Idempotent upsert on `(platform, content_id)` (FR-013); a write failure is caught and
  logged, never aborting the harvest (FR-014).
- **Ranking window**: rolling 180 days by `published_at` (FR-009a), configurable via a task/env param.
- **Views now captured (update 2026-07-16)**: roach was enhanced (feature 002, research R12) to enrich
  Instagram **Reel views (play count)** + comments from the Reels-grid API via browser impersonation, so
  `harvested_signals.views` is now populated for IG video items. Views proved the decisive reach signal
  (a reel with 108 likes had **545K views**), available for competitors and owned accounts alike (no
  Graph API). **Current ranking still uses `(likes + comments)`** (`ix_signal_profile_engagement`);
  incorporating `views` for video (and normalizing engagement by account baseline) is the top recommended
  enhancement — likes-only badly under-ranks high-reach Reels.
- **Alternatives considered**: (a) Read the per-account "{username} - Social Harvest" workbooks at
  generation time — rejected: brittle sheet discovery, slow, Python-side ranking. (b) Extend
  `harvested_items` with the columns — rejected: overloads a dedup ledger with unrelated analytics fields
  and complicates its unique-index semantics.

## R4 — Client grounding source (knowledge_records)

- **Decision**: Query current (`superseded_by IS NULL`) `knowledge_records` for the client via trigram
  fuzzy match on `client_key`/`client_name` (the KB may store a slightly different casing/spelling than
  the flow's client param). Return `{subject, information}` rows to seed the prompt.
- **Rationale**: Reuses feature 001's data and its existing `pg_trgm` indexes (`ix_client_key_trgm`).
  Degrades gracefully to "no records" without error (FR-011).
- **Alternatives considered**: Calling the knowledge-base MCP server over HTTP — rejected: adds a network
  dependency and auth for what is a simple same-database read.

## R5 — Per-client quantity source (Clients worksheet)

- **Decision**: Read the monthly content quantity per client from the Clients worksheet
  (`1-aV46TIn4m_zs3vtCNeS_Bvl3Tt-tgg09uuG_NqgNNY`) via the existing `google_read_sheet_data` task,
  matching the client row and reading a quantity column. The spreadsheet id, tab name (`Clients`), and
  quantity column name are env-overridable (`SONGBIRD_CLIENTS_SPREADSHEET_ID`, `SONGBIRD_CLIENTS_TAB`,
  `SONGBIRD_QUANTITY_COLUMN`) with the known defaults. Fail fast with a clear message when the client row
  or quantity is missing (FR-003b); a per-run `quantity` param overrides the sheet.
- **Rationale**: This is the same workbook operators already maintain for the content-plan → Jira flow, so
  the quantity lives where the team already curates client config; no new config surface.
- **Open detail (resolve in tasks/impl)**: the exact quantity column header must be confirmed against the
  live sheet. Because the column name is env-configurable and the flow fails fast when absent, this does
  not block planning — it is a one-line config value, not a design change.
- **Alternatives considered**: A `CLIENT_QUANTITY` hashmap in `hashmap.py` — rejected: quantity changes
  per client per season and belongs in the operator-maintained sheet, not in code (constitution's hashmap
  guidance is for stable structural mappings like worker/component ids).

## R6 — Own vs competitor handle resolution (CLIENT_SOCIAL)

- **Decision**: Add a `CLIENT_SOCIAL` mapping to `hashmap.py`: `client → {"own": [handles],
  "competitors": [handles]}`. Per-run params may add/override handles. Handles match
  `harvested_signals.profile_key` case-insensitively.
- **Rationale**: The own/competitor structural association is stable config that fits the mandated hashmap
  pattern (like `COMPONENTS`/`CONTENT_EDITOR`). Empty/missing → no signal, and generation degrades to
  knowledge + params (FR-011).
- **Alternatives considered**: Auto-discovering competitors — rejected: out of scope (spec Assumptions).

## R7 — Publish-date distribution (monthly plan)

- **Decision**: Songbird computes publish dates in the engine (never the model, FR-004): distribute
  `quantity` items across the target month by an even spread over the month's days (e.g. evenly spaced day
  indices), formatted `YYYY-MM-DD` for the `Tanggal` column. When `quantity` exceeds the day count,
  multiple ideas share dates but remain spread (spec Edge Cases).
- **Rationale**: Deterministic, testable, and keeps the model focused on ideation. "Best-time-to-post"
  modelling is out of scope (spec Assumptions).
- **Alternatives considered**: Letting the model assign dates — rejected by FR-004 (unreliable, untestable).

## R8 — Content-plan column alignment (draft vs live)

- **Decision**: The generated idea maps to the content-plan columns the downstream flow reads
  (`convert_content_plan_row_to_jira_issue`): at minimum `Topik`, `Tanggal`, `Bentuk`, plus `Format`,
  `Purpose/Theme`, `Strategic Application`, `Shoot Guide` (video/story capture guide, `-` for posts;
  renamed from `Visualisasi Konten`), `Reference` (post content flow / video reference link). The **draft** sheet uses these columns
  **plus** reviewer-only rationale columns (`Adapted Pattern`, `Source Exemplar`, `Rationale`, `Hit Note`)
  (FR-016a). The **live** target reads the existing worksheet header via `google_read_sheet_data`, then
  appends rows aligned by column name — unmapped generated fields omitted, rationale columns excluded
  (FR-016).
- **Rationale**: Aligning by name (not position) tolerates live-sheet column reordering (spec Edge Cases)
  and guarantees downstream consumption without reformatting (FR-017). Contract captured in
  `contracts/content-plan-row.md`.
- **Alternatives considered**: Positional append with a hard-coded column order — rejected: silently
  misaligns if the live sheet's columns differ.

## R9 — Language / code-mixing (Indonesian + English)

- **Decision**: The generation prompt instructs: write primarily in Bahasa Indonesia, retain English terms
  where a native marketer naturally would (brand/proper names, hashtags, loanwords like "reels"/
  "engagement", trending phrases, taglines), do not force-translate, and mirror the code-mixing style
  present in the client's knowledge records and harvested captions (FR-003a).
- **Rationale**: Matches how the Indonesian audience and clients actually write; strict Bahasa-only would
  read unnaturally and mistranslate brand/marketing terms.
- **Alternatives considered**: Post-generation language normalization — rejected: would strip the
  intentional code-mixing the requirement calls for.

## R10 — Run-outcome shape & "not guaranteed" caveat

- **Decision**: Both flows return the standard dict; `summary` carries `ideas_requested`, `ideas_produced`,
  `ideas_failed`, `signal_available` (bool), `target` (`draft`/`live`), and a constant
  `hit_disclaimer` string ("Audience performance is a bias from historical patterns and is not
  guaranteed."). The disclaimer also appears as the draft's `Hit Note` column (FR-006).
- **Rationale**: Satisfies FR-006/FR-019/SC-004 with machine-checkable fields and a human-visible note.
- **Alternatives considered**: Only a log line — rejected: not verifiable per SC-004.
