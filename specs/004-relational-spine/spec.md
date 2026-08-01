# Feature Specification: Relational Spine — Clients, Accounts, Runs, and Briefs

**Feature Branch**: `004-relational-spine`

**Created**: 2026-07-31

**Status**: Draft

**Input**: User description: "Establish the relational spine the application schema currently lacks, so that clients, accounts, harvested content, knowledge, and generated briefs are linked entities rather than unlinked islands."

## Context

The application datastore currently holds three tables — `knowledge_records`, `harvested_items`,
and `harvested_signals` — connected by exactly one foreign key, which points at itself
(`knowledge_records.superseded_by`). They are three unlinked islands.

Everything that would connect them lives outside the datastore:

| Fact | Where it lives today |
|---|---|
| Who our clients are | Google Sheet, "Clients" worksheet (21 rows) |
| Which social handle belongs to which client | Google Sheet, `Instagram`/`TikTok` URL columns |
| Which handle is a competitor of which client | Google Sheet, "Hashmaps" worksheet, `CLIENT_SOCIAL` block (2 clients, 4 handles) |
| Which knowledge record is about which client | A free-text name, matched at query time by a token-subset rule in code |
| What a generated content brief was | A Google Sheet row and a local JSON file — nothing in the datastore |
| Which execution produced a given row | Nothing |

Measured on the live database (2026-07-31):

- 62 current knowledge records under **14** distinct free-text client names
- 656 performance records across **18** distinct handles
- 691 collection-ledger records across **22** distinct handles
- 21 clients on the roster; **2** of them have competitors configured
- **0** rows anywhere carry an ownership role

Three concrete defects this produces, all observable in the live data:

1. **`profile_key` is an unattached string with no ownership semantics.** The collection ledger
   contains `MS4wLjABAAAAbSCgATHrl-SzAZ8n9B1Up_QTp2tEdIY1xm4T3CGUrbpF-Y51s2woFGy2z1gLaawf` —
   an opaque platform identifier stored in the same column that elsewhere holds `lasikasyik`. It
   also contains `rsmatadryap`, which matches no client and no configured competitor. Nothing in
   the schema can tell these apart from an owned account.
2. **Name reconciliation is code, and the code is ambiguous.** The roster calls a client
   `LASIK Asyik by SMEC Tebet`; the knowledge base calls it `Lasik Asyik`. The roster has
   `Nirwana Coffee Space Pamekasan` and `Nirwana Coffee Space Sumenep`; the knowledge base has
   *three* names — `Nirwana Pamekasan`, `Nirwana Sumenep`, and `Nirwana Coffee Space`. The
   token-subset rule that bridges these resolves `Nirwana Coffee Space` as a match for **both**
   roster clients, so one outlet's knowledge is currently served to the other.
3. **Ownership vanishes when a run ends.** Owned-versus-competitor is resolved inside a
   generation run from two spreadsheet sources, used to split the ranking budget, and then
   discarded. The question "which of our 656 performance records describe competitors" cannot be
   answered after the fact.

Additionally, `harvested_items` and `harvested_signals` exist **only** in the container
initialisation script, which runs once at first database creation. There is no migration file for
either, so there is currently no supported way to change their structure on a running database.

---

## Clarifications

### Session 2026-07-31

- Q: What does a harvest do when it collects a handle that has no account record? → A: Skip the item with a classified reason and continue the profile; accounts must be pre-registered by reconciliation, never invented by the write path.
- Q: What identifies an account, given that platforms allow handle renames? → A: A stable internal identifier. The handle is a current attribute plus a history of observed handles, so a rename keeps one account and one unbroken history.
- Q: When does roster reconciliation run? → A: On a daily schedule, plus a manual trigger. A harvest takes whatever is registered at the time and does not gate on reconciliation.
- Q: Are follower counts captured in this feature, or is the structure only built? → A: Captured on every harvest — an observation is written whenever the platform returns a count, and nothing is written when it does not. The Instagram capture mechanism is an open implementation question (see Assumptions).
- Q: What does marking a client, account, or relationship inactive actually do? → A: It blocks storage. Items collected for an inactive account are skipped with a classified reason and reported, exactly as for an unregistered handle.

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Ownership and client attribution answerable from the datastore (Priority: P1)

