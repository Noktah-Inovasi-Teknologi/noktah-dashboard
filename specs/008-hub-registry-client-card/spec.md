# Feature Specification: Noktah Hub v1: Registry, Client Card and Intake

**Feature Branch**: `master` (changes reach GitHub through pull requests)

**Created**: 2026-09-25

**Status**: Draft (revised after grilling)

**Input**: User description: "Noktah Hub v1: Client Registry and Client Card with AI intake. Managers-only internal dashboard (hub.noktah.co) that becomes the one home for the Registry (replacing the Clients and Hashmaps sheet tabs) and for each Client's Client Card (replacing AnythingLLM), with AI that distills pasted raw information into Proposals a Manager confirms."

**Grill ledger**: [grill.md](grill.md) · session 2026-09-25 (closed; 33 decisions)

## Inherited & Deferred *(mandatory)*

- Claimed from register: none in scope (`docs/DEFERRED.md` was created empty on 2026-09-25)
- Re-deferred: none
- Deferred by this spec: A-1 (automations read the Registry), H-1 (Client groups), H-2 (Client Card facts and Requests pushed into Jira and Slack). All three were added to `docs/DEFERRED.md`.

## Context

Today the facts Eskala works from are scattered, and the scatter has already cost content.

**The client list lives in four places**: the Clients sheet tab, the Hashmaps sheet tab, the database, and the harvest schedule. Each automation reads a different one. Team assignments are matched to clients by exact spelling, so one typo produces a Jira issue with no assignee.

**Client facts live in AnythingLLM as 95 free-form notes**, filed under inconsistent subjects and in mixed languages. Staff can't look anything up there, and nobody is expected to.

These incidents all happened in September 2026:
- **ESKL-11176**: a caption carried another branch's phone number, address and hashtag.
- **ESKL-11175**: a LASIK price was wrong because the unit and conditions were missing.
- **ESKL-11164/11165**: the wrong logo was used.

Noktah Hub is the Managers' dashboard. This feature makes it:
1. The **one home for the Registry**: Clients, their teams and social accounts.
2. The **one home for each Client's Client Card**, in four parts: the facts (Profil), the brand (Guideline), what the Client asked for (Riwayat Permintaan), and an at-a-glance Summary (Ringkasan).
3. **Organised by roles within each Noktah Brand.**

Entering information is fast: a Manager pastes what the Client said or sent, and AI drafts Proposals for a person to confirm.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Sign in and see only what your role allows (Priority: P1)

Defila, Brand Manager of Eskala, signs in with her Google account and sees Eskala's Clients. A Project Manager signs in and sees the same Clients, but when they change a Client's Guideline, the change waits for Defila. A Sales & Marketing colleague can look but not change. A person who signs in but has no role sees only "Anda belum punya akses". The Owner sees every Noktah Brand.

**Why this priority**: every other story depends on knowing who is acting and what they may do. The Hub holds client data and one person's approval rights, so access must be right before anything else ships.

**Independent Test**: set up one Person per role, sign in as each, and confirm what each can see, change and approve, including a signed-in Person with no role.

**Acceptance Scenarios**:

1. **Given** a Person whose email holds no Hub role, **When** they sign in, **Then** they see only "Anda belum punya akses" and no data at all (G-11).
2. **Given** a Project Manager of Eskala, **When** they open the Hub, **Then** they see only Eskala's Clients, and a Venyu Client's page is refused (G-10).
3. **Given** a Sales & Marketing Person, **When** they open a Client, **Then** they can read everything and change nothing (G-9).
4. **Given** a Person with two emails, **When** they sign in with either one, **Then** they are recognised as the same Person, and the history names the Person (G-16).

---

### User Story 2 - Update a Client from what they said or sent (Priority: P1)

The PIC of a clinic writes in the WhatsApp group: *"Mulai 1 Oktober harga LASIK jadi 9,5 jt per mata ya, promo pelajar tetap. Tolong minggu depan bikin konten promo ini."* An Account Executive copies the message, or screenshots it, into the Client's Intake. Within seconds the Hub shows:
- a **Proposal** to change the price in Harga & promo, with the current and proposed value, the exact sentence it came from, who said it, and when it takes effect;
- a new **Request**: "Tolong minggu depan bikin konten promo ini", status *baru*.

She edits one word, accepts both, and the card updates. The old price stays in the history, and the paste is kept as evidence.

