# Contract: Roster Sync Report

**Feature**: `004-relational-spine` | Satisfies FR-020 … FR-024b

`roster-sync` returns the standard flow dict (constitution I). Its `summary` is the contract: it is
what an operator reads to find out what the spreadsheets and the datastore disagree about.

## Shape

```json
{
  "start_time": "2026-08-01T03:00:00Z",
  "end_time": "2026-08-01T03:00:12Z",
  "data": { "clients": [], "accounts": [], "roles": [] },
  "summary": {
    "roster_sources": { "clients_sheet": 20, "components_block": 21, "union": 21 },
    "clients":  { "created": 0, "updated": 0, "deactivated": 0, "unchanged": 21 },
    "aliases":  { "created": 0, "conflicts": [] },
    "accounts": { "created": 0, "renamed": 0, "deactivated": 0, "unchanged": 22 },
    "roles":    { "created": 0, "changed": 0, "deactivated": 0, "unchanged": 22 },
    "unresolved": [],
    "source_disagreements": [],
    "newly_inactive_accounts": []
  },
  "error": null
}
```

## Fields that carry the weight

### `source_disagreements` (FR-023)

The two roster sources disagree today and will again. Never silently merged.

```json
[
  {"kind": "missing_from_clients_sheet", "name": "Eskala",
   "note": "present in COMPONENTS only; no handles, no content-type amounts"},
  {"kind": "name_mismatch", "clients_sheet": "Nirwana Coffee Shop Sumenep",
   "components_block": "Nirwana Coffee Space Sumenep",
   "resolved_to": "Nirwana Coffee Space Sumenep", "via": "alias"}
]
```

### `aliases.conflicts` (FR-003)

A name already claimed by a different client. Reported, **never** resolved by choosing.

```json
[{"alias": "Nirwana Coffee Space", "requested_client": "Nirwana Coffee Space Sumenep",
  "already_owned_by": "Nirwana Coffee Space Pamekasan", "action": "rejected"}]
```

The database's `UNIQUE (alias_key)` produces the rejection; the flow catches it and records it. A
conflict does not fail the run — the remaining roster still syncs (constitution V).

### `newly_inactive_accounts` (FR-024b)

The one that needs acting on. Collection targets live in `prefect.yaml`, not the datastore, so
deactivating an account here does **not** stop it being fetched — the content is collected and then
discarded at the storage boundary.

```json
[{"handle": "someclient", "platform": "instagram", "reason": "client left the roster",
  "action_required": "remove the harvest-monthly-someclient deployment"}]
```

Until an operator acts, every run for that account spends rate-limited requests on data that is
thrown away. This entry is the only warning that happens.

### `unresolved` (FR-023)

Anything the flow could not place. Never dropped, never invented.

```json
[{"kind": "handle_without_client", "handle": "rsmatadryap", "platform": "instagram"},
 {"kind": "opaque_identifier", "handle": "MS4wLjABAAAAbSCgATH…", "platform": "tiktok"}]
```

## Behavioural guarantees

| Guarantee | Requirement |
|---|---|
| Re-running changes nothing on unchanged input — all counters fall to `unchanged` | FR-020, SC-008 |
| Nothing is deleted; departures become `is_active = false` | FR-024 |
| A conflict or an unresolved entry never aborts the run | constitution V |
| Collection and generation never wait on this flow, and never refuse to start because it has not run | FR-022a |
| Runs daily on a schedule and on demand | FR-022a |
| Sheets unreachable ⇒ fail the run with a clear error; do **not** deactivate anything | An empty read must never be mistaken for an empty roster |

That last row matters more than its size suggests. `roster-sync` infers departures from *absence*
from the spreadsheet, so a failed read looks identical to every client leaving at once. It must
refuse to act on a read it did not get, exactly as `hashmap.load_hashmaps()` already raises rather
than returning empty mappings.
