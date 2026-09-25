# Grill ledger: Noktah Hub v1 — Client Registry and Client Card with AI intake

**Session**: 2026-09-25 · **Status**: closed 2026-09-25 (shared understanding confirmed by the user)
**Input**: "continue the spec chain first" (grilling the drafted spec 008)
**Owning spec**: new spec 008 (drafted before this session; revised in place) — see G-1

Every row below is traced into spec.md by /speckit-specify, once, as a
requirement, scenario, edge case or assumption — or dropped there with a
reason. Numbers, ordering and negatives are copied verbatim.

## Decisions

### G-1 — What spec 008 covers, and what it leaves out
- **Decision**: Spec 008 covers the client registry, the people list, the Client Card, AI intake from pasted chats or screenshots, and the old AnythingLLM notes. It does **not** cover: switching the automations (harvest, songbird, content-plan → Jira) to read the Hub; putting card facts into Jira tickets and Slack; performance charts, meeting notes, or the Incentive Framework recap. Each of those is its own later spec.
- **Why**: the switch-over changes how running automations behave and deserves its own spec; the rest are separate capabilities.
- **Lands in**: assumption (scope)

### G-2 — Who may change what (first answer, superseded in part by the roles decision)
- **Decision**: Rejected as "every manager edits everything". The user: "Brand Manager is the highest position in a Brand; Project Manager, Account Executive and Sales and Marketing for that Brand report to the Brand Manager. So we need role-based, not just Defila." Access and approval follow ROLES held within a Brand, not a named person. Detailed rules: see the round-2 rows.
- **Why**: the organisation has a reporting line per Brand; approval rights belong to the position, and the Hub will serve more than one person per role over time.
- **Lands in**: requirement (roles and permissions); CONTEXT.md «Brand», «Brand Manager», «Manager»

### G-3 — Branches of the same group
- **Decision**: No group concept in this version. Every branch keeps its own full Client Card, with a **"copy from another client"** action for brand fields. A fact never applies to more than one Client unless a manager explicitly copies it.
- **Why**: simple, and it cannot leak one branch's facts into another by accident (ESKL-11176). Groups can come later if copying becomes a chore.
- **Lands in**: requirement; CONTEXT.md «Client»

### G-4 — Screenshots
- **Decision**: Screenshots are allowed in this version. Every proposal drawn from a screenshot is marked **"dari gambar — cek manual"** and needs a deliberate tick before it can be accepted.
- **Why**: chats often arrive as screenshots, but a screenshot's quote cannot be checked against pasted text without AI.
- **Lands in**: requirement; acceptance scenario

### G-5 — Facts from someone who isn't the PIC
- **Decision**: The proposal is shown flagged **"belum dikonfirmasi PIC"**. A manager can still accept it, but only after ticking **"PIC sudah konfirmasi"**.
- **Why**: only the client's named PIC can change a fact; a staff remark must not slip onto the card unnoticed.
- **Lands in**: requirement; acceptance scenario; CONTEXT.md «PIC»

### G-6 — Monthly AI spending cap for intake
- **Decision**: **USD 5 a month**, separate from the existing extraction budget, with an alert to `#noktah-otomasi` when **80%** is used. When the cap is hit, intake pauses; typing facts in directly still works.
- **Why**: about 40 intakes a month cost cents; the cap only guards against runaway use.
- **Lands in**: requirement; success criterion

### G-7 — The Clients and Hashmaps sheet tabs until the switch-over
- **Decision**: (answered during /speckit-specify on 2026-09-25, recorded here) The Hub is the only place registry data is edited. Until the automations switch over, the Hub keeps the Clients and Hashmaps tabs updated as read-only copies, **within 5 minutes** of each change, marked "don't edit here"; a failed update is retried, then alerted, never silently skipped. A check lists any difference and the registry value wins.
- **Why**: one place to edit from day one, while the automations keep working unchanged.
- **Lands in**: requirement (FR-010, FR-010a); success criterion (SC-009)