**Why this priority**: wrong facts reaching captions is the recurring, costly failure, and fast confirmed entry is what keeps cards current. This also replaces the only thing AnythingLLM is used for.

**Independent Test**: paste a real chat excerpt containing a price change and a request. Accept both, and confirm the card, the request list, the history and the evidence.

**Acceptance Scenarios**:

1. **Given** a Client with a current price, **When** a Manager submits a chat that changes it, **Then** a Proposal shows the current value, the proposed value word for word, the quoted excerpt, the speaker and the valid-until date, and **nothing is saved until a Manager accepts it**.
2. **Given** a chat containing both a fact change and an ask, **When** it is submitted, **Then** it produces **both** a Request and a fact Proposal (G-26).
3. **Given** a Proposal drawn from a screenshot, **When** it is shown, **Then** it is marked **"dari gambar — cek manual"** and cannot be accepted without a deliberate tick (G-4).
4. **Given** a fact stated by someone other than the Client's PIC, **When** it is proposed, **Then** it is flagged **"belum dikonfirmasi PIC"** and can be accepted only after ticking **"PIC sudah konfirmasi"** (G-5).
5. **Given** a text Proposal whose quoted excerpt is not in the submitted text, **When** it is shown, **Then** it is marked unverified and cannot be accepted as is.
6. **Given** a client's brand guideline as a Google Doc or a text PDF, **When** it is submitted, **Then** Guideline Proposals are made and checked quote by quote. A scanned PDF is marked **"cek manual"** (G-31).
7. **Given** a message with no facts or requests, **When** it is submitted, **Then** the Hub says there is nothing to update.

---

### User Story 3 - Read and maintain a Client Card (Priority: P1)

A Manager opens a Client and sees its Client Card:
- **Profil**: 10 facts, each with its valid-until date and source.
- **Guideline**: the brand, in 10 sections, with a completeness indicator such as "Guideline 6/10".
- **Riwayat Permintaan**: every Request, newest first, with its status.
- **Ringkasan**: a short automatic overview dated when it was made.

Empty required facts are highlighted and expired ones are marked. Any fact can be edited directly, and its full history can be opened.

**Why this priority**: the card is where Intake writes, and what Managers read before briefing content. Without viewing and correcting it, nothing else is usable.

**Independent Test**: open a Client, edit a Profil fact and a Guideline section, change a Request's status, and confirm history, approval state, completeness and the Summary refresh.

**Acceptance Scenarios**:

1. **Given** a Client, **When** a Manager opens it, **Then** the four parts are shown, with the required Profil facts and required Guideline sections highlighted when empty (G-24, G-30).
2. **Given** a fact whose valid-until date has passed, **When** the card is shown, **Then** it is marked expired.
3. **Given** a Project Manager changes the Guideline, **When** saved, **Then** the change waits in "menunggu persetujuan", the card keeps showing the approved value, and the Noktah Brand's managers' Slack channel is told (G-9, G-21).
4. **Given** a wrong fact, **When** a Manager corrects it, **Then** the old value stays in history marked **"dikoreksi"**; nothing is deleted (G-32).
5. **Given** the card changed, **When** the Summary next refreshes (at most once a day per Client), **Then** it reflects only confirmed content and shows its date (G-27).

---

### User Story 4 - Manage the Registry (Priority: P2)

A Manager opens the Client list, filtered by default to active Clients, and edits one Client's details:
- name, status, and monthly quotas per content type
- folders and Jira component
- team: AE, Content Planner, Field Associate, Content Editor and QC, each chosen from the people list
- own and competitor social accounts

Every change is recorded with who and when, and it appears in the Clients and Hashmaps sheet copies within minutes.

**Why this priority**: the Registry underpins later features, which will switch the automations to read it. Today the sheet still works as a stopgap, but from this feature on, the Hub is the only place to edit.

**Independent Test**: change a Client's status, quota, Field Associate and competitor list. Confirm each is recorded in history and appears in the sheet copy within 5 minutes, touching only the Hub's columns.

**Acceptance Scenarios**:

1. **Given** the import has run, **When** a Manager opens the list, **Then** all 22 Clients from the sheet appear, plus Eskala as an internal Client, and every sheet-versus-database difference was listed for confirmation (G-18, G-19).
2. **Given** a team role, **When** a Manager assigns it, **Then** they choose a Person from the list; free text is not accepted.
3. **Given** a Registry change, **When** saved, **Then** the sheet copy shows it within 5 minutes, and columns the Hub doesn't manage are untouched (G-7, G-20).
4. **Given** a Client that leaves, **When** a Manager marks it inactive, **Then** it disappears from the default list but stays viewable with its history (G-32).

