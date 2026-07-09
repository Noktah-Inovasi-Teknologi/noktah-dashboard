# MCP Tool Contracts: Client Knowledge Base

The `knowledge-base` MCP server exposes the following tools to AnythingLLM. All tools return JSON.
Argument/return shapes are the contract; the server validates arguments with Pydantic and returns
typed errors (never raises to the LLM). Errors use `{ "ok": false, "error": "<message>" }`.

Design goals: minimal token payloads (return only matched records), deterministic supersession and
current/historical filtering, and confirmation-oriented ingestion.

---

## `kb_find_client`

Resolve a possibly-inconsistent client name to existing normalized clients (FR-003b).

**Arguments**
| Name | Type | Required | Description |
|------|------|----------|-------------|
| `name` | string | yes | Client name as typed by the user |

**Returns**
```json
{
  "ok": true,
  "query": "acme",
  "exact_match": "Acme Corp",
  "suggestions": [
    { "client_name": "Acme Corp", "client_key": "acme corp", "similarity": 0.82, "record_count": 12 }
  ]
}
```
- `exact_match` is the display name when `client_key` matches exactly, else `null`.
- `suggestions` ranked by `pg_trgm` similarity (desc), close matches only. Empty when the client is
  new. The LLM MUST ask the user to confirm before treating a suggestion as the same client.

---

## `kb_upsert_record`

Create a knowledge record, applying supersession automatically (FR-003, FR-003a, FR-010, R6).

**Arguments**
| Name | Type | Required | Description |
|------|------|----------|-------------|
| `client_name` | string | yes | Confirmed client display name |
| `subject` | string | yes | Moderate-granularity topic |
| `information` | string | yes | Knowledge body (length-capped) |
| `source_type` | enum(`plain_text`,`google_doc`,`google_sheet`) | yes | Origin |
| `source_reference` | string | conditionally | Required for google_doc/google_sheet |
| `created_by` | string | no | Chatbot user, if known |

**Returns**
```json
{
  "ok": true,
  "result": "created | superseded | unchanged",
  "record": { "id": "…", "client_name": "Acme Corp", "subject": "billing terms", "timestamp": "2026-07-07T…Z" },
  "superseded_record_id": "…or null"
}
```
- `created`: no prior current record for (client, subject).
- `superseded`: prior current record existed and differed → it is marked superseded; new row current.
- `unchanged`: identical information already current → no-op dedupe (R6).
- Confirmation summary for FR-004 is composed by the LLM from this return.

---

## `kb_query_current`

Retrieve current (non-superseded) knowledge for a client (US2; FR-005, FR-006, FR-007, FR-011).

**Arguments**
| Name | Type | Required | Description |
|------|------|----------|-------------|
| `client_name` | string | yes | Client to query (normalized server-side) |
| `question` | string | no | Natural-language question for relevance ranking |
| `subject` | string | no | Restrict to a subject (fuzzy match — see note) |
| `limit` | integer | no | Max records (default small, e.g. 5) |

**Returns**
```json
{
  "ok": true,
  "client_name": "Acme Corp",
  "found": true,
  "records": [
    { "id": "…", "subject": "billing terms", "information": "Net-30, invoiced monthly.", "timestamp": "2026-06-01T…Z" }
  ]
}
```
- Only `superseded_by IS NULL` rows. Ranked by `pg_trgm` similarity of `question`/`subject` against
  `subject`+`information`; if no `question`/`subject`, return all current records for the client.
- `subject` is matched **fuzzily** (`pg_trgm` similarity ≥ 0.3 against the stored subject), not
  exactly — a caller passing `subject="services"` still matches a record stored under
  `"services offered"`. An exact-match filter previously caused false "not found" results whenever
  the caller's wording didn't match the stored subject verbatim.
- `found: false` with empty `records` when the client/records do not exist — the LLM MUST state no
  information is available rather than hallucinate (FR-011, SC-003).
- Superseded records are NEVER returned here (SC-003).

---

## `kb_query_history`

Retrieve historical / superseded knowledge with time context (US3; FR-008, FR-009).

**Arguments**
| Name | Type | Required | Description |
|------|------|----------|-------------|
| `client_name` | string | yes | Client to query |
| `subject` | string | no | Restrict to a subject (fuzzy match — same as `kb_query_current`) |
| `since` | date | no | Lower bound (inclusive) |
| `until` | date | no | Upper bound (inclusive) |
| `limit` | integer | no | Max records (default e.g. 10) |

**Returns**
```json
{
  "ok": true,
  "client_name": "Acme Corp",
  "records": [
    {
      "id": "…", "subject": "billing terms",
      "information": "Net-15, invoiced weekly.",
      "timestamp": "2025-01-10T…Z",
      "status": "superseded",
      "superseded_by": "…", "superseded_by_timestamp": "2026-06-01T…Z"
    }
  ]
}
```
- Includes superseded rows (and optionally current, when the time range spans them), each labelled
  with `status` (`current`/`superseded`) and a pointer to the superseding record (FR-009, US3-AS2).
- Ordered by `timestamp` (desc by default). Time period always visible in each record.

---

## Error contract (all tools)

```json
{ "ok": false, "error": "human-readable message", "code": "validation | not_found | source_fetch | internal" }
```
- `validation`: bad/missing arguments (e.g., empty `information`, missing `source_reference` for a
  Google source).
- `source_fetch`: Google Docs/Sheets could not be retrieved (auth/permission/size).
- `internal`: unexpected server/database error (logged at ERROR; message is safe/non-sensitive).

## Notes on Google source ingestion

Fetching a Google Doc/Sheet is performed by the server when the LLM passes a link during an
ingestion turn (helper in `ingestion.py`), returning raw text to the LLM for extraction. The LLM
then issues one or more `kb_upsert_record` calls. A separate fetch tool (`kb_fetch_google_source`)
MAY be exposed if AnythingLLM's own google-drive MCP is not used for retrieval; its contract mirrors
a read-only `{ url } → { ok, text, tabs? }` shape.