An analyst or a flow needs to know, for any piece of harvested content, which client it belongs
to and whether the account that published it is the client's own account, a tracked competitor,
or a reference account being watched for other reasons. Today this requires opening two Google
Sheets and applying a matching rule by hand. After this change it is a single query against the
system of record.

**Why this priority**: This is the headline outcome and the precondition for every downstream
analysis. Ranking, baselines, and any owned-versus-competitor comparison are currently computed
from configuration that is not recorded next to the data it describes. Delivered alone, this
story already makes the existing 656 performance records and 691 ledger records interpretable
without a spreadsheet.

**Independent Test**: Load the existing data, then answer "list every competitor account and its
client", "how many performance records belong to owned accounts", and "which configured
competitor has never been collected" without opening any spreadsheet. Verify the answers against
the current spreadsheet configuration by hand.

**Acceptance Scenarios**:

1. **Given** the 18 handles present in the performance store, **When** the roster and its
   competitor configuration have been loaded, **Then** every one of the 656 performance records
   resolves to exactly one account, and that account resolves to at least one client with a
   stated role.
2. **Given** an account related to more than one client, **When** its performance records are
   attributed, **Then** each relationship and its role is returned, and the account is still a
   single record with a single stream of harvested content.
3. **Given** the four configured competitor handles, **When** an analyst asks which competitors
   have been collected, **Then** the system distinguishes a configured-but-never-collected
   competitor from a collected one, using only stored data.
4. **Given** a collection-ledger handle that matches no client and no configured competitor
   (`rsmatadryap`, and an opaque platform identifier), **When** the existing data is loaded,
   **Then** those records are retained as an account with no client relationship rather than
   dropped, guessed at, or silently attached to a client.
5. **Given** a new harvest of a registered account, **When** it writes a performance record,
   **Then** the record is attached to an account on write, so the guarantee in scenario 1 holds
   for future data and not only for the backfill.
6. **Given** a harvest that collects a handle with no account record, **When** it attempts to
   write, **Then** the item is skipped with a classified reason, the rest of the profile is still
   processed, the skip appears in the run summary, and no account is created by the write path.
7. **Given** two handles differing only in letter case, **When** either is looked up, **Then**
   they resolve to the same account.

---

### User Story 2 - Client name reconciliation as data (Priority: P2)

The same client is named differently in the roster, in the knowledge base, and in its social
handle. A person maintaining the system needs to see and correct that mapping directly, rather
than depend on a matching rule embedded in code that can silently match the wrong client.

**Why this priority**: This is what makes client knowledge trustworthy. It is separable from
Story 1 — ownership can be answered without it — but until it lands, one Nirwana outlet's
knowledge continues to be served to the other, and any client whose knowledge-base name is not a
token subset of its roster name silently has no knowledge at all.

**Independent Test**: Record the known alternative names for all 21 clients, then verify that
each of the 14 knowledge-base names resolves to exactly one client, that
`Nirwana Coffee Space` resolves to Pamekasan only, and that no client receives another client's
records.

**Acceptance Scenarios**:

1. **Given** the roster name `LASIK Asyik by SMEC Tebet` and the knowledge-base name
   `Lasik Asyik`, **When** either is used to look up a client, **Then** both resolve to the same
   client without any similarity computation at query time.
2. **Given** the knowledge-base names `Nirwana Pamekasan`, `Nirwana Sumenep`, and
   `Nirwana Coffee Space`, **When** they are reconciled, **Then** the first and third resolve to
   `Nirwana Coffee Space Pamekasan` and the second resolves to `Nirwana Coffee Space Sumenep`,
   and no name resolves to more than one client.
3. **Given** an attempt to record an alternative name that already belongs to a different client,
   **When** it is saved, **Then** it is rejected as a conflict and surfaced to a maintainer,
   never resolved by picking one.
4. **Given** a client with no knowledge records at all, **When** its knowledge is retrieved,
   **Then** it receives zero records rather than a near-name client's records.

