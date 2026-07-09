# Feature Specification: Client Knowledge Base

**Feature Branch**: `001-client-knowledge-base`

**Created**: 2026-07-07

**Status**: Draft

**Input**: User description: "Client Knowledge Base — feed via internal chatbot (AnythingLLM), Google Docs/Sheets/plain text; chatbot parses input; token-efficient retrieval; knowledge records include timestamp, client name, information, superseded-by; also holds Client Historical Data; chatbot retrieves accurately with minimal tokens."

## Clarifications

### Session 2026-07-07

- Q: How should the system decide that a new record supersedes an existing one? → A: Add an explicit `subject` (topic/category) field; a new record supersedes the latest non-superseded record with the same `client_name` + `subject`. Subject granularity should be moderate — grouped by a meaningful topic (e.g., "billing terms", "brand guidelines"), neither per-sentence fine nor one-bucket-per-client coarse.
- Q: What is the expected scale of the knowledge base? → A: Medium — dozens of clients, hundreds of records total, years of accumulated history.
- Q: How should client identity be matched (given `client_name` is half the supersession key)? → A: Normalized + confirm — case-insensitive/trimmed matching; when a new name closely matches an existing client, the chatbot suggests the existing one and the user confirms. No full client registry in v1.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Feed Knowledge Base via Chatbot (Priority: P1)

A team member opens the internal chatbot and submits a piece of client information — either by pasting plain text, sharing a Google Doc/Sheet link, or uploading a file. The chatbot parses the content, extracts structured records (client name, information body, timestamp), and stores them in the knowledge base. The user receives confirmation that the records were saved.

**Why this priority**: This is the entry point for all knowledge — nothing else works without ingestion.

**Independent Test**: Can be fully tested by submitting a plain-text client note via the chatbot and verifying a structured record appears in the knowledge base with the correct fields.

**Acceptance Scenarios**:

1. **Given** a team member pastes plain text describing a client's current preferences into the chatbot, **When** they submit it, **Then** the chatbot creates one or more structured knowledge records and confirms the save with a summary of what was stored.
2. **Given** a team member shares a Google Docs or Google Sheets link in the chatbot, **When** they submit it, **Then** the chatbot fetches the document, parses its content into structured records, and confirms save.
3. **Given** the submitted content is ambiguous or missing a client name, **When** the chatbot processes it, **Then** it prompts the user for the missing field before saving.

---

### User Story 2 - Query the Knowledge Base via Chatbot (Priority: P1)

A team member asks the chatbot a question about a client (e.g., "What do we know about Client X's current contract?"). The chatbot retrieves the most relevant, up-to-date records from the knowledge base and responds with a concise, accurate answer, using the minimum tokens necessary while preserving quality.

**Why this priority**: Retrieval is the primary value of the knowledge base — tied in importance with ingestion.

**Independent Test**: Can be fully tested by inserting a known record and asking the chatbot a question whose answer is contained in that record, verifying the chatbot returns the correct information.

**Acceptance Scenarios**:

1. **Given** relevant records exist for a client, **When** a team member asks a factual question about that client, **Then** the chatbot returns an accurate answer drawn from the knowledge base within a reasonable response time.
2. **Given** a record has been superseded by a newer one, **When** the chatbot retrieves information, **Then** it returns the current record and does not surface the superseded version as the primary answer.
3. **Given** no records exist for a queried client, **When** the team member asks about that client, **Then** the chatbot clearly states no information is available rather than hallucinating.

---

### User Story 3 - Query Client Historical Data (Priority: P2)

A team member asks the chatbot for historical information about a client (e.g., "What were Client X's preferences last quarter?"). The chatbot retrieves records from the historical data store, clearly indicating the time period and supersession status.

**Why this priority**: Historical context is valuable but secondary to current-knowledge retrieval.

**Independent Test**: Can be fully tested by inserting a historical record with a past timestamp, then querying for that time period and verifying the correct historical record is returned.

**Acceptance Scenarios**:

1. **Given** historical records exist for a client, **When** a team member asks about a past time period, **Then** the chatbot surfaces records from that period with timestamps visible.
2. **Given** a record was superseded, **When** historical data is retrieved, **Then** the superseded record is clearly marked as no longer current, with a pointer to the record that superseded it.

---

### User Story 4 - Mark a Record as Superseded (Priority: P2)

A team member updates an existing piece of client information via the chatbot. The old record is preserved but marked as superseded by the new record, maintaining a full audit trail.

**Why this priority**: Data integrity and auditability require supersession tracking, but the feature can launch without it if needed.

**Independent Test**: Can be tested by creating a record, then submitting an update via the chatbot, and verifying the old record is marked superseded and the new record is current.

**Acceptance Scenarios**:

1. **Given** an existing knowledge record for a client, **When** a team member submits updated information for the same topic via the chatbot, **Then** the system creates a new record and links it as superseding the previous one, preserving the old record.

---

### Edge Cases

