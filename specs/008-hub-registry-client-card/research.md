# Research: Noktah Hub v1 (spec 008)

Every question below was answered from the repo, the live system, or the grill ledger. No NEEDS CLARIFICATION remains.

## R1: Roles and permissions

- **Decision**: Every `/v1` request is resolved as follows, and roles are checked by one pure function.
  - The Hub-login JWT is verified (existing `auth.py`), then its email is looked up in `person_emails`.
  - That gives a Person and their active role assignments.
  - `permissions.can(assignments, action, brand)` returns `allow`, `deny` or `needs_approval`.
  - **Actions**: `read`, `edit_registry`, `edit_profil`, `edit_guideline`, `approve_guideline`, `manage_people`, `appoint_brand_manager`, `run_intake`, `edit_requests`.
  - **Rules** (G-9):
    - **Owner:** `allow` on every action, in every Noktah Brand.
    - **Brand Manager:** `allow` on every action in its Noktah Brand, except `appoint_brand_manager`.
    - **Project Manager / Account Executive:** `allow` on edits and `run_intake`; `needs_approval` on `edit_guideline`; `deny` on `approve_guideline` and `manage_people`.
    - **Sales & Marketing:** `read` only.
    - **Staff roles:** `deny` everything, including sign-in.
  - **Scope** (G-10): any role other than Owner is limited to its own Noktah Brand.
  - **No active manager role** → HTTP 403 `no_access`. The web app turns that into the "Anda belum punya akses" page (G-11).
- **Rationale**: one table-driven function, tested exhaustively against the G-9 table, is easier to trust than checks scattered across routes.
- **Alternatives**:
  - Cloudflare Access groups for roles: rejected by G-11.
  - Postgres row-level security: heavy for fewer than 10 Managers, and it would still need the email→Person map.

## R2: Client Card history and approvals

- **Decision**:
  - **Storage:** each value is a row in `card_values`, with a `state` of `current`, `pending`, `superseded`, `corrected` or `rejected`.
  - **At most one current value:** a partial unique index enforces one `current` row per (client, part, field).
  - **Pending Guideline changes** (PM or AE) are inserted as `pending`. On approval, one transaction marks the previous `current` row `superseded`, promotes the pending row to `current`, and stamps `decided_by` and `decided_at`.
  - **Brand Manager or Owner edits** are written directly as `current`.
  - **Corrections** write a new `current` row and mark the old one `corrected`, with a pointer to its replacement (FR-029).
  - **Concurrency:** each client carries a `card_version` counter. Every write sends the version it read, and a mismatch returns 409 with the newer state (FR-044).
- **Rationale**: history lives in the table itself, not a side log, so "who set it, when, what it replaced" is one query (SC-002). The partial unique index is the same technique knowledge_records uses for one current record.
- **Alternatives**:
  - A JSON document per card with a separate history log: loses per-field querying and invites drift between the two.
  - Temporal tables: overkill for this scale.

## R3: Intake pipeline

- **Decision**:
  1. **Source → text and images.**
     - Pasted text: used as is.
     - Screenshot (PNG, JPEG or WebP, up to 5 MB): stored and sent as an image.
     - Google Doc: fetched with the Docs API. The Google read helper is ported from `service/knowledge-base/ingestion.py` into the API.
     - PDF: `pypdf` extracts the text. If a page yields fewer than 40 characters, the PDF is treated as scanned, and `pypdfium2` renders up to 10 pages to images (G-31).
  2. **Cache:** the input's `sha256` plus the Client id is the content hash. An identical earlier Intake returns its result, so no second model call is made (constitution XI).
  3. **One model call** with a strict JSON schema (contracts/intake-ai.md). The prompt carries:
     - the card definition (field keys and their meaning)
     - the current values of the chosen Client's Card
     - the Client's PIC name
     - the always-banned claims
     - the rules: never translate, word for word, patient data never
  4. **Validation:** `finish_reason == "length"` is checked **before** parsing, as in roach feature 007. A schema failure is retried **once**, with the validator's error quoted. A second failure records the Intake as `failed/validation_failed`.
  5. **Deterministic checks** on each Proposal:
     - **Quote in text** (text sources only): the excerpt is compared with case, whitespace and quote style normalised, and with WhatsApp line prefixes (`[dd/mm/yy hh.mm] Name:`) stripped. If it isn't found, the Proposal gets the `unverified` flag.
     - **PIC match:** the speaker is a token-subset match with the PIC name from Profil field 9. An empty PIC or no match adds the `not_pic` flag.
     - **Price completeness:** a `harga-promo` line with no unit (per mata, per paket, per sesi, /…) or no conditions field adds the `price_incomplete` flag.
     - **Other client:** the excerpt names another Registry Client or a sibling branch. This uses token-subset matching against all Client names except this one, and adds the `other_client` flag.
     - **Contradiction:** the proposed value differs from the current value and the Proposal isn't marked as an update. Adds the `contradicts` flag.
     - **Patient data:** the model's `is_patient_data` marker drops the Proposal before storage (FR-039).
     - **Images:** Proposals from image sources carry the `from_image` flag.
  6. **Acceptance rules:**
     - `from_image` needs the tick `image_checked`.
     - `not_pic` needs the tick `pic_confirmed`.
     - `unverified` must be edited, or its value retyped, before acceptance.