---

### User Story 3 - Migration-managed, reversible schema evolution (Priority: P3)

An operator needs to apply a structural change to the running database and, if it goes wrong,
undo it — without recreating the container and losing the data. Today two of the three tables
exist only in a first-boot initialisation script, so there is no supported path to change them at
all.

**Why this priority**: Necessary for Stories 1 and 2 to be applied to the existing database, and
independently valuable: it is what makes the datastore's structure reproducible and auditable
rather than a snapshot of whatever the running container happens to contain.

**Independent Test**: Apply every migration in order to an empty database and confirm the result
is structurally identical to the running database. Apply them to a copy of the running database
and confirm they are safe to re-run. Reverse the newest migration and confirm the database
returns to its previous structure with no data loss in the pre-existing tables.

**Acceptance Scenarios**:

1. **Given** an empty database, **When** all migrations are applied in order, **Then** the
   resulting structure matches the running database, including the three pre-existing tables that
   currently have no migration.
2. **Given** the running database with its 62, 656, and 691 rows, **When** the migrations are
   applied, **Then** they succeed, no existing column is removed or retyped, every existing
   uniqueness rule still holds, and no row is lost.
3. **Given** any migration in this feature, **When** it is applied twice, **Then** the second
   application is harmless.
4. **Given** a newly applied migration, **When** its reversal is applied, **Then** the structures
   it introduced are removed and the data that existed before it remain intact.

---

### User Story 4 - Durable identity for executions and generated briefs (Priority: P4)

Every collection or generation execution, and every content idea produced by one, needs a stable
identifier that outlives the run — so that a later feature can group observations by the
execution that produced them and trace a published post back to the idea it came from.

**Why this priority**: It is the placeholder the later attribution work is built on. Nothing
consumes it yet, which is why it is last; but introducing it now means the identifiers exist
before there is history to lose.

**Independent Test**: Confirm that a place exists to record executions and generated ideas, that
an identifier assigned there is stable, and that an idea can be linked to the execution and
client that produced it. Confirm the system behaves identically whether or not any such records
exist.

**Acceptance Scenarios**:

1. **Given** the new structures, **When** no execution or idea has ever been recorded, **Then**
   all existing flows behave exactly as before.
2. **Given** a recorded execution, **When** an idea is attached to it, **Then** the idea carries
   its own stable identifier and resolves to both the execution and the client.
3. **Given** a recorded idea, **When** the execution that produced it is looked up, **Then** the
   execution's kind (collection or generation), its client, and its start and end are available.

---

### Edge Cases

- **A handle that belongs to no client.** Two exist today. They are kept, marked unattributed,
  and excluded from any owned-versus-competitor answer rather than defaulting to either.
- **An opaque platform identifier stored where a handle belongs.** Present today in the
  collection ledger. It must be preserved as-is so existing records still resolve, and must be
  distinguishable from a real handle.
- **A handle whose letter case varies between records.** Lookup must be case-insensitive; the
  original form must still be recoverable for display.
- **An account that renames itself on the platform.** Once a maintainer records that the new
  handle replaced the old, both resolve to the same account and the history is continuous. Until
  then, the new handle is unregistered and its harvest is skipped per FR-016a — a visible failure
  rather than a silently split history.
- **Two handles that turn out to be the same account.** Merging is a deliberate maintainer action
  and is never inferred, since two similar handles are far more often two accounts.
- **The same handle claimed by two clients** — a shared competitor, likely on this roster where
  competitors are national chains and fifteen of twenty-one clients are eye clinics. Supported:
  one account, one relationship per client, each with its own role.
- **The same handle claimed as owned by two clients.** Rejected and reported — a handle has one
  owner, however many clients watch it.
- **An alternative client name that maps to two clients.** Exists today (`Nirwana Coffee Space`).
  Must be a rejected conflict, never silently resolved.
- **A client with no social account** (`Eskala`, `Breko`, `The StarFit`). Must be a valid client
  with zero accounts.
- **A client with an account on more than one platform.** Must be supported; the same display
  handle on two platforms is two distinct accounts.