- What happens when a Google Doc is too large to process in a single pass?
- How does the system handle duplicate submissions of the same content?
- What if a Google Sheet has multiple tabs — are all tabs parsed?
- What happens when the chatbot cannot determine which client a submitted document belongs to?
- How are conflicting records (same client, same topic, same date) resolved?

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Users MUST be able to submit client information to the knowledge base via the internal chatbot using plain text input.
- **FR-002**: Users MUST be able to submit client information by providing a Google Docs or Google Sheets link in the chatbot.
- **FR-003**: The chatbot MUST parse submitted content into structured knowledge records containing at minimum: timestamp, client name, subject (topic/category), information body, and superseded-by reference (nullable).
- **FR-003a**: The chatbot MUST assign each record a `subject` at moderate granularity — a meaningful topic grouping (e.g., "billing terms", "brand guidelines") — rather than one subject per sentence or a single catch-all subject per client. Where the subject is unclear, the chatbot MUST propose one and let the user confirm or adjust it before saving.
- **FR-003b**: The system MUST match `client_name` using normalized comparison (case-insensitive, whitespace-trimmed). When a submitted client name closely matches an existing client, the chatbot MUST suggest the existing client and require user confirmation before creating records, to prevent duplicate/fragmented client identities.
- **FR-004**: The chatbot MUST confirm successful ingestion with a summary of the records created or updated.
- **FR-005**: The knowledge base MUST support querying via natural-language questions submitted through the chatbot.
- **FR-006**: The chatbot MUST return accurate, up-to-date answers drawn from knowledge base records, prioritising current (non-superseded) records.
- **FR-007**: The chatbot MUST retrieve information using the minimum tokens necessary without sacrificing accuracy or completeness of the answer.
- **FR-008**: The knowledge base MUST store Client Historical Data, preserving all past records with their original timestamps.
- **FR-009**: The chatbot MUST be able to retrieve Client Historical Data and clearly indicate the time period and supersession status of returned records.
- **FR-010**: When a new record shares the same `client_name` and `subject` as an existing non-superseded record, the system MUST mark the previous record as superseded and link it to the new record, preserving the full history.
- **FR-011**: The chatbot MUST explicitly state when no information is available for a queried client rather than generating unverified answers.
- **FR-012**: The knowledge base MUST be accessible exclusively through the internal chatbot for v1; no standalone browse or search UI is in scope.

### Key Entities

- **Knowledge Record**: A single structured unit of client information with fields: `id`, `client_name`, `subject` (topic/category at moderate granularity), `information`, `timestamp`, `superseded_by` (nullable reference to a newer record), `source_type` (plain text / Google Doc / Google Sheet), `source_reference` (URL or identifier, nullable). Uniqueness for supersession is keyed on `client_name` + `subject`.
- **Client**: An entity identified by a normalized name (case-insensitive, trimmed), grouping all knowledge records and historical data entries. Close-match names are reconciled to an existing client via chatbot-prompted user confirmation. No separate canonical-ID registry in v1.
- **Historical Data Entry**: A knowledge record with a past timestamp that has been superseded; stored in the same or a separate data store, retrievable with time-period context.
- **Ingestion Session**: Represents a single chatbot submission — one document or text block — producing one or more Knowledge Records.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Team members can submit a plain-text client note and receive confirmation of stored records within 30 seconds.
- **SC-002**: Team members can retrieve accurate answers to client-related questions via the chatbot with a response time under 15 seconds.
- **SC-003**: 100% of current (non-superseded) knowledge records are surfaced correctly when queried; superseded records are never returned as the primary answer.
- **SC-004**: At least 90% of submitted Google Docs and Sheets are successfully parsed into structured records without manual intervention.
- **SC-005**: Historical queries correctly identify the time period of returned records 100% of the time, with supersession links visible.
- **SC-006**: Token usage per retrieval query is reduced by at least 30% compared to naive full-document retrieval, while maintaining answer accuracy above 90% as measured by spot-check review.

## Assumptions

- The internal chatbot is AnythingLLM, already deployed and accessible to team members.
- Google Docs and Sheets are accessible via the existing Google OAuth credentials already configured in the project.
- "Client name" is captured as text but matched via normalization (case-insensitive, trimmed) with chatbot-prompted confirmation on close matches; a canonical client registry with alias management is out of scope for this feature.
- Plain-text submissions are typed or pasted directly into the chatbot — file uploads are out of scope for v1 unless trivially supported by AnythingLLM.
- The knowledge base and historical data may share the same underlying storage, with supersession status distinguishing current from historical records; the planning phase will determine if a separate store is warranted.
- Whether Prefect is used for ingestion pipelines (e.g., scheduled Google Sheets sync) will be determined during planning and is intentionally left open in this specification.
- Access control is not in scope for v1 — all team members with chatbot access can read and write the knowledge base.
- Expected scale is medium: dozens of clients and hundreds of current records total, with years of superseded/historical records accumulating over time. The retrieval approach chosen in planning must remain accurate and token-efficient at this scale.
- The "superseded-by" link is created automatically when updated information is submitted for the same `client_name` + `subject`; manual supersession is not required for v1.