---

### User Story 5 - Manage people and roles (Priority: P2)

A Brand Manager adds a new Account Executive with their work and personal emails, and gives them the AE role in their Noktah Brand. The Owner appoints a Brand Manager for a Noktah Brand. A Person who leaves can no longer be assigned or sign in, while past records still name them.

**Why this priority**: roles are internal to the Hub (G-11), so someone must be able to grant them. Team assignments also depend on the people list.

**Independent Test**: add a Person with two emails and a role, sign in with each email, then mark the Person as left and confirm they can't sign in or be assigned, while history still shows them.

**Acceptance Scenarios**:

1. **Given** a Brand Manager, **When** they grant a role, **Then** they can grant roles only within their own Noktah Brand, and cannot appoint a Brand Manager; only the Owner can (G-9).
2. **Given** an email already belonging to another Person, **When** a Manager adds it, **Then** the Hub refuses and points to that Person.

---

### User Story 6 - Bring the old notes in (Priority: P3)

All 95 old notes are processed by AI into Proposals, Client by Client. The Brand Manager reviews them like any Intake. Notes whose Client can't be matched wait in a "belum ada klien" list, where a Manager picks the Client or discards the note.

**Why this priority**: it saves re-typing what is already known. Cards are usable without it, and Intake fills them naturally.

**Independent Test**: run the processing, then confirm every note became Proposals or a "belum ada klien" entry, and that nothing reached a card without acceptance.

**Acceptance Scenarios**:

1. **Given** the 95 notes, **When** processed, **Then** every note produces Proposals or a "belum ada klien" entry, and none is written to a card automatically (G-13, G-17).
2. **Given** a note in English, **When** proposed, **Then** it is shown as stored, for rewriting, and **never machine-translated** (G-13).

---

### Edge Cases

- **A fact that names another branch or Client** (ESKL-11176): the Proposal is flagged. A fact never applies to more than one Client unless a Manager explicitly uses "copy from another client" (G-3).
- **A price without its unit or conditions** (ESKL-11175): the Proposal is flagged incomplete and asks for the missing part.
- **Contradictions**, within one paste or against the current card: both values are shown side by side for the Manager to choose.
- **No Brand Manager to approve** (absent, or the role is empty): the Owner can approve in any Noktah Brand, and pending changes stay visible in "menunggu persetujuan" (G-12).
- **Monthly AI cap reached**: Intake and Summary refresh pause. The last Summary stays visible, marked out of date. Direct editing still works (G-6, G-27).
- **The sheet copy fails to update**: it is retried, then alerted, and **never silently skipped** (G-7).
- **Someone edits the sheet copy despite the note**: the check lists the difference, and the Registry value wins (G-7).
- **An unreadable screenshot or scanned PDF**: the Hub says so and asks for text; it never guesses.
- **The office PC or tunnel is down**: pages load with a clear Bahasa message, and no change appears saved that wasn't.
- **The AI fails, times out or returns invalid output**: invalid output is retried once with the validation error, then recorded as a failed Intake; it never reaches the card.
- **Two Managers edit the same thing at once**: the second save is refused with the newer value shown, rather than overwriting it.
- **Patient data in a pasted chat**: never proposed as a card fact, and the raw paste holding it is removed after 12 months, leaving only the quoted excerpts (FR-036).
- **An old note with no card home** (e.g. monthly performance numbers): listed as "tidak masuk kartu" rather than forced into a field.

## Requirements *(mandatory)*

### Functional Requirements

**Access and roles**