- **A configured competitor that has never been collected.** Three of the four exist in this
  state. Must be recorded as a known account with zero performance records, not absent.
- **Follower counts that are unavailable.** Instagram follower counts are currently unobtainable
  for every account. Absence must be recorded as absence, never as zero and never interpolated
  into an observed value.
- **A harvest of a handle that was never registered.** Every item is skipped with a classified
  reason and the profile reports zero collected. Nothing gates the harvest on reconciliation, so
  the run summary is the only place this surfaces — it must read as "handle unregistered", not as
  an ordinary quiet zero, or a sheet edit that never reconciled looks identical to a month with no
  new posts.
- **An account deactivated while its collection target still runs.** Collection targets live
  outside the datastore, so deactivating an account does not stop it being fetched — the content
  is fetched and then discarded at the storage boundary. This spends rate-limited requests on data
  that is thrown away, so reconciliation must report each newly-inactive account loudly enough
  that the target is removed promptly. Reading collection targets from the datastore would close
  this properly and is deliberately out of scope here.
- **The roster changes** — a client is added, renamed, or leaves. Renaming must preserve the
  client's identity and its links; the former name becomes an alternative name.
- **Re-running the load.** Loading the roster twice must not duplicate clients, accounts, or
  links.

## Requirements *(mandatory)*

### Functional Requirements

**Client**

- **FR-001**: The system MUST hold a canonical record for every client on the roster, with a
  stable identifier that does not change when the client's display name changes.
- **FR-002**: The system MUST record alternative names for a client — including the names used by
  the knowledge base and any former roster name — as data that a maintainer can inspect and edit.
- **FR-003**: An alternative name MUST resolve to at most one client. An attempt to attach a name
  already claimed by another client MUST be rejected and reported, never silently resolved.
- **FR-004**: Client lookup by any recorded name MUST be exact and case-insensitive, and MUST NOT
  depend on any similarity or token-overlap computation at query time.
- **FR-005**: A client with no matching name MUST return no client, rather than the closest one.

**Account**

- **FR-006**: The system MUST hold a record for every social account it knows about — a handle on
  a platform — including accounts configured but never collected.
- **FR-007**: The relationship between a client and an account MUST carry an explicit role of
  `owned`, `competitor`, or `reference`, persisted as data rather than derived at run time. The
  role belongs to the relationship, not to the account — the same account can be a competitor of
  one client and a reference for another.
- **FR-008**: An account MUST be relatable to any number of clients, each relationship carrying
  its own role, and MUST be recordable with no client relationship at all. One handle is always
  one account record, however many clients relate to it.
- **FR-009**: An account MUST have at most one `owned` relationship. An attempt to record a
  second client as the owner of the same handle MUST be rejected and reported, never silently
  accepted — a handle has one owner even when many clients watch it.
- **FR-010**: A client and an account MUST NOT have more than one relationship between them, so
  a role is never ambiguous for a given client-account pair.
- **FR-011**: An account MUST be identified by a stable internal identifier that does not change
  when its handle changes. The handle is an attribute of the account, not its identity.
- **FR-011a**: The system MUST record the handles an account has been observed under as a
  history, retaining the form each was observed in, and MUST expose which handle is current.
- **FR-011b**: Recording that a handle replaced a previous one MUST be an explicit maintainer
  action, not an inference. Two handles MUST NOT be merged into one account automatically.
- **FR-011c**: Account lookup by handle MUST be case-insensitive and MUST resolve a previously
  observed handle to the same account, so historical records and links keep resolving after a
  rename.
- **FR-012**: The system MUST distinguish a handle observation that is a real handle from one
  that is only an opaque platform identifier.
- **FR-013**: The system MUST record follower counts as timestamped observations that accumulate
  over time rather than as a single current value, and MUST record the absence of a follower
  count as absence — never as zero, and never as an interpolated value stored beside observed
  ones.
- **FR-013a**: Every collection run MUST write a follower observation for each account it
  collects, whenever the platform returned a count. A count that was not returned MUST result in
  no observation rather than a placeholder row.