### G-8 — "Brand" means the client's; Noktah's business lines are "Noktah Brands"
- **Decision**: Noktah's business lines (Eskala, Venyu) are called **Noktah Brand**. The word **brand** on its own refers to anything client-related: a Client's logo, tagline, tone and banned claims. The Client Card section keeps the name "Brand & Gaya". The role title stays **Brand Manager** (the manager of a Noktah Brand).
- **Why**: "the Brand Manager approves brand fields" must not be ambiguous about whose brand.
- **Lands in**: CONTEXT.md «Noktah Brand», «brand»; requirement wording

### G-9 — Roles in the Hub and what each can do
- **Decision** (accepted as proposed):

  | Role | Scope | Can do |
  |---|---|---|
  | **Owner** | the whole company | Everything, in every Noktah Brand. Appoints Brand Managers. |
  | **Brand Manager** | their Noktah Brand | Everything in their Noktah Brand. **Approves** changes to a Client's brand fields. Assigns roles in their Noktah Brand. |
  | **Project Manager**, **Account Executive** | their Noktah Brand | Edit Clients, teams, social accounts and Client Cards; run Intake. Brand-field changes **wait for the Brand Manager**. |
  | **Sales & Marketing** | their Noktah Brand | **View only**. |

  Staff (Content Planner, Field Associate, Content Editor, QC) are on the people list for team assignments but **cannot sign in**.
- **Why**: follows the reporting line — PM, AE and Sales & Marketing report to the Brand Manager, the highest position in a Noktah Brand.
- **Lands in**: requirement (roles & permissions); CONTEXT.md «Owner», «Brand Manager»

### G-10 — Seeing other Noktah Brands
- **Decision**: Every Client belongs to exactly one Noktah Brand. Managers see and change only their own Noktah Brand's Clients; **only the Owner sees across Noktah Brands**.
- **Why**: Venyu and Eskala are separate lines; nothing in one should be editable from the other.
- **Lands in**: requirement