- **FR-001**: Hub access and permissions MUST follow **roles held within a Noktah Brand**, not named individuals (G-2).
- **FR-002**: Roles MUST be the Hub's own, held on its people list and matched by the email a Person signs in with. The external sign-in service MUST NOT be used for roles. A signed-in Person with no role MUST see only "Anda belum punya akses" (G-11).
- **FR-003**: The external sign-in MUST accept **anyone who signs in with Google or an email code**; the Hub alone decides access. Every data request MUST be checked against the Person's roles, so no Client data reaches a Person without a role (G-14).
- **FR-004**: The roles and their rights MUST be (G-9):

  | Role | Scope | Can do |
  |---|---|---|
  | **Owner** | the whole company | Everything, in every Noktah Brand. Appoints Brand Managers. |
  | **Brand Manager** | their Noktah Brand | Everything in their Noktah Brand. **Approves** changes to a Client's Guideline. Assigns roles in their Noktah Brand. |
  | **Project Manager**, **Account Executive** | their Noktah Brand | Edit Clients, teams, social accounts and Client Cards; run Intake. Guideline changes **wait for the Brand Manager**. |
  | **Sales & Marketing** | their Noktah Brand | **View only**. |

  Staff (Content Planner, Field Associate, Content Editor, QC) MUST be on the people list for team assignments, and MUST NOT be able to sign in.
- **FR-005**: Every Client MUST belong to exactly one Noktah Brand. Managers MUST see and change only their own Noktah Brand's Clients; **only the Owner sees across Noktah Brands** (G-10).
- **FR-006**: The first roles MUST be set at installation: **Owner: `core@noktah.co`**; **Brand Manager of Venyu: `bagas@noktah.co`**; **Brand Manager of Eskala: `defila@noktah.co`** (G-15).
- **FR-007**: A Person MUST be able to have **several emails**, all counting as the same Person, and **several roles**, each within one Noktah Brand. The change history MUST always show the Person, not the email (G-16).
- **FR-008**: The Owner MUST be able to approve Guideline changes in any Noktah Brand. Pending changes MUST appear in a **"menunggu persetujuan"** list (G-12).
- **FR-009**: When a Guideline change waits for approval, a short message MUST be posted to that Noktah Brand's managers' Slack channel, with a link. The channel is `#eskala-managerial` for Eskala, and `#noktah-managerial` for Venyu until it has its own (G-21).

**Registry**

- **FR-010**: Managers MUST be able to view Clients (active by default) and edit a Client's name, status (pending, active, inactive), monthly quota per content type (Post, Story, Short Video), folders, and Jira component.
- **FR-011**: Managers MUST be able to assign each team role (Account Executive, Content Planner, Field Associate, Content Editor, QC) by choosing a Person; free text MUST NOT be accepted.
- **FR-012**: Managers MUST be able to add, change and deactivate a Client's social accounts: platform, handle, and own or competitor. One account MAY be linked to several Clients.
- **FR-013**: Every Registry and people change MUST be recorded with the old value, new value, Person, and time.
- **FR-014**: The Registry MUST be imported once from the Clients and Hashmaps sheet tabs. The **sheet wins** for names, status, quotas and folders. Existing database records (social accounts, history) MUST be kept and linked to the matching Client. **Every difference MUST be listed** for a Manager to confirm; **nothing is merged silently** (G-19).
- **FR-015**: Eskala MUST be a Client in the Eskala Noktah Brand, marked **internal**, with its own Client Card and social accounts. Internal Clients MUST NOT be written to the Clients sheet copy (G-18).
- **FR-016**: After import, the Hub MUST be the only place Registry data is edited. Until the automations switch over, the Hub MUST keep the Clients and Hashmaps tabs updated as read-only copies **within 5 minutes** of each change, marked as maintained by the Hub with a note not to edit them there. A failed update MUST be retried, then alerted, and **never silently skipped** (G-7).
- **FR-017**: The sheet copy MUST write **only the columns the Hub manages** and **never touch** the others, such as "No." and "Total Minutes Equivalent" (G-20).
- **FR-018**: The Hub MUST offer a check that lists every difference between the sheet copies and the Registry. The Registry value wins, and the check reports what it overwrote (G-7).

**Client Card**

- **FR-019**: Each Client MUST have a Client Card in four parts: **Profil**, **Guideline**, **Riwayat Permintaan** and **Ringkasan** (G-23).
- **FR-020**: **Profil** MUST have these 10 facts (G-24):
  1. Nama & penulisan
  2. Tentang usaha
  3. Lokasi, kontak & jam buka (this branch only)
  4. Akun & hashtag
  5. Produk & layanan, including what is NOT offered
  6. Harga & promo: each line has item, price, **unit**, conditions and valid-until
  7. Orang yang tampil: name and title exactly as written, role, schedule and consent
  8. Target audiens
  9. PIC: name, position, number, and who may approve content
  10. Aturan produksi & privasi

  **Required: 1, 3, 4, 5 and 9.**