- **FR-013b**: Capturing a follower count MUST NOT be able to fail a collection run. It is
  best-effort enrichment, consistent with how the existing collection path treats every other
  side call.

**Linking existing data**

- **FR-014**: Every existing performance record and every existing collection-ledger record MUST
  be linked to exactly one account.
- **FR-015**: All existing columns of the performance store and the collection ledger MUST be
  preserved unchanged, and both of their existing uniqueness rules MUST continue to hold.
- **FR-016**: The write path that creates performance records and ledger records MUST resolve and
  attach the account at write time, so that new records satisfy FR-014 without a later backfill.
- **FR-016a**: The write path MUST NOT create an account. Where no account record exists for a
  collected handle, or where the account exists but is inactive, the item MUST be skipped with a
  classified reason, the remaining items for that profile MUST still be processed, and the skip
  MUST appear in the run's summary — never written unlinked, never attached to a guessed client,
  and never silently dropped.
- **FR-016b**: Accounts MUST be created only by roster reconciliation or by the one-time backfill
  of existing data. Reconciliation is therefore a prerequisite for collecting a handle that the
  system has not seen before.
- **FR-016c**: The two skip reasons — handle unregistered, and account inactive — MUST be
  distinguishable from each other and from an ordinary no-new-content result. They call for
  different operator actions: register the handle, versus remove a collection target that should
  no longer run.
- **FR-017**: Existing knowledge records MUST be linked to a client where a recorded name
  resolves; where none resolves, the record MUST be retained and reported as unattributed.
- **FR-018**: For any performance record, the datastore MUST be able to return every client that
  relates to its account and the role of each relationship, with no spreadsheet access and no
  in-code name matching.

**Execution and brief**

- **FR-019**: The system MUST hold a record for an execution — a collection or a generation —
  with a stable identifier, its kind, the client it was for where applicable, and when it started
  and ended.
- **FR-020**: The system MUST hold a record for a generated content idea, with a stable
  identifier assigned at creation, resolvable to the execution and the client that produced it.
- **FR-021**: All existing flows MUST behave identically when no execution or idea records exist.

**Roster synchronisation**

- **FR-022**: The system MUST be able to reconcile the datastore's client roster, account list,
  and client-account roles from the operator-maintained spreadsheets, and MUST be safe to run
  repeatedly.
- **FR-022a**: Reconciliation MUST run on a daily schedule and MUST also be triggerable on
  demand. Collection and generation MUST NOT wait on it or refuse to start because of it — they
  operate on whatever is registered at the time they run.
- **FR-023**: Reconciliation MUST report, per run, what it added, what it changed, what it could
  not resolve, and why — and MUST NOT silently drop or invent a client, an account, or a role.
- **FR-023a**: Because nothing gates a harvest on reconciliation, a run summary MUST distinguish
  a profile that collected nothing because there was nothing new from one that collected nothing
  because its handle is unregistered. The two are indistinguishable as a bare zero, and only the
  second is a configuration fault.
- **FR-024**: A client, account, or client-account relationship that exists in the datastore but
  no longer in the spreadsheet MUST be marked inactive rather than deleted, so historical records
  keep resolving.
- **FR-024a**: Inactive MUST block storage of new content for that account, per FR-016a. Existing
  records for an inactive account MUST remain readable and MUST keep resolving to their client
  and role — deactivating stops accumulation, it does not hide history.
- **FR-024b**: Reconciliation MUST report every account it newly deactivates, because collection
  targets are configured outside the datastore. Until an operator removes the corresponding
  collection target, that account continues to be fetched and its content discarded.

**Schema evolution**

- **FR-025**: A migration MUST exist for each of the three pre-existing tables, reproducing the
  structure currently in the running database.
- **FR-026**: Every migration in this feature MUST be additive — no column removed, no column
  retyped, no existing constraint loosened or tightened in a way that could reject existing rows.
- **FR-027**: Every migration MUST have a reversal that removes what it introduced without
  affecting data that existed before it.
- **FR-028**: Every migration MUST be safe to apply more than once.
- **FR-029**: Applying all migrations in order to an empty database MUST produce the same
  structure as the first-boot initialisation path, so the two cannot drift.

