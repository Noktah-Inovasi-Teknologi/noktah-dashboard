# Data Model: Noktah Hub v1 (spec 008)

Migration `010_hub_registry_card.sql` is additive, idempotent, transactional and self-recording, with a matching `.down.sql`. It is mirrored in `init.sql`. Entity names follow CONTEXT.md.

## Noktah Brand: `noktah_brands`

| column | type | rules |
|---|---|---|
| id | uuid pk | |
| brand_key | text unique | `eskala`, `venyu` (seeded) |
| display_name | text | "Eskala", "Venyu" |
| slack_managerial_env | text | name of the env var holding its managers' webhook (`SLACK_MANAGERIAL_ESKALA` / `SLACK_MANAGERIAL_NOKTAH`) |

## Person: `people`, `person_emails`, `person_roles`

**people**

| column | type | rules |
|---|---|---|
| id | uuid pk | |
| display_name | text not null | also the WORKERS key written to the sheet copy |
| jira_account_id | text null | |
| slack_user_id | text null | |
| status | text | `active` or `left` (CHECK) |
| left_at | timestamptz null | |
| created_at / updated_at | timestamptz | |
| version | int | optimistic concurrency |

**person_emails**

| column | type | rules |
|---|---|---|
| id | uuid pk | |
| person_id | uuid fk → people | |
| email | text | stored lower-case; **UNIQUE**: one email belongs to one Person (G-16) |

**person_roles** (append-only periods; a role is ended, never deleted)

| column | type | rules |
|---|---|---|
| id | uuid pk | |
| person_id | uuid fk | |
| role | text | CHECK: `owner`, `brand_manager`, `project_manager`, `account_executive`, `sales_marketing`, `content_planner`, `field_associate`, `content_editor`, `qc` |
| noktah_brand_id | uuid fk null | **NULL only for `owner`** (CHECK `(role = 'owner') = (noktah_brand_id IS NULL)`) |
| valid_from | timestamptz | |
| valid_to | timestamptz null | null = active |
| granted_by | uuid fk → people null | null for installation seeds |

- **Manager roles** are `owner`, `brand_manager`, `project_manager`, `account_executive` and `sales_marketing`. The others are staff, who can't sign in (G-9).
- A partial unique index allows at most one active `brand_manager` per Noktah Brand.

## Client: `clients` (existing table, additive columns)

Existing columns: `id`, `client_key`, `display_name`, `is_active`, `created_at`, `updated_at`.

| added column | type | rules |
|---|---|---|
| noktah_brand_id | uuid fk null | backfilled to Eskala at import. Every Client belongs to exactly one Noktah Brand (G-10). NOT NULL is enforced later, through a validated CHECK, per schema.md |
| status | text null | `pending`, `active` or `inactive` (CHECK when not null). `is_active` is kept in sync (`status <> 'inactive'`) for existing readers |
| is_internal | boolean not null default false | Eskala = true (G-18) |
| quota_post / quota_story / quota_short_video | int null | ≥ 0 |
| drive_folder_id / content_plan_folder_id | text null | |
| jira_component_id | text null | |
| sheet_row_name | text null | the name last written to the Clients tab (R5) |
| version | int not null default 0 | optimistic concurrency |
| card_version | int not null default 0 | bumps on any Card write (R2) |

## Team assignment: `client_team_assignments`

| column | type | rules |
|---|---|---|
| id | uuid pk | |
| client_id | uuid fk | |
| team_role | text | CHECK: `account_executive`, `content_planner`, `field_associate`, `content_editor`, `qc` |
| person_id | uuid fk → people | **must be a Person**, never free text (FR-011) |
| valid_from / valid_to | timestamptz | partial unique index: one active assignment per (client, team_role) |
| set_by | uuid fk → people null | |

## Social account (existing: `accounts`, `account_handles`, `client_account_roles`)

Reused unchanged. `client_account_roles.role` is `owned` or `competitor`. One account may link to several Clients (FR-012). Deactivation sets `is_active = false`, never a delete (G-32).

## Change record: `registry_changes` (append-only)

| column | type | rules |
|---|---|---|
| id | bigserial pk | the sheet copy tracks the last id it synced |
| entity | text | `client`, `team`, `account_link`, `person`, `person_email`, `person_role` |
| entity_id | uuid | |
| client_id | uuid null | for per-Client history |
| field | text | |
| old_value / new_value | jsonb | |
| person_id | uuid fk null | who; null = system (import) |
| at | timestamptz | |