- **FR-021**: **Guideline** MUST have these 10 sections, grounded in brand theory (G-28):
  1. **Inti brand**: purpose, vision, mission, values, and the brand promise in one line
  2. **Positioning**: target (linked to Profil), category, **points of difference**, **points of parity**, **reasons to believe**, a one-sentence positioning statement, and the tagline word for word
  3. **Identitas (Prism)**: physique, personality, culture, relationship, reflection, self-image
  4. **Kepribadian & arketipe**: 5 personality traits rated 1–5 (sincerity, excitement, competence, sophistication, ruggedness), and a **main and supporting archetype** from the 12
  5. **Suara**: 4 voice scales rated 1–5 (serious↔playful, formal↔casual, respectful↔irreverent, enthusiastic↔matter-of-fact); form of address (Anda/kamu/Kak); Bahasa–English mixing; emoji use; headline and selling style; words used and avoided; example lines
  6. **Visual**: logo and approved files, usage rules, logos never to use, colours with codes, fonts, photo and video look, layout and formats
  7. **Pesan & pilar**: 3–5 content pillars, key messages with proof, preferred calls to action
  8. **Wajib ada**: logo placement, disclaimer, contact line, call to action
  9. **Batasan**: always-banned claims ("terbaik", "100%", "pasti sembuh", "dijamin"…) plus the Client's own, sensitive topics, and competitor-mention rules
  10. **Referensi & pembeda**: the Client's guideline documents, examples they love or dislike, and **competitors to stand apart from**, linked to the Registry's competitor accounts
- **FR-022**: Archetype MUST be chosen from the 12, and personality traits and voice scales MUST be rated 1–5; everything else is free text. These lists MUST be **versioned**, so changing one later **never reinterprets old values** (G-29).
- **FR-023**: Guideline required sections MUST be **Suara**, **Visual** (logo and approved files), **Batasan**, and the **Positioning statement**; the rest are optional. Every card MUST show a **brand completeness** indicator, e.g. "Guideline 6/10" (G-30).
- **FR-024**: Every Profil fact and Guideline section MUST carry a valid-until date (or "none") and a source (who, where, when). It MUST be stored word for word as entered or accepted, in the language given, **never machine-translated**.
- **FR-025**: Every change to Profil or Guideline MUST keep all earlier values, with their period, Person and source.
- **FR-026**: A **Request** MUST record (G-26):
  - its date
  - what was asked, **word for word**
  - who asked (the PIC or someone else)
  - where: WhatsApp group, meeting or email
  - status: **baru → diproses → selesai**, or **ditolak** with a reason
  - an optional link, such as a Jira ticket or Drive file

  A Request that also changes a fact MUST produce **both** a Request and a fact Proposal.
- **FR-027**: The **Summary** MUST be written by AI **from the confirmed parts only**. It is regenerated automatically when confirmed facts, the Guideline or open Requests change, **at most once a day per Client**, and shows who the Client is, current promos, key content rules and open Requests, with the date it was made. It MUST be marked "dibuat otomatis" and MUST be **never edited by hand and never a source**. **If the cap is reached, the last Summary stays visible, marked as out of date** (G-27).
- **FR-028**: Each branch MUST have its own Client Card. A fact MUST NOT apply to more than one Client unless a Manager explicitly uses **"copy from another client"**, which is available for Guideline sections (G-3).
- **FR-029**: **Nothing MUST ever be deleted**, except raw Intake material after 12 months (FR-036). A Client that leaves is marked **inactive**. A wrong fact is **corrected**, with the mistake kept in history marked **"dikoreksi"**. A wrong Request is marked **ditolak** with a reason (G-32).

**Intake**

