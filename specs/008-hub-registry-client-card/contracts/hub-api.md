# Contract: hub-api (spec 008)

**Base URLs**:
- `/v1/*` is reached only through `hub-api.noktah.co` (Access service token), with the Hub-login JWT in `X-Hub-User-Jwt`. Each call is resolved to a Person and their roles (research R1).
- `/internal/*` is reached only on the Docker network, with `X-Hub-Internal-Token`. It refuses any request carrying `cf-ray` or `cf-connecting-ip`.

**Conventions**:
- **Body:** JSON. **Times:** ISO 8601 UTC. **Money:** USD numeric.
- **Errors:** `{ "error": code, "message": Bahasa text, ...details }` with these codes:

  | Status | Code | Meaning |
  |---|---|---|
  | 401 | `unauthenticated` | Access tokens missing or invalid |
  | 403 | `no_access` | signed in, but no Manager role; the web app shows `/no-access` |
  | 403 | `forbidden` | this role can't do this action, or not in this Noktah Brand |
  | 404 | `not_found` | also returned for Clients of another Noktah Brand, so their existence doesn't leak |
  | 409 | `conflict` | the `version` sent is stale; the body carries the current state (FR-044) |
  | 422 | `invalid` | validation error |
  | 402 | `ai_cap_reached` | monthly AI cap reached; Intake is paused, direct edits still work |

- **Writes** send the `version` they read (Registry, People, Request) or the `card_version` (Card).

## Identity

| Method | Path | Who | Returns |
|---|---|---|---|
| GET | `/v1/me` | any Manager | `{person: {id, display_name}, roles: [{role, noktah_brand}], brands: [visible], can: {edit_registry, edit_guideline, approve, manage_people, appoint_bm, run_intake}}` |

## Registry

| Method | Path | Action | Notes |
|---|---|---|---|
| GET | `/v1/clients?status=&brand=` | read | `status` is active, pending, inactive or all; default active. Scoped to the Noktah Brands the caller can see. Rows: `{id, name, status, is_internal, brand, quotas, team_summary, card_completeness, pending_approvals}` |
| GET | `/v1/clients/{id}` | read | Registry record: Noktah Brand, status, quotas, folders, Jira component, team, social accounts, version |
| POST | `/v1/clients` | edit_registry | Create a Client; `brand` is required |
| PATCH | `/v1/clients/{id}` | edit_registry | `{version, name?, status?, quota_post?, quota_story?, quota_short_video?, drive_folder_id?, content_plan_folder_id?, jira_component_id?}`, logged in `registry_changes` |
| PUT | `/v1/clients/{id}/team/{team_role}` | edit_registry | `{version, person_id or null}`. `person_id` must be an active Person (FR-011) |
| POST | `/v1/clients/{id}/accounts` | edit_registry | `{platform, handle, relation: own or competitor}`. Links an existing account, or creates one |
| PATCH | `/v1/clients/{id}/accounts/{account_id}` | edit_registry | `{relation?, is_active?}`; accounts are never deleted (G-32) |
| GET | `/v1/clients/{id}/history` | read | This Client's `registry_changes`, newest first |

## People

| Method | Path | Action | Notes |
|---|---|---|---|
| GET | `/v1/people?status=` | read | People in the caller's Noktah Brands; the Owner sees everyone |
| POST | `/v1/people` | manage_people | `{display_name, emails[], jira_account_id?, slack_user_id?}`. An email another Person already has → 422 naming that Person |
| PATCH | `/v1/people/{id}` | manage_people | `{version, display_name?, jira_account_id?, slack_user_id?, status?}`. Setting `status: left` ends the Person's roles and team assignments |
| POST | `/v1/people/{id}/emails` | manage_people | `{email}` |
| DELETE | `/v1/people/{id}/emails/{email}` | manage_people | Detaches the email; the Person and their history stay |
| POST | `/v1/people/{id}/roles` | manage_people; `appoint_bm` (Owner only) to grant brand_manager | `{role, brand}`. A Brand Manager grants roles only in their own Noktah Brand, and never brand_manager or owner |
| POST | `/v1/people/{id}/roles/{role_id}/end` | as above | Sets `valid_to` |

## Client Card

