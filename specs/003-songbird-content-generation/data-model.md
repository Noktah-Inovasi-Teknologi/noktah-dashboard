# Phase 1 Data Model: Songbird — Targeted Content Generation

Entities derived from the spec. Only `harvested_signals` is new persistent storage; the rest are existing
tables (reused) or in-memory structures passed between tasks.

## Persistent: `harvested_signals` (NEW — Postgres)

The rankable "what hits" store. Populated best-effort by the social-harvest engine (FR-012/013/014),
queried by songbird (FR-008/009/009a). Lives in the existing `postgres` service via
`config/postgres/init.sql`.

| Column | Type | Notes |
|--------|------|-------|
| `id` | `BIGSERIAL PK` | surrogate |
| `platform` | `TEXT NOT NULL` | `instagram` / `tiktok` |
| `profile_key` | `TEXT NOT NULL` | account handle (matches `harvested_items.profile_key`); own/competitor lookup key |
| `content_id` | `TEXT NOT NULL` | platform content id |
| `content_type` | `TEXT NOT NULL` | `video`/`image`/`carousel`/`story` |
| `published_at` | `TIMESTAMPTZ NULL` | for the 180-day ranking window |
| `caption` | `TEXT NULL` | source caption (exemplar text) |
| `hashtags` | `TEXT NULL` | space-joined |
| `views` | `BIGINT NULL` | public view/play count. Populated for **IG video (Reels)** via roach's Reels-grid enrichment (feature 002 R12); NULL for carousels/images and where the platform doesn't expose it. The strongest reach signal — see research R3 (ranking currently uses likes+comments; adding views is the top recommended enhancement). |
| `likes` | `BIGINT NULL` | public count |
| `comments` | `BIGINT NULL` | public count |
| `subtitle` | `TEXT NULL` | roach transcript |
| `content_flow` | `TEXT NULL` | roach content-flow breakdown |
| `summary` | `TEXT NULL` | roach summary |
| `advertisement` | `BOOLEAN NOT NULL DEFAULT false` | **manual** flag: was this run as a paid ad/boost? Not auto-detectable (IG doesn't expose it), so reviewer-assigned. Inserted as the column default (`false`) and **preserved on conflict** (a re-record never resets it). The **canonical source is the Account Social Harvest sheet**; the scheduled `social-harvest-sync` flow (feature 002) reconciles this column from that sheet, so the DB reflects reviewer edits. |
| `harvested_at` | `TIMESTAMPTZ NOT NULL DEFAULT now()` | last upsert time |

**Constraints / indexes**:
- `uq_harvested_signal (platform, content_id)` — idempotent upsert key (FR-013).
- `ix_signal_profile_engagement (profile_key, (COALESCE(likes,0)+COALESCE(comments,0)) DESC)` — top-N
  ranking per handle.
- `ix_signal_profile_trgm` GIN trigram on `profile_key` — tolerant handle matching.

**Write rule**: `INSERT … ON CONFLICT (platform, content_id) DO UPDATE` refreshing metrics/analysis +
`harvested_at` (never duplicates; keeps freshest metrics).

**Rank query** (songbird): `WHERE lower(profile_key) = ANY($handles)` `AND (published_at IS NULL OR
published_at >= now() - INTERVAL '180 days')` `ORDER BY (COALESCE(likes,0)+COALESCE(comments,0)) DESC,
published_at DESC NULLS LAST LIMIT $k`.

## Reused: `knowledge_records` (existing — feature 001)

Read-only. Current rows: `superseded_by IS NULL`. Songbird reads `client_name, subject, information` via
fuzzy `client_key`/`client_name` match. No schema change.

## Reused: `harvested_items` (existing — feature 002)

Unchanged. The social-harvest engine's signal write is a **separate, additional** insert into
`harvested_signals`; the dedup ledger keeps its narrow responsibility.

## In-memory structures (task inputs/outputs)

### Marketing Parameters (flow params)
`client` (str, required), `platform` (str), `audience` (str), `goal` (str), `tone` (str),
`content_pillars` (list[str]), `quantity` (int|None — None ⇒ read from Clients sheet, monthly only),
`month` (str "Month YYYY", monthly only), `date_range` (optional), `target` ("draft"|"live", monthly
only; on-demand is always draft), `signal_window_days` (int, default 180),
`credentials_block_name` (str, default "google-creds").

### Client Configuration (read from Clients worksheet)
`{client_name, quantity}` — resolved from the Clients tab of
`1-aV46TIn4m_zs3vtCNeS_Bvl3Tt-tgg09uuG_NqgNNY`. Missing client/quantity ⇒ fail fast (FR-003b).

### Client Knowledge (task output)
`{client_name, records: [{subject, information}]}`.

### Performance Signal (task output)
Ranked list of `harvested_signals` rows (dict per row incl. computed `engagement`). May be empty ⇒
degrade (FR-011).

### Content Idea (generation output → one row)
Model returns per idea (Indonesian, code-mixed per FR-003a):
`topik`, `bentuk` (content form), `format`, `purpose_theme`, `strategic_application`,
`visualisasi_konten`, plus rationale fields `adapted_pattern`, `source_exemplar`, `rationale`.
Engine adds `tanggal` (assigned date, monthly only — FR-004) and the constant hit-note.

### Plan Deliverable
- **Draft**: content-plan columns + rationale columns (`Adapted Pattern`, `Source Exemplar`, `Rationale`,
  `Hit Note`) — see `contracts/content-plan-row.md`.
- **Live**: content-plan columns only, aligned to the existing worksheet header by name (FR-016).

## Client Accounts Mapping (`CLIENT_SOCIAL` in `hashmap.py`)

`client_name → {"own": [handle,…], "competitors": [handle,…]}`. Structural config, `.get(client, {})`
with empty fallback. Per-run params may supplement.