- **Rationale**: the model drafts, rules check, and a person decides (constitution XI, G-4, G-5). Validation discipline is copied from roach feature 007, which already proved it.
- **Alternatives**: two-pass extraction (facts, then requests) doubles cost for no measured gain; tool-calling adds provider variance.

## R4: Cost and model

- **Decision**:
  - **Models:** `HUB_INTAKE_MODEL` defaults to `xiaomi/mimo-v2.5`, the house model already routed through OpenRouter for songbird and roach video; it is multimodal. `HUB_SUMMARY_MODEL` uses the same default. Both are configuration (constitution II).
  - **Spend:** `usage.cost` from each response goes into `ai_ledger` (call site, client, model, provider, tokens, cost). The cap `HUB_AI_MONTHLY_CAP_USD=5` is checked **before** each call, counting both Intake and Summary.
  - **Alerts:** crossing 80% sends one alert per month through `SLACK_AUTOMATION_NOKTAH`.
- **Marginal cost** at today's measured rates (roach video on mimo ≈ $0.0019/item, including media):
  - a text Intake of about 3k tokens in and 1.5k out: about **$0.001–0.002**
  - a screenshot Intake: about **$0.002–0.004**
  - a Summary: about **$0.001**

  Expected month: 40 Intakes plus ≤ 23×30 Summaries, capped at once a day and only on change (realistically fewer than 100), comes to **under $1**. The 95 old notes are a one-time **~$0.2**.
- **Verification**: one live image Intake is run during implementation, before relying on mimo for screenshots. If images fail, `HUB_INTAKE_VISION_MODEL` is split out, still configuration only.
- **Measured 2026-09-25** (T050): a 720×520 WhatsApp-style screenshot with four messages, sent through the `intake_v1` prompt.
  - It was served by `xiaomi/mimo-v2.5` via DeepInfra, valid on the first attempt.
  - It used 3,216 tokens in and 291 out, costing **$0.000505**, below the estimate.
  - Both items came back word for word: the LASIK price line (with `satuan` "per mata" and `syarat` "promo pelajar tetap") and the Request "Tolong minggu depan bikin konten promo ini.". Each was flagged `from_image` only.
  - The greeting and "Siap bu" were correctly left out.
  - Images work on the house model, so **no vision model split is needed**.
- **Routing, measured 2026-09-25** (old-notes pilot):
  - 7 of 10 back-to-back calls failed with `429 "xiaomi/mimo-v2.5 is temporarily rate-limited upstream"`, all from DeepInfra.
  - Cause: we asked for provider-side `json_schema`, which for this model only DeepInfra, StreamLake and Venice support. So every call skipped Xiaomi's own endpoint (98% uptime) and landed in DeepInfra's pool, which OpenRouter users share.
  - Two providers in the old order (DigitalOcean, Parasail) no longer serve the model.
  - Changed to JSON mode with the schema in the system message, and order `xiaomi, novita, streamlake, deepinfra`. Our validator was already the authority, so validation is unchanged.
  - The old-notes batch now also spaces calls 4 s apart, and retries a provider failure once after 60 s.

## R5: Sheet copy

