# Phase 1 Data Model: Client Knowledge Base

Derived from spec Key Entities and Functional Requirements. Single-table design per research R3.

## Table: `knowledge_records`

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | `uuid` | PK, default `gen_random_uuid()` | Stable record identifier |
| `client_name` | `text` | NOT NULL | Display form as entered/confirmed |
| `client_key` | `text` | NOT NULL | Normalized: `lower(trim(client_name))`, collapsed internal whitespace. Supersession + retrieval key (FR-003b) |
| `subject` | `text` | NOT NULL | Topic/category, moderate granularity (FR-003a) |
| `subject_key` | `text` | NOT NULL | Normalized: `lower(trim(subject))`. Supersession key half |
| `information` | `text` | NOT NULL, length-capped | The knowledge body |
| `timestamp` | `timestamptz` | NOT NULL, default `now()` | Creation time (ISO 8601 per constitution) |
| `superseded_by` | `uuid` | NULL, FK → `knowledge_records(id)`, **DEFERRABLE INITIALLY DEFERRED** | NULL = current; set = historical (FR-010) |
| `source_type` | `text` | NOT NULL, CHECK in (`plain_text`,`google_doc`,`google_sheet`) | Ingestion origin |
| `source_reference` | `text` | NULL | URL/identifier of source document, if any |
| `created_by` | `text` | NULL | Optional attribution (chatbot user), if available |

### Derived / invariants

- **Current record**: `superseded_by IS NULL`.
- **Historical record**: `superseded_by IS NOT NULL` (points to the record that replaced it).
- **Supersession uniqueness**: at most **one** current record per (`client_key`, `subject_key`).
  Enforced by a partial unique index (see below).

### Indexes

```text
-- At most one current record per client+subject (enforces FR-010 supersession invariant)
UNIQUE INDEX uq_current_client_subject
  ON knowledge_records (client_key, subject_key)
  WHERE superseded_by IS NULL;

-- Fast current-record lookups per client
INDEX ix_client_current
  ON knowledge_records (client_key)
  WHERE superseded_by IS NULL;

-- Historical / time-range queries
INDEX ix_client_timestamp
  ON knowledge_records (client_key, timestamp DESC);

-- Fuzzy client + subject matching (pg_trgm) for FR-003b / retrieval ranking
INDEX ix_client_key_trgm  ON knowledge_records USING gin (client_key gin_trgm_ops);
INDEX ix_subject_trgm     ON knowledge_records USING gin (subject gin_trgm_ops);
INDEX ix_information_trgm ON knowledge_records USING gin (information gin_trgm_ops);
```

Requires extensions: `pgcrypto` (or `pgcrypto`/`uuid-ossp` for UUID default) and `pg_trgm`. Both
ship with stock PostgreSQL 15.

## Entity mapping (spec → schema)

| Spec entity | Representation |
|-------------|----------------|
| Knowledge Record | A row in `knowledge_records` |
| Client | Distinct `client_key` values (no separate table in v1; normalized identity per FR-003b) |
| Historical Data Entry | A row where `superseded_by IS NOT NULL` (same table, R3) |
| Ingestion Session | Not persisted as an entity in v1; one chatbot submission produces one or more rows. Optional future `ingestion_id` column if audit grouping is needed |

## State transitions

```text
                 upsert (new client_key+subject_key)
   (none) ─────────────────────────────────────────►  CURRENT
                                                          │
             upsert w/ same client_key+subject_key        │
             AND different information                     ▼
   CURRENT ──────────────────────────────────────►  SUPERSEDED (old row)
             (old.superseded_by = new.id)                  +
                                                     new CURRENT row

   CURRENT ──── upsert w/ identical information ────►  NO-OP (unchanged; R6 dedupe)
```

- Supersession is **append + link**: the old row is never deleted or mutated except to set
  `superseded_by`; the new row becomes current. Full history is preserved (FR-008, US4).

## Validation rules

- `client_name`, `subject`, `information` MUST be non-empty after trim (FR-003).
- `information` length capped (e.g., a few KB) to keep retrieval token cost bounded; oversized
  content is split into multiple subjects by the LLM before upsert (R6).
- `source_type` MUST be one of the enumerated values; `source_reference` required when
  `source_type` is `google_doc`/`google_sheet`.
- `superseded_by` MUST reference an existing record and MUST NOT point to itself.
- Writing a new current record for an existing (`client_key`,`subject_key`) MUST atomically set the
  prior current row's `superseded_by` within the same transaction (no window with two current rows).
- **Implementation note (found during testing)**: the new row's id must be generated client-side
  (e.g. `uuid.uuid4()`) and the prior row's `superseded_by` set to it *before* the new row is
  inserted — inserting the new current row first would transiently violate
  `uq_current_client_subject` (checked immediately, not deferred). This requires the FK on
  `superseded_by` to be `DEFERRABLE INITIALLY DEFERRED`, since it references the not-yet-inserted
  new row's id until COMMIT. Additionally, because `SELECT ... FOR UPDATE` cannot lock a
  not-yet-existing row, concurrent first-inserts for the same (client_key, subject_key) can still
  race past each other; the repository retries on `UniqueViolationError` to convert the losing
  attempt into the correct `unchanged`/`superseded` outcome instead of raising to the caller.