| Method | Path | Action | Notes |
|---|---|---|---|
| GET | `/v1/card-definition` | read | Definition v1, including the choice lists |
| GET | `/v1/clients/{id}/card` | read | `{card_version, profil, guideline, pending, completeness, requests_open, summary: {body, generated_at, stale, cap_paused}}`. Each field's current value is `{value, valid_until, expired, source, set_by, set_at, from_intake}` |
| PUT | `/v1/clients/{id}/card/{part}/{field}` | edit_profil or edit_guideline | `{card_version, value, valid_until, source}`. A Guideline change by a PM or AE is stored as `pending` and triggers a Slack notice (G-9, G-21); by the Brand Manager or Owner it becomes `current` immediately |
| POST | `/v1/clients/{id}/card/{part}/{field}/correct` | as above | Marks the current value `corrected` and writes the new one (FR-029) |
| GET | `/v1/clients/{id}/card/{part}/{field}/history` | read | Every row, newest first, with Person, state, source and evidence link |
| POST | `/v1/clients/{id}/card/guideline/{field}/copy-from` | edit_guideline | `{card_version, from_client_id}`. Same Noktah Brand only; the approval rules apply (G-3) |
| GET | `/v1/approvals` | approve_guideline | Pending Guideline values in the caller's Noktah Brands; the Owner sees all |
| POST | `/v1/approvals/{value_id}/approve` | approve_guideline | `{}` |
| POST | `/v1/approvals/{value_id}/reject` | approve_guideline | `{reason}`; a reason is required |

## Requests

| Method | Path | Action | Notes |
|---|---|---|---|
| GET | `/v1/clients/{id}/requests?status=` | read | Newest first |
| POST | `/v1/clients/{id}/requests` | edit_profil | `{requested_on, text, requested_by, is_pic, channel, link?}` |
| PATCH | `/v1/requests/{id}` | edit_profil | `{version, status?, reject_reason?, link?}`. `ditolak` requires a reason; every change is recorded as an event |

## Intake

| Method | Path | Action | Notes |
|---|---|---|---|
| POST | `/v1/clients/{id}/intakes` | run_intake | `{kind, text?, image_base64?, mime?, url?, pdf_base64?, filename?}`, where `kind` is text, image, gdoc or pdf; 7 MB limit. Processed synchronously (≤ 90 s); returns the Intake. An identical earlier Intake returns its existing result (the cache) |
| GET | `/v1/intakes/{id}` | read | `{id, client, kind, status, failure_reason, submitted_by, submitted_at, raw_available, proposals}` |
| GET | `/v1/clients/{id}/intakes` | read | List of the Client's Intakes |
| POST | `/v1/proposals/{id}/decide` | run_intake | `{outcome, final_value?, ticks, card_version}`, where `outcome` is accept, edit or reject. Refused (422) unless the ticks its flags require are set; an `unverified` Proposal must be edited. A Request Proposal creates a Request |
| GET | `/v1/notes/unmatched` | run_intake | Old-note Intakes with no Client yet |
| POST | `/v1/notes/{intake_id}/assign` | run_intake | `{client_id}`; re-runs the pipeline for that Client |
| POST | `/v1/notes/{intake_id}/discard` | run_intake | `{}` |
| GET | `/v1/ai/usage` | read | `{month, spent_usd, cap_usd, paused}` |

A **Proposal** is `{id, target, field_key, current_value, proposed_value, excerpt, speaker, spoke_at, valid_until, flags, outcome, final_value, ticks, decided_by, decided_at}`.

## Internal (Prefect only)

| Method | Path | Called by | Returns |
|---|---|---|---|
| POST | `/internal/sheet-sync?check=false` | `hub-sheet-sync`, every 5 min; Mondays 06:00 with `check=true` | `{status, changes_applied, cells_written, overwritten}`; status is no_changes, synced or checked. Fails with 5xx, so the flow alerts |
| POST | `/internal/summaries/refresh` | `hub-summary-refresh`, hourly | `{refreshed, skipped_fresh, paused_by_cap}` |
| POST | `/internal/intakes/purge-raw` | `hub-intake-purge`, daily | `{purged}`: raw material older than 12 months |
| POST | `/internal/registry/import?validate_only=true` | `hub-registry-import`, manual | `{clients, people, team, accounts, differences, unmatched, skipped, roster_sync_paused}` |
| POST | `/internal/notes/process?limit=` | `hub-notes-process`, manual | `{processed, matched, unmatched, nothing_found, failed, paused_by_cap, remaining}` |