## Card definition: `card_definitions`

| column | type | rules |
|---|---|---|
| version | text pk | `v1` |
| body | jsonb | parsed `config/hub/card_v1.yaml` |
| frozen_at | timestamptz null | set when the first `card_values` row references it. A frozen version cannot change (constitution XII, G-29) |

**`config/hub/card_v1.yaml`** (content defined by G-24, G-28, G-29, G-30):
- **profil**: 10 fields.
  - Keys: `nama_penulisan`, `tentang_usaha`, `lokasi_kontak_jam`, `akun_hashtag`, `produk_layanan`, `harga_promo`, `orang_yang_tampil`, `target_audiens`, `pic`, `aturan_produksi_privasi`.
  - Required: `nama_penulisan`, `lokasi_kontak_jam`, `akun_hashtag`, `produk_layanan`, `pic`.
  - `harga_promo` is a list of lines: `{item, harga, satuan, syarat, berlaku_sampai}`.
  - `pic` is `{nama, jabatan, nomor, boleh_approve}`.
- **guideline**: 10 sections.
  - Keys: `inti_brand`, `positioning`, `identitas_prism`, `kepribadian_arketipe`, `suara`, `visual`, `pesan_pilar`, `wajib_ada`, `batasan`, `referensi_pembeda`.
  - Required: `suara`, `visual`, `batasan`, plus `positioning.pernyataan`.
  - Structured parts:
    - `kepribadian_arketipe` = `{sifat: {sincerity, excitement, competence, sophistication, ruggedness: 1–5}, arketipe_utama, arketipe_pendukung}`
    - `suara` = `{skala: {serius_jenaka, formal_santai, hormat_berani, antusias_datar: 1–5}, sapaan, campur_bahasa, emoji, gaya_judul, gaya_jualan, kata_dipakai[], kata_dihindari[], contoh[]}`
  - Everything else is free text or sub-fields.
- **choices**:
  - `arketipe`: the 12 archetypes (Innocent, Sage, Explorer, Outlaw, Magician, Hero, Lover, Jester, Everyman, Caregiver, Ruler, Creator), with Bahasa labels
  - `sifat`: Aaker's 5 traits
  - `skala_suara`: the 4 voice scales
- **always_banned**: `terbaik`, `nomor 1`, `100%`, `pasti sembuh`, `dijamin`, `tanpa risiko`.

## Client Card value: `card_values` (append-only history)

| column | type | rules |
|---|---|---|
| id | uuid pk | |
| client_id | uuid fk | |
| part | text | `profil` or `guideline` |
| field_key | text | must exist in the referenced definition version |
| definition_version | text fk → card_definitions | |
| value | jsonb | text or structured, **word for word**, never translated (FR-024) |
| valid_until | date null | |
| source | jsonb | `{who, where, when}` |
| state | text | `current`, `pending`, `superseded`, `corrected`, `rejected` (CHECK) |
| replaced_by | uuid fk → card_values null | set on `superseded` / `corrected` |
| set_by | uuid fk → people | who wrote it (Person, never an email) |
| set_at | timestamptz | |
| decided_by / decided_at | uuid / timestamptz null | Approval of a pending Guideline value (G-9, G-12) |
| reject_reason | text null | |
| from_proposal_id | uuid fk → intake_proposals null | evidence link (FR-036) |
| copied_from_client_id | uuid null | "copy from another client" (G-3) |

- Partial unique index: `(client_id, part, field_key) WHERE state = 'current'`.
- Partial unique index: `(client_id, part, field_key) WHERE state = 'pending'`, so one pending change per field.
- **Completeness** (G-30) is derived, not stored: required fields with a current non-empty value, divided by the total.

## Request: `client_requests`, `client_request_events`

**client_requests**