- **FR-030**: Managers allowed to edit MUST be able to submit, for one chosen Client: pasted text, a screenshot image, a Google Doc link, or a PDF (G-4, G-31).
- **FR-031**: The Hub MUST return Proposals for Profil facts, Guideline sections and Requests. Each names its target, gives the proposed value word for word, a valid-until date if stated, the speaker and date if identifiable, and **the exact excerpt it came from**.
- **FR-032**: For text input (pasted text, Google Docs, text PDFs), each excerpt MUST be checked **without AI** to appear in the submitted text. A Proposal that fails the check MUST be marked unverified.
- **FR-033**: A Proposal from a screenshot or scanned PDF MUST be marked **"dari gambar — cek manual"** (**"cek manual"** for scanned PDFs) and MUST need a deliberate tick before acceptance (G-4, G-31).
- **FR-034**: A Proposal whose speaker isn't the Client's named PIC MUST be flagged **"belum dikonfirmasi PIC"**, and MUST be accepted only after ticking **"PIC sudah konfirmasi"** (G-5). Proposals MUST also be flagged when a price lacks its unit or conditions, when the text names another Client or branch, or when it contradicts the card.
- **FR-035**: Nothing from Intake MUST reach a card until a Manager accepts it. Each Proposal is accepted (as is or edited) or rejected individually, and Guideline Proposals then follow FR-004/FR-008.
- **FR-036**: Every submission MUST be kept as evidence with its Proposals and each outcome, linked from the values it produced. The **raw material** (pasted text, screenshot, document) MUST be kept for **12 months**, then removed. The **quoted excerpts** that became Proposals, the Proposals and their outcomes MUST be kept forever, so the history of every value stays traceable.
- **FR-037**: AI output MUST be validated before it is shown. Invalid output is retried once with the validation error, then recorded as failed, and never shown as Proposals.
- **FR-038**: AI spend for Intake and Summaries MUST be capped at **USD 5 a month**, separate from the extraction budget, with an alert to `#noktah-otomasi` when **80%** is used. At the cap, Intake pauses; direct editing still works (G-6).
- **FR-039**: Intake MUST NOT propose patient-identifying information (patient names, diagnoses, images of patients) as card content.

**Old notes**

- **FR-040**: **All** 95 existing notes MUST be processed by AI into Proposals for review, Client by Client. Nothing reaches a card without acceptance, and English notes are shown as stored and **never machine-translated** (G-13).
- **FR-041**: Notes whose Client can't be matched MUST go to a **"belum ada klien"** list. A Manager picks the Client or discards the note; the Client is **never guessed** (G-17).

**Retiring AnythingLLM**

- **FR-042**: Everything related to AnythingLLM MUST be removed, **with no archive**: its service and data (workspaces, 307 chats), its public address `chat.noktah.co`, the connector service that exists only to serve it, and its plugin configuration, which holds a plaintext Google login. The old notes' database table MUST be kept, because it is the source for FR-040 and songbird reads it. Removal MUST happen only after an explicit go-ahead at that step (G-33).

**Reliability**

- **FR-043**: When the Hub can't reach its data, it MUST say so in Bahasa, and MUST NOT appear to save a change that wasn't saved.
- **FR-044**: Concurrent edits to the same thing MUST NOT silently overwrite each other.
- **FR-045**: The Hub's text MUST be in Bahasa Indonesia. "Brand" in the interface MUST refer to the Client's brand; Noktah's business lines are called **Noktah Brand** (G-8).

### Key Entities *(names from CONTEXT.md)*

- **Noktah Brand**: Eskala or Venyu. Has one Brand Manager and owns its Clients.
- **Person**: someone on the people list. Has one or more emails, active or left, and zero or more roles, each tied to one Noktah Brand, except Owner, which covers the whole company.
- **Role assignment**: a Person, a role (Owner, Brand Manager, Project Manager, Account Executive, Sales & Marketing, or a staff role), a Noktah Brand, and the period it applied.
- **Client**: belongs to one Noktah Brand. Has name, status, internal flag, quotas, folders, Jira component, team, social accounts, and one Client Card.
- **Team assignment**: a Client, a team role, a Person, and the period it applied.
- **Social account**: platform and handle, with its handle history. Linked to Clients as own or competitor.
- **Client Card**:
  - Profil facts and Guideline sections: each value has valid-until, source, Person, time, correction mark and, for the Guideline, Approval state
  - Requests
  - the Summary: generated, dated, possibly out of date