### G-11 — Roles are internal, keyed by the person's email
- **Decision**: Roles are the Hub's own, held on its people list and matched by the email (account) the person signs in with. **Do not use Cloudflare's list for roles.** Someone signed in whose email holds no Hub role sees "Anda belum punya akses" and nothing else. (Whether Cloudflare's list should still limit who can reach the sign-in at all: see the next round.)
- **Why**: one internal source of who-may-do-what, managed in the Hub.
- **Lands in**: requirement

### G-12 — When there is no Brand Manager to approve
- **Decision**: The **Owner** can approve brand-field changes in any Noktah Brand. Pending changes appear in a **"menunggu persetujuan"** list so nothing waits unseen.
- **Why**: an absent or empty Brand Manager role must not freeze a Client's brand facts.
- **Lands in**: requirement; edge case

### G-13 — The old AnythingLLM notes
- **Decision**: **All** of them are processed by AI and turned into Proposals for review, client by client. Nothing reaches a Client Card without a person accepting it; English notes are shown as they are for rewriting, never machine-translated.
- **Why**: nothing already known should need re-typing, and review keeps the card trustworthy.
- **Lands in**: requirement (FR-028); user story 5

### G-14 — Cloudflare no longer decides who may use the Hub
- **Decision**: Cloudflare's sign-in for `hub.noktah.co` is opened to **anyone who signs in with Google or an email code**; the Hub alone decides access from its roles (G-11). A signed-in person with no role sees only "Anda belum punya akses", and the Hub's API checks roles on every request, so no client data leaves. `automate.noktah.co` (Prefect) **keeps its own tight Cloudflare list**, because it has no roles of its own.
- **Why**: one list of who may do what, maintained in the Hub.
- **Lands in**: requirement; assumption (Cloudflare Access policy change is an ops step)

### G-15 — The first roles
- **Decision**: **Owner: `core@noktah.co`.** **Brand Manager of Venyu: `bagas@noktah.co`.** **Brand Manager of Eskala: `defila@noktah.co`.** Set when the Hub is first installed. (Noted in session: every Owner action is recorded as `core@noktah.co`; that stays meaningful only while one person uses that account.)
- **Why**: the user's choice; `core@noktah.co` is the company's own account.
- **Lands in**: requirement (initial setup); assumption

### G-16 — One person, several emails and roles
- **Decision**: A person can have **several emails**, all counting as the same person, and **several roles**, each within one Noktah Brand. The change history always shows the person, not the email.
- **Why**: staff join tools with personal Gmail as well as @noktah.co addresses, and one person can serve both Noktah Brands.
- **Lands in**: requirement; CONTEXT.md «Person»

### G-17 — Old notes that match no Client
- **Decision**: Notes whose Client can't be matched go to a **"belum ada klien"** list. A Manager picks the Client (or discards the note); it then becomes Proposals like the rest. **Never guessed.**
- **Why**: 34 of the 95 notes aren't linked to any Client today; guessing is how one branch's facts end up on another's card.
- **Lands in**: requirement; edge case

### G-18 — Eskala's own social media is an internal Client
- **Decision**: Eskala is a Client in the Eskala Noktah Brand, marked **internal**, with its own Client Card and social accounts. Internal Clients are **not written to the Clients sheet copy**, so today's automations do not start processing them (out of scope until the switch-over, G-1).
- **Why**: it is already a Client in the database and in Jira; the sheet copy must not change what automations do.
- **Lands in**: requirement; CONTEXT.md «Client»

### G-19 — Sheet and database disagree at import
- **Decision**: The **sheet wins** for names, status, quotas and folders. Existing database records (social accounts, history) are kept and linked to the matching Client. **Every difference is listed** in the import report for a Manager to confirm; **nothing is merged silently**.
- **Why**: the sheet is what people edited and what automations use today; the database holds history worth keeping.
- **Lands in**: requirement (FR-009); edge case

### G-20 — Sheet columns the Hub doesn't manage
- **Decision**: The sheet copy writes **only the columns the Hub manages** and **never touches** the others (e.g. "No.", "Total Minutes Equivalent"), so formulas and extra columns keep working.
- **Why**: the sheet has columns no automation reads and that may be formulas.
- **Lands in**: requirement (FR-010)

### G-21 — Telling the Brand Manager a change is waiting
- **Decision**: When a brand change waits for approval, post a short Slack message to the Noktah Brand's managers' channel: `#eskala-managerial` for Eskala, `#noktah-managerial` for Venyu until it has its own. For example: "Perubahan brand Klinik Mata Sampang menunggu persetujuan: <link>".
- **Why**: nobody should have to go looking for the approval list.
- **Lands in**: requirement

### G-22 — The card's fields (first proposal rejected)
- **Decision**: The 12 fields from config/client_card_v1.yaml are **rejected as outdated**. The user: the card must be able to represent **the client**, **the client's request history**, **the summary**, and **the guideline**. Redesign: see the next round.
- **Why**: the old fields were written for eye clinics, mostly to prevent one kind of caption error; they don't describe the client, record what the client asked for, or hold the guideline.
- **Lands in**: requirement (Client Card structure) — superseded by the redesign rows

### G-23 — The Client Card has four parts
- **Decision**: **Profil** (who the client is; Proposals or typed; valid-until, source, history per fact) · **Guideline** (how the client's content must look and sound, i.e. their brand; every change needs the **Brand Manager's approval**) · **Riwayat Permintaan** (everything the client asked for, dated, with status; Intake pulls requests out of chats and meeting notes as Proposals) · **Ringkasan** (short overview written by AI **from the confirmed parts only**, refreshed on change, marked "dibuat otomatis", **never edited by hand and never a source**). The earlier 4 "brand fields" become the whole Guideline part. **The user added: the brand part must be thorough, grounded in brand theory** — see G-28 onward.
- **Why**: the card must represent the client, their request history, a summary, and the guideline (G-22).
- **Lands in**: requirement (card structure); CONTEXT.md «Client Card», «Profil», «Guideline», «Request», «Summary»

### G-24 — Profil fields
- **Decision**: 10 fields: (1) Nama & penulisan · (2) Tentang usaha · (3) Lokasi, kontak & jam buka — this branch only · (4) Akun & hashtag · (5) Produk & layanan, incl. what is NOT offered · (6) Harga & promo — each line item, price, **unit**, conditions, valid-until · (7) Orang yang tampil — name and title exactly as written, role, schedule, consent · (8) Target audiens · (9) PIC — name, position, number, who may approve content · (10) Aturan produksi & privasi. **Required: 1, 3, 4, 5 and 9.**
- **Why**: fits every industry served (clinics, cafés, gaming lounge, gym, retailer); drawn from what the Brand Manager actually saved in AnythingLLM.
- **Lands in**: requirement

### G-25 — Guideline fields (accepted, then deepened)
- **Decision**: Accepted as the minimum: (1) Positioning & tagline · (2) Pilar & arah konten · (3) Gaya bahasa · (4) Visual · (5) Logo & aset · (6) Wajib ada · (7) Klaim & topik terlarang · (8) Referensi; required 3, 5 and 7. **Deepened by the brand-theory redesign (G-28 onward), which supersedes this list where they differ.**
- **Why**: accepted before the user asked for a theory-grounded brand part.
- **Lands in**: superseded by G-28+

### G-26 — What one request records
- **Decision**: date · what was asked, **word for word** · who asked (PIC or someone else) · where (WhatsApp group, meeting, email) · status **baru → diproses → selesai**, or **ditolak** with a reason · optional link (Jira ticket, Drive file). A request that also changes a fact produces **both** a request entry and a fact Proposal. Turning requests into Jira tickets automatically is **out of scope** (G-1); a Manager can paste the ticket link.
- **Why**: the client's asks must be traceable, and a fact change must still go through the fact Proposal.
- **Lands in**: requirement; key entity

### G-27 — The summary
- **Decision**: Regenerated automatically whenever confirmed facts, the Guideline or open requests change, **at most once a day per Client**; counted in the same **USD 5** monthly cap. Shows who the client is, current promos, key content rules and open requests, with the date it was made. **If the cap is reached, the last summary stays visible, marked as out of date.**
- **Why**: a Manager needs the client at a glance; the summary is a view, never a source.
- **Lands in**: requirement; edge case

### G-28 — The Guideline's ten sections (brand theory)
- **Decision**: (1) **Inti brand** — purpose, vision, mission, values, brand promise in one line (Sinek, Aaker core identity) · (2) **Positioning** — target (linked to Profil), category, **points of difference**, **points of parity**, **reasons to believe**, one-sentence positioning statement, tagline word for word (Keller) · (3) **Identitas (Prism)** — physique, personality, culture, relationship, reflection, self-image (Kapferer) · (4) **Kepribadian & arketipe** — Aaker's 5 traits rated 1–5 (sincerity, excitement, competence, sophistication, ruggedness); **main and supporting archetype** from the 12 (Mark & Pearson) · (5) **Suara** — 4 voice scales 1–5 (serious↔playful, formal↔casual, respectful↔irreverent, enthusiastic↔matter-of-fact), form of address (Anda/kamu/Kak), Bahasa–English mixing, emoji use, headline and selling style, words used and avoided, example lines · (6) **Visual** — logo and approved files, usage rules, logos never to use, colours with codes, fonts, photo/video look, layout and formats · (7) **Pesan & pilar** — 3–5 content pillars, key messages with proof, preferred calls to action · (8) **Wajib ada** — logo placement, disclaimer, contact line, CTA · (9) **Batasan** — always-banned claims ("terbaik", "100%", "pasti sembuh", "dijamin"…) plus the client's own, sensitive topics, competitor-mention rules · (10) **Referensi & pembeda** — the client's guideline documents, examples they love or dislike, **competitors to stand apart from** (linked to the registry's competitor accounts). This supersedes G-25's 8 fields.
- **Why**: the user asked for a thorough brand part grounded in brand theory (G-23).
- **Lands in**: requirement (Guideline structure); key entity