### Key Entities

- **Client**: A business the agency works for. Has a stable identifier, one canonical display
  name, an active/inactive state, and any number of alternative names. Owns accounts, knowledge
  records, executions, and briefs. 21 exist today, in a spreadsheet.
- **Client Alias**: An alternative name a client is known by, in the knowledge base or a former
  roster entry. Resolves to exactly one client. This is the artefact that turns today's in-code
  matching rule into inspectable data.
- **Account**: One presence on one platform, identified by a stable internal identifier rather
  than by its handle, so a rename does not split it in two. Carries its current handle and whether
  it is still being collected. It does **not** carry a client or a role; those live on the
  relationship below. 22 handles exist across the two harvest tables today; 4 more are configured
  but never collected.
- **Account Handle**: A handle an account has been observed under, retaining the form observed,
  whether it is a real handle or an opaque platform identifier, and whether it is the current one.
  This is what lets a renamed account keep one history, and what lets an old handle in an existing
  record still resolve.
- **Client Account Role**: The relationship between one client and one account, carrying the role
  — owned, competitor, or reference — that currently exists only inside a running flow, plus
  whether the relationship is still active. This is what makes a national chain representable as
  the competitor of several clients at once while remaining a single account with a single stream
  of harvested content. At most one client may hold the `owned` role for a given account, and a
  client-account pair has at most one relationship.
- **Follower Observation**: A timestamped follower count for an account, appended rather than
  overwritten. Sparse by nature — currently obtainable for TikTok only.
- **Run**: One execution of a collection or a generation, with a stable identifier, a kind, an
  optional client, and a start and end. The grouping key that lets a later feature ask what a
  single execution produced or observed.
- **Brief**: One generated content idea, with a stable identifier assigned when it is generated,
  resolving to the run and client that produced it. Introduced here as structure only.
- **Harvested Signal** *(existing, extended)*: Gains a link to Account. All current columns and
  its uniqueness rule are unchanged.
- **Harvested Item** *(existing, extended)*: Gains a link to Account. All current columns and its
  uniqueness rule are unchanged.
- **Knowledge Record** *(existing, extended)*: Gains a link to Client. Its supersession behaviour
  and its current-record uniqueness rule are unchanged.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of the 656 existing performance records resolve to exactly one account, and
  every one of those accounts resolves to at least one client with a stated role — with no manual
  step. On today's data each resolves to exactly one client, since no handle is yet shared.
- **SC-002**: Owned-versus-competitor for any account is answerable directly from the datastore,
  with zero spreadsheet reads and zero name-matching logic at query time — including for an
  account that several clients relate to, where the answer is per client.
- **SC-003**: All 22 handles in the collection ledger are accounted for: each is either related
  to at least one client or explicitly held with no client relationship. Zero handles are dropped,
  and zero are attached to a client that was guessed.
- **SC-003a**: After a handle rename is recorded, both the old and the new handle resolve to the
  same account, and zero performance or ledger records are orphaned onto a second account.
- **SC-004**: All 14 distinct knowledge-base client names resolve to exactly one client each, and
  no client receives records belonging to another — verified specifically for the three Nirwana
  names and the `Lasik Asyik` / `LASIK Asyik by SMEC Tebet` pair.
- **SC-005**: A structure for content briefs exists with stable identifiers and resolves to a
  client and an execution, while holding zero rows and changing no existing behaviour.
- **SC-006**: A database built from migrations alone is structurally identical to a database
  built from the first-boot initialisation path, for all tables including the three that exist
  today.
- **SC-007**: Every migration introduced by this feature can be applied to a copy of the live
  database and reversed, with the pre-existing 62, 656, and 691 rows intact and unchanged after
  both operations.
- **SC-008**: Re-running the roster reconciliation produces no duplicate clients, accounts, or
  roles, and reports every unresolved name and handle rather than dropping it.
- **SC-009**: A maintainer can correct a client name mapping by editing data, with no code change
  and no redeployment.

## Out of Scope