- **Card definition**: the versioned list of Profil facts and Guideline sections, with their required flags, plus the versioned choice lists (the 12 archetypes, personality traits, voice scales).
- **Intake**: one submission for one Client by one Person. Holds the raw input and its kind (text, screenshot, document), the Proposals, their check results and flags, each outcome, and the AI cost and model used.
- **Proposal**: a suggested change to one Profil fact, one Guideline section, or a new Request, with the excerpt it came from.
- **Approval**: the Brand Manager's or Owner's decision on a pending Guideline change.
- **Change record**: who changed what in the Registry or the people list, from which value to which, and when.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A Manager can turn a pasted Client message into a confirmed card update in under 2 minutes, from paste to saved.
- **SC-002**: For 100% of card values, including imported ones, the Hub can answer who set it, when, from what source, and what it replaced.
- **SC-003**: Zero values reach a card without a named Person's acceptance or direct entry, and zero Guideline changes take effect without an Approval.
- **SC-004**: On a test set of pastes that includes fabricated quotes, 100% of those Proposals are flagged unverified.
- **SC-005**: After import, all 22 Clients plus Eskala (internal) exist. Their statuses, quotas, folders, Jira components, Content Editor and Field Associate assignments, and own and competitor accounts match the sheet, and every difference is listed with its resolution.
- **SC-006**: Within one month of launch, required Profil facts are filled for at least 80% of active Clients, and every active Client shows a brand completeness score.
- **SC-007**: Intake and Summary AI spend stays under **USD 5 a month**, the 80% alert fires when crossed, and the average cost per Intake is reported.
- **SC-008**: Until the automations switch over, every Registry change appears in the sheet copies within 5 minutes, and a weekly comparison finds zero unexplained differences.
- **SC-009**: A signed-in Person without a role reaches zero Client data, verified by trying every page and data request as such a Person.
- **SC-010**: AnythingLLM and everything that served only it are gone. The Hub covers every use found in its chat history (saving facts, lookups, change history).

## Clarifications

### Session 2026-09-25

- Q: Where do Managers edit Clients before the automations switch over to the Registry? → A: Only in the Hub. The Hub keeps the sheet tabs updated as read-only copies (FR-016 to FR-018; G-7).
- Grill session 2026-09-25 (33 decisions, [grill.md](grill.md)) revised this draft:
  - access became role-based per Noktah Brand, with roles held internally
  - the Client Card became Profil, a brand-theory Guideline, Requests and a Summary
  - Intake gained documents, and flags for screenshots and non-PIC facts
  - nothing is ever deleted
  - AnythingLLM is removed entirely
- Q: How long is the raw pasted material kept? → A: 12 months, then only the quoted excerpts that became Proposals are kept (FR-036).

## Assumptions

- **Scope** (G-1): this spec covers the Registry, the people list and roles, the Client Card, Intake, and the old notes. It does **not** cover:
  - switching the automations (harvest, songbird, content-plan → Jira) to read the Hub
  - putting Client Card facts or Requests into Jira tickets and Slack
  - performance charts, meeting notes, or the Incentive Framework recap

  These are deferred as A-1 and H-2. Client groups are deferred as H-1 (G-3).
- **Card design history**: the 12 fields first drafted in `config/client_card_v1.yaml` were **rejected as outdated** (G-22) and replaced by the four-part card. The intermediate 8-field Guideline (G-25) is **superseded** by the 10 sections of G-28. No data was ever stored under the old design.
- **Terminology** (G-8): the Client Card part is called "Guideline" in the glossary, and the interface may title its section "Brand & Gaya". "Brand" in the interface always means the Client's.
- **Sign-in**: Cloudflare Access proves identity (Google or email code). Opening `hub.noktah.co` to any signed-in email is an ops step in this feature. `automate.noktah.co` (Prefect) **keeps its own tight list**, because it has no roles of its own (G-14).
- **`core@noktah.co`** is the company's own account. Every Owner action is recorded under it, which stays meaningful only while one person uses it (G-15).
- **Existing data**: the database holds 21 clients, 25 social accounts and 95 current old notes. 34 notes aren't linked to any Client. The sheet says "Nirwana Coffee Shop Sumenep" where the database says "…Space…", and SWA and SMEC Pekanbaru are missing from the database; the import lists all of these (G-19).
- **Architecture** (decided earlier; recorded for planning):
  - The Hub web app never touches the database or the AI provider. One data service is the only writer, reached through the tunnel with a service token and the Person's verified sign-in.
  - That service is request/response, not a Prefect flow, following the knowledge-base service's precedent.
  - Recurring work runs as Prefect flows: the sheet copy, the Summary refresh and the old-notes processing.
- **Constitution note**: the constitution places static mappings (workers, components, role assignments) in `hashmap.py`. This feature copies them into the Registry and keeps the sheet (the hashmaps' source) updated. Retiring `hashmap.py` as their source belongs to the switch-over spec (A-1), which will need a constitution amendment.
- **Cost**: at about 40 Intakes a month, mostly text, plus daily-at-most Summaries for about 23 Clients, AI spend is expected to be well under USD 1 a month. The cap protects against runaway use.