| column | type | rules |
|---|---|---|
| id | uuid pk | |
| client_id | uuid fk | |
| requested_on | date | |
| text | text | **word for word** (G-26) |
| requested_by | text | name as written |
| is_pic | boolean | |
| channel | text | `whatsapp_group`, `meeting`, `email`, `lainnya` (CHECK) |
| status | text | `baru`, `diproses`, `selesai`, `ditolak` (CHECK) |
| reject_reason | text null | CHECK `status <> 'ditolak' OR reject_reason IS NOT NULL` |
| link | text null | |
| from_proposal_id | uuid null | |
| created_by | uuid fk → people | |
| created_at / updated_at | timestamptz | |
| version | int | |

**client_request_events**: append-only status history `(request_id, from_status, to_status, reason, person_id, at)`.

## Summary: `client_summaries`

| column | type | rules |
|---|---|---|
| id | uuid pk | |
| client_id | uuid fk | |
| body | text | |
| generated_at | timestamptz | |
| source_fingerprint | text | hash of the confirmed Card, Guideline and open Requests it was built from |
| is_current | boolean | partial unique index: one current per client |
| model / cost_usd | text / numeric | |

The Summary is **stale** when the current fingerprint differs from the last one. A refresh happens only when it is stale **and** the last one is older than 24 h (G-27).

## Intake: `intakes`, `intake_proposals`

**intakes**

| column | type | rules |
|---|---|---|
| id | uuid pk | |
| client_id | uuid fk null | null only for unmatched old notes (G-17) |
| kind | text | `text`, `image`, `gdoc`, `pdf_text`, `pdf_scanned`, `old_note` |
| raw_text | text null | purged after 12 months (FR-036) |
| raw_blob | bytea null | screenshots and scanned pages; purged after 12 months |
| raw_purged_at | timestamptz null | |
| source_ref | text null | Google Doc URL, file name, or `knowledge_records.id` |
| content_hash | text | sha256; unique per (client_id, content_hash) for non-failed Intakes, which acts as the cache |
| submitted_by | uuid fk → people null | null for old notes (system) |
| submitted_at | timestamptz | |
| status | text | `processing`, `ready`, `nothing_found`, `failed`, `unmatched`, `discarded` |
| failure_reason | text null | `validation_failed`, `truncated`, `timeout`, `provider_error`, `cap_reached`, `unreadable` (CHECK; required when failed) |
| model / provider / cost_usd / prompt_version | | attribution (constitution XI) |

**intake_proposals**

| column | type | rules |
|---|---|---|
| id | uuid pk | |
| intake_id | uuid fk | |
| target | text | `profil`, `guideline` or `request` |
| field_key | text null | for profil and guideline |
| proposed_value | jsonb | word for word |
| excerpt | text | the exact source quote; **kept forever** (FR-036) |
| speaker / spoke_at / valid_until | text / date / date null | |
| flags | text[] | from `unverified`, `from_image`, `not_pic`, `price_incomplete`, `other_client`, `contradicts`, `not_bahasa` |
| outcome | text | `pending`, `accepted`, `edited`, `rejected`, `tidak_masuk_kartu` |
| final_value | jsonb null | what was actually saved, if edited |
| ticks | jsonb | `{image_checked, pic_confirmed}`, required by the flags (R3) |
| decided_by / decided_at | | |

## AI ledger: `ai_ledger`

`(id, call_site ['hub.intake', 'hub.summary', 'hub.notes'], client_id, intake_id null, model, provider, prompt_tokens, completion_tokens, cost_usd, at)`. The monthly total drives the cap (G-6). `hub_alerts_sent (month, kind)` makes sure the 80% alert fires once per month.

## Sync state: `hub_sync_state`

A single row: `(last_change_id, last_success_at, last_check_at, last_error)`, used by the sheet copy (R5).

## State transitions

- **Card value**:
  - `pending` → `current` (approved) or `rejected`
  - `current` → `superseded` (a newer value) or `corrected` (fixed as a mistake)
  - Never deleted.
- **Request**: `baru` → `diproses` → `selesai`. Any state can go to `ditolak`, which requires a reason. Every change is an event row.
- **Intake**:
  - `processing` → `ready`, `nothing_found` or `failed`
  - old notes: `unmatched` → (assigned) `processing` → … or `discarded`
- **Proposal**: `pending` → `accepted`, `edited` or `rejected` (or `tidak_masuk_kartu` for old notes). Guideline acceptance by a PM/AE creates a `pending` card value (R2).
- **Person**: `active` → `left`. Its roles and team assignments are ended (`valid_to`), not deleted.