- Populating the brief structure with generated ideas (feature S-06). This feature creates the
  structure and its identifiers only.
- Inferring which published post came from which brief, and any confidence attached to that
  inference (feature S-07).
- Cohort membership, cohort baselines, and cohort-relative ranking (feature S-08). This feature
  records a client-account role, not a cohort. A set of accounts sharing a role for one client is
  not a cohort and must not be treated as one.
- Changing how the generation flow ranks or retrieves; its existing behaviour is preserved, and
  switching it onto the new links is a later change.
- Longitudinal re-observation of post metrics, and any velocity derived from it.
- Moving client administration out of the spreadsheets into an application interface.
- Driving collection targets from the datastore. Targets stay configured outside it, which is why
  deactivating an account stops storage but not fetching (FR-024b).

## Assumptions

- **The spreadsheets remain the operator's editing surface; the datastore becomes the query
  system of record.** The "Clients" and "Hashmaps" worksheets stay where a person adds a client or
  a competitor. A reconciliation step copies that into the datastore, matching the existing
  pattern where the harvest sync flow reconciles reviewer edits back from a sheet. This spec does
  not move client administration out of the spreadsheets.
- **The Nirwana ambiguity is resolved in favour of Pamekasan.** The knowledge records filed under
  `Nirwana Coffee Space` duplicate those under `Nirwana Pamekasan`, and one of them carries the
  subject `Nirwana Coffee Space Pamekasan profile`. `Nirwana Coffee Space` is therefore treated as
  an alias of `Nirwana Coffee Space Pamekasan`, not a third client and not shared brand-level
  knowledge. The Sumenep record describes itself as a branch of the Pamekasan outlet but is a
  separate client on the roster and stays separate.
- **The existing token-subset matching rule informs the initial alias list but is not carried
  forward.** It is used once to propose the mapping, which is then reviewed and stored. After this
  feature, resolution is by recorded alias only.
- **Backfilled roles come from the current spreadsheet configuration.** All 18 collected handles
  become `owned` relationships with their roster client; the 4 handles in the competitor block
  become `competitor` relationships with the client that names them; the two unattributable
  ledger handles become accounts with no client relationship at all. No handle is currently shared
  between clients, so every backfilled account starts with exactly one relationship — the
  many-clients-per-account shape is what the model permits, not what the initial data contains.
- **`jeceyehospital` is treated as a competitor, not as unattributed.** It appears in the
  collection ledger (22 Instagram items, 3 TikTok) with no performance records, and is a
  configured competitor of `LASIK Asyik by SMEC Tebet`.
- **Follower history starts sparse and fills going forward.** It can never be backfilled — a
  follower count is only observable in the present — so capture begins with this feature.
  TikTok already returns a count on every listing. Instagram currently returns none, because the
  only path the collector uses is the endpoint that an upstream fault has been failing for every
  account.
- **No working route to Instagram follower counts is currently known.** A candidate was evaluated
  and probed against the live platform during planning; it did not work. See `research.md` R3 for
  what was tried and ruled out. Nothing in this spec depends on one being found: Instagram
  observations simply stay absent, which FR-013a already treats as a valid recorded state, and the
  structure exists so counts can be captured the moment a route is.
- **Executions and briefs are structure-only in this feature.** Whether the collection and
  generation flows begin writing execution records here, or in the feature that populates briefs,
  is left to planning; either satisfies the success criteria.
- **Existing consumer behaviour is preserved.** The generation flow's knowledge retrieval and
  performance ranking continue to work throughout; adopting the new links in those consumers may
  follow in a later change without breaking this one.
- **Migrations are applied manually by an operator**, matching the existing convention of piping
  a numbered file into the database container. No migration framework is assumed.

## Dependencies

- The "Clients" and "Hashmaps" worksheets remain readable, and remain the source of the roster,
  the owned-handle columns, and the competitor block.
- The existing performance store, collection ledger, and knowledge store keep their current
  columns and uniqueness rules; this feature adds to them and changes nothing they already hold.
- The running database is reachable for the backfill, and a copy of it is available for
  rehearsing the migrations and their reversals.