- **Decision**: `POST /internal/sheet-sync`, called by the `hub-sheet-sync` flow every 5 minutes.
  - **Change detection:** it reads `registry_changes` since the last successful sync (tracked in `hub_sync_state`). With nothing new, it returns `no_changes` (at most one Sheets read per run for the weekly check mode).
  - **Clients tab:** the desired row set is non-internal Clients, keyed by `clients.sheet_row_name`, the name last written to the sheet, so renames update the right row. The Hub manages only the `Name`, `Folder ID`, `Content Plan Folder ID`, `Status`, `Post`, `Story`, `Short Video`, `Instagram` and `TikTok` columns. It never touches `No.` or `Total Minutes Equivalent`, or any other column (G-20). New Clients are appended.
  - **Hashmaps tab:** each block's range is rewritten from the Registry:
    - WORKERS `B3:C`: Person name → Jira account
    - COMPONENTS `F3:G`
    - CONTENT_EDITOR `J3:K`
    - FIELD_ASSOCIATE `N3:O`
    - CLIENT_SOCIAL `R3:T`: one row per competitor

    Other columns are never written. The value cells use the exact names `hashmap.py` resolves (a Person's display name is the WORKERS key).
  - **Header note:** row 1 of both tabs gets the cell note "Dikelola oleh Noktah Hub — jangan edit di sini" (FR-016).
  - **Writes:** only changed cells, in one `values.batchUpdate` per tab.
  - **Failure:** Prefect retries and `alert_hooks("noktah")` alerts. The sync state isn't advanced, so the next run retries the same changes (never silently skipped, G-7).
  - **Check mode** (FR-018): diff only. It lists differences, overwrites them with Registry values, and returns what it overwrote. It runs weekly (the Monday 06:00 run uses `check=true`).
- **Rationale**: diff-then-write keeps formulas and unmanaged columns intact and stays well within Sheets quotas (the 429s seen on 2026-09-24 came from row-by-row reads).
- **Alternatives**: rewriting whole tabs would destroy formulas and extra columns; Apps Script triggers would put logic in the sheet.

## R6: Import

- **Decision**: `POST /internal/registry/import?validate_only=true|false`, run by the `hub-registry-import` flow.
  - **Sources:** it reads the Clients tab, the Hashmaps blocks and the current `clients`, `accounts`, `account_handles` and `client_account_roles` rows.
  - **Matching:** Clients are matched by normalised name, using the token-subset rule and the explicit overrides already in `flows/common/roster.py` (e.g. "Nirwana Coffee Shop Sumenep" ↔ "…Space…").
  - **The sheet wins** for name, status, quotas and folders (G-19). Existing accounts and history are linked, not recreated.
  - **Eskala** is created as an internal Client (G-18).
  - **People:** Person records come from WORKERS (name + Jira ID, no email). The first roles are seeded by email: Owner `core@noktah.co`, Brand Manager Venyu `bagas@noktah.co`, Brand Manager Eskala `defila@noktah.co` (G-15). CONTENT_EDITOR and FIELD_ASSOCIATE become team assignments, and COMPONENTS becomes each Client's Jira component.
  - **Report:** a list of every difference (sheet vs database), every unmatched name, and every value skipped with a reason. Validate-only writes nothing.
  - **The real run** writes everything in one transaction, sets `hub_sync_state` to "in sync" (the sheet already matches), and pauses the `roster-sync` deployment through the Prefect API, recorded in the result.
- **Rationale**: reuses the proven matching rules, and a validate-only report puts every difference in front of a Manager (SC-005).

## R7: Old notes

- **Decision**: `POST /internal/notes/process` walks the current `knowledge_records` rows in batches of 10.
  - Each row becomes an Intake (kind `old_note`, raw text = subject + information, source = the record id).
  - **Matching** by `client_id` if set, otherwise by token-subset name match. No or ambiguous match → `client_id` null, and the Intake appears in "belum ada klien" (G-17).
  - Matched notes run the Intake pipeline. Notes whose content maps to no card field become an Intake outcome of `tidak_masuk_kartu`.
  - English text is kept as is and flagged `not_bahasa`, never translated (G-13).
  - It is **idempotent by record id**, so re-running skips done notes.
  - It stops at the cost cap and resumes on the next run.
  - "Assign client" on an unmatched note re-runs the pipeline for it.

## R8: Web proxy and auth

- **Decision**: `server/api/hub/[...path].ts` forwards to `hubApi()`, which keeps the service token and the `X-Hub-User-Jwt` behaviour already live.
  - The method, query and JSON body are forwarded. Screenshots and PDFs go as base64 in JSON, up to 7 MB. Per-route handlers aren't needed.
  - **Denials:** 403 `no_access` redirects to `/no-access`, and 409 shows a "Data sudah diubah orang lain, muat ulang" toast.
  - **Cloudflare Access for `hub.noktah.co`** changes to *Include: Everyone* (any Google or email-code sign-in), an ops step. `automate.noktah.co` keeps "Only managerials" (G-14).
- **Rationale**: one proxy means one place where credentials are attached, and fewer files to keep in sync with the API.

## R9: Slack notices

- **Decision**:
  - **Approvals** (G-21): two new incoming webhooks on the existing "Noktah Otomasi" Slack app, `SLACK_MANAGERIAL_ESKALA` (#eskala-managerial) and `SLACK_MANAGERIAL_NOKTAH` (#noktah-managerial, used for Venyu). This is a user step, like the otomasi webhooks.
  - **Cap alerts:** go to the existing `SLACK_AUTOMATION_NOKTAH`.
  - **Delivery:** best-effort, never blocking the save. A failed notice is logged, and the approval still appears in "menunggu persetujuan".

## R10: Retiring AnythingLLM (G-33)

- **Decision**: after Intake and old-note processing are live, and only after the user's explicit go-ahead at that task, remove:
  - the `anythingllm` and `knowledge-base` services from compose, their containers, and their images
  - the `service/anythingllm` and `service/knowledge-base` directories, including the plugin config holding the plaintext Google login
  - `.claude/rules/backend/knowledge-base.md`, and every CLAUDE.md mention
  - the `chat.noktah.co` public hostname, in the Cloudflare tunnel (a user step)

  The `knowledge_records` table **stays**: songbird reads it (S-1), and it is the old-note source.