### G-29 — Fixed choices where the theory gives a fixed list
- **Decision**: **Archetype** is chosen from the 12; **Aaker's 5 traits** and the **4 voice scales** are rated **1–5**; everything else is free text. These lists are **versioned** like the extraction vocabulary, so changing one later never reinterprets old values.
- **Why**: consistent across clients, comparable, and usable directly by songbird to shape generated content.
- **Lands in**: requirement

### G-30 — What is required in the Guideline, and completeness
- **Decision**: Required: **Suara**, **Visual** (logo and approved files), **Batasan**, and the **Positioning statement**. Everything else is optional; every card shows a **brand completeness** indicator (e.g. "Guideline 6/10").
- **Why**: most clients have no full brand strategy on day one; requiring all ten would block every card, and the indicator makes the gaps visible to the Brand Manager.
- **Lands in**: requirement; success criterion

### G-31 — Filling the Guideline from the client's own documents
- **Decision**: Intake also accepts a **Google Doc link or a text-based PDF**, reads the text, and proposes Guideline (and Profil) changes like a pasted chat, checked quote by quote. A **scanned PDF counts as images**, like a screenshot, and is marked **"cek manual"**. Every Guideline Proposal still needs the Brand Manager's approval.
- **Why**: many clients already have a brand guideline document.
- **Lands in**: requirement

### G-32 — Nothing is ever deleted from the Hub
- **Decision**: **Never delete.** A Client that leaves is marked **inactive** (filtered out by default, history intact). A wrong fact is **corrected**, and the mistake stays in its history marked **"dikoreksi"**. A wrong request is marked **ditolak** with a reason.
- **Why**: history is what answers "who said this, and when?".
- **Lands in**: requirement; edge case

### G-33 — AnythingLLM is deleted entirely
- **Decision**: **Delete everything related to AnythingLLM** — no archive, no two-week overlap. That covers the AnythingLLM container and its data (workspaces, 307 chats), its route `chat.noktah.co`, the knowledge-base MCP service that exists only to serve it, and its plugin config (which holds a plaintext Google login). The **`knowledge_records` table stays** in the database: it holds the 95 old notes that G-13 processes, and songbird reads it directly. Deletion is a task of this spec, carried out after an explicit go-ahead at that step.
- **Why**: the user's call; the Hub replaces it, and the notes worth keeping are already in the database.
- **Lands in**: requirement; task (with confirmation); assumption (knowledge_records retained)

## Deferred register rows in scope
- none — `docs/DEFERRED.md` was created empty on 2026-09-25

## Facts looked up (not decisions)
- Clients sheet: 22 rows (20 active, 1 inactive, 1 pending); columns No., Name, Folder ID, Content Plan Folder ID, Status, Post, Story, Short Video, Total Minutes Equivalent, Instagram, TikTok.
- Hashmaps: WORKERS 8, COMPONENTS 23, CONTENT_EDITOR 22, FIELD_ASSOCIATE 22, CLIENT_SOCIAL 2 clients. No AE, Content Planner or QC assignments exist anywhere today.
- Database: clients 21, accounts 25 (21 Instagram, 4 TikTok), client_account_roles 22 (18 owned, 4 competitor); knowledge_records 95 current.
- AnythingLLM usage: 307 chats since July 2026; the Brand Manager wrote 255; mostly saving pasted info, lookups, comparisons.
- Database clients (21) include **Eskala** itself (its own social accounts; also a Jira component) and "Nirwana Coffee Space Sumenep", where the sheet says "Nirwana Coffee Shop Sumenep"; SWA and SMEC Pekanbaru are in the sheet but not the database.
- `config/client_card_v1.yaml` (drafted 2026-09-24, never used by any code or data) is superseded by G-23…G-31; the card definition is redrawn, not migrated.
- No automation reads the Clients sheet's "Total Minutes Equivalent" column (grep, 2026-09-25).
- Cloudflare Access: policy "Only managerials" (4 emails) gates hub.noktah.co; login by Google or one-time PIN.
