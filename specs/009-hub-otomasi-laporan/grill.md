# Grill ledger: Noktah Hub — Otomasi & Laporan

**Session**: 2026-09-26 · **Status**: closed 2026-09-26 (shared understanding confirmed by the user)
**Input**: "Noktah Hub Otomasi & Laporan, spec 009. Read specs/009-hub-otomasi-laporan/brief.md first; its decisions are settled."
**Owning spec**: new spec 009 — see G-1

Every row below is traced into spec.md by /speckit-specify, once, as a
requirement, scenario, edge case or assumption — or dropped there with a
reason. Numbers, ordering and negatives are copied verbatim.

The decisions in `brief.md` (dated 2026-09-26) are settled inputs, not re-asked here; spec.md
traces them too.

## Decisions

### G-1 — Which spec owns this, and what it leaves out
- **Decision**: New spec 009, extending spec 008 (the Hub), the content-plan → Jira automation and spec 002 (harvest). In: the Otomasi menu (Jira automation and harvest), approving a Content Plan and creating its Jira issues in batches, harvest review in the Hub, one Registry-driven harvest job, and the Laporan menu (delivery and performance reports). Out: songbird; any change to how roach collects posts; editing or moving Jira issues from the Hub (the Hub creates issues and reads their status, nothing more; see G-9 for the one exception still open).
- **Why**: Accepted recommendation; keeps the Jira board the one place production work moves.
- **Lands in**: requirement (scope) + assumption (out of scope)

### G-2 — Deferred rows A-1, H-2, S-1
- **Decision**: (a) A-1 claimed for the Jira automation and the harvest only. Songbird keeps reading the Clients/Hashmaps sheets, so the Registry → sheet copy keeps running until songbird switches. (b) H-2 re-deferred: this spec is about when and how issues get created, not what they say. (c) S-1 is already done (PR #13, songbird reads the Client Card); the row is deleted.
- **Why**: Accepted recommendation.
- **Lands in**: requirement (A-1 part) + assumption (sheet copy keeps running)

### G-3 — Who sees Laporan
- **Decision**: A new permission "Lihat laporan", ticked per person like "Kelola otomasi". On by default for Owner and Brand Managers.
- **Why**: Accepted recommendation; per-client filtering can come later if Account Executives need it.
- **Lands in**: requirement

### G-4 — Published, late, cancelled, published late
- **Decision**: **Published** = the issue reached *Published, Need Review* or *Done*. **Late** = the publication date has passed and the issue is not published. **Shelved** issues count separately as cancelled, never as late. **Published late** is also reported: published, but after its publication date, using the day Jira's status history shows it reaching a published status.
- **Why**: Without "published late", a post two weeks late looks the same as one on time.
- **Lands in**: CONTEXT.md «Published», «Late» + requirement

### G-5 — A Content Plan that changes after approval
- **Decision**: Before issues are created, the Hub notices the plan changed since approval, shows it as "Berubah sejak disetujui", and it must be approved again. After issues are created, pressing "Buat issue Jira" again creates issues only for rows that don't have one yet; it **never** duplicates. A row already turned into an issue is not updated in Jira by that press.
- **Why**: Accepted recommendation; also removes today's double-run risk (running the flow twice creates every issue twice).
- **Lands in**: requirement + edge case

### G-6 — Harvest review in the Hub
- **Decision**: A list of harvested posts per account, filtered by month, with link, caption, numbers and review marks. Anyone with "Kelola otomasi" can set them. Marks are **multiple choice** (a post may carry several): **Iklan**, plus two more the user accepted: off-topic and not the account's own post (exact labels and effects: next round). Default: no mark. Ad flags already set in the Account Social Harvest sheets are brought over once; after that, the sheets stop being written.
- **Why**: Accepted recommendation, widened by the user to three marks.
- **Lands in**: requirement

### G-7 — How the harvest runs from the Registry
- **Decision**: One monthly job goes through every social account (own and competitor) of every active Client, one at a time, with today's spacing (30 minutes apart) and today's 31-day window. A new account gets 90 days on its first harvest automatically. "Jalankan sekarang" in Otomasi runs one account on demand. A Client marked inactive stops being harvested. **Any change to a Client or competitor in the Registry changes the harvest accordingly**: an added account is harvested from the next run, a removed one is not, with no schedule to edit.
- **Why**: Accepted recommendation; the user added that Registry changes must flow through by themselves.
- **Lands in**: requirement + scenario

### G-8 — The Jira issue key is written back into the Content Plan
- **Decision**: After an issue is created, its key goes into the Content Plan row it came from. (User's note on Q1: clients sometimes add or change rows after issues exist, so the row must carry its issue.)
- **Why**: User requirement; it is also how G-5 knows which rows already have an issue.
- **Lands in**: requirement

### G-9 — Watching a Content Plan for changes after issues exist
- **Decision**: There is a watcher on each Content Plan that has issues, which notices when the plan is modified. What counts as a change, how quickly it is noticed, and what happens next are open (round 2).
- **Why**: User requirement on Q1.
- **Lands in**: requirement (details pending)

### G-10 — The delivery report goes down to stations and people
- **Decision**: Beyond planned / created / published / late, the report shows detailed performance of each **station**, each individual and each team: input and output, how many times work went forward and back, etc. Definitions are open (round 2).
- **Why**: User requirement on Q4.
- **Lands in**: requirement (details pending)

### G-11 — The word for approving a Content Plan
- **Decision**: **Greenlight**. A Content Plan is greenlit by a Manager before its issues can be created; the Hub button reads "Greenlight". "Approval" stays with Guideline changes, and the plan's per-row Approval column is untouched. (Supersedes the wording "Setujui" in brief.md.)
- **Why**: "Approval" already means the Brand Manager's decision on a Guideline change; one word for three things confuses.
- **Lands in**: CONTEXT.md «Greenlight»

### G-12 — Which plan column gets the issue
- **Decision**: The issue key (as a link) goes into **Key** only. **TicketID** is not used: it was meant for the Jira issue too, so it is redundant. The Hub also keeps its own record of which row made which issue; an erased or duplicated Key is flagged for a person to fix, and **never** causes a second issue.
- **Why**: User: "TicketID supposed to be for jira issue. but if it's redundant dont use it."
- **Lands in**: requirement + edge case

### G-13 — What the watcher does (details of G-9)
- **Decision**: Option A. Every **15 minutes** the Hub checks Content Plans that have issues. A changed row is flagged "berubah setelah issue dibuat" with what changed (old → new), and a **comment** listing the change is posted on its Jira issue. The issue's fields are **not** changed; a person updates them in Jira. A row deleted from the plan is flagged "dihapus dari plan"; its issue is **never** shelved automatically. New rows without a Key wait for the next "Buat issue Jira". This amends G-1: the Hub may add comments to issues, never change their fields.
- **Why**: Accepted recommendation. The user asked whether it costs money: it does not (Google and Jira API calls are free within quota; no AI is involved).
- **Lands in**: requirement + edge case

### G-14 — Which Jira status belongs to which station
- **Decision**: Plan → Content Planner. Footages in Progress → Field Associate. Footage Taken, Designs in Progress → Content Editor. Designs in Review → Quality Assurance. Internally Reviewed, In Review by Client, Reviewed by Client, Scheduled → Field Associate (client review is its own station, handled by the Field Associate until *Published, Need Review*). Published, Need Review → Quality Assurance. (User's corrections to the proposal: Footage Taken is the Content Editor's; Internally Reviewed is the Field Associate's; Published, Need Review is Quality Assurance's too.) How stations connect to roles and people: next round, the user found it confusing.
- **Why**: User's mapping.
- **Lands in**: CONTEXT.md «Station» + requirement

### G-15 — Returns, and Events judged by managers
- **Decision**: A **return** (content sent back to an earlier station) means something failed to be approved. It is counted, but whether it is charged to anyone depends on its nature: some returns are Event-worthy, some are not. The Hub **never** charges a return by itself. Events are judged by managers (Manajerial), with one or more categories of Excellence or Violation; once an Event is judged, the automation computes its points.
- **Why**: User's answer on Q13.
- **Lands in**: CONTEXT.md «Return», «Event» + requirement

### G-16 — How far back the reports go
- **Decision**: Jira history is pulled into the Hub from **1 January 2026**. The performance report uses every stored harvest.
- **Why**: Accepted recommendation.
- **Lands in**: requirement

### G-17 — The three review marks (details of G-6)
- **Decision**: **Iklan** (paid or boosted), **Tidak relevan** (off-topic, e.g. a holiday greeting or giveaway), **Bukan konten akun ini** (repost, collab, someone else's content). A post with any mark is left out of the performance report's averages and top posts and out of songbird's examples; it still counts in "how many posts this month", stays stored, and is shown in a separate collapsed list in the report.
- **Why**: Accepted recommendation.
- **Lands in**: requirement

### G-18 — Seeing individual numbers
- **Decision**: "Lihat laporan" covers per-person numbers too. On by default for Owner and Brand Managers, tickable for Production Managers. No "see only my own" view (staff don't sign in to the Hub).
- **Why**: Accepted recommendation.
- **Lands in**: requirement

### G-19 — Stations, roles and people
- **Decision**: Five stations, named by the Incentive Framework's letters. **A Planning**: Content Planner; status Plan; the person comes from the Client's Team. **B Footage**: Field Associate; Footages in Progress; the issue's Field Associate. **C Editing**: Content Editor; Footage Taken, Designs in Progress; the issue's Content Editor. **D Quality control**: Quality Assurance; Designs in Review, Published, Need Review; the Client's Team. **E Client & publication**: Field Associate; Internally Reviewed, In Review by Client, Reviewed by Client, Scheduled; the issue's Field Associate. A team is the Client's Team, so every number also rolls up per Client. The Jira assignee is **not** used to find people. (Resolves Q12 from round 2.)
- **Why**: Accepted recommendation; the Framework's category letters and the station numbers line up.
- **Lands in**: requirement + CONTEXT.md «Station» (sharpened)

### G-20 — How an Event is judged in Jira today
- **Decision**: *Judged* is a status. On the transition to Judged a screen appears for the judge with **Error Category**, **Reporter's Own Mistake** and **Problem/Error Solved** together. (The user's description of today's setup; what the Hub computes from it is settled next round.)
- **Why**: User's answer on Q18.
- **Lands in**: assumption

### G-21 — What the Hub shows from the Incentive Framework
- **Decision**: Per person per month: Violation points and Excellence points (never netted), the sanction tier from table 3.2, and the Events behind them. Per Client team per month: client first-pass rate, average QC rounds, and whether both thresholds are met. Per person: whether they qualify for the Rp100.000 team reward (at least 60% of their teams meet both). Content with **more than 3 rounds between two stations** is flagged for managerial review, not charged. Out: rupiah amounts for Tunjangan (salaries are not in the Hub) and reward payouts. The Hub prepares the monthly rekap (7.1 step 2); managers still make the penetapan (step 3).
- **Why**: Accepted recommendation.
- **Lands in**: requirement

### G-22 — The amnesty period
- **Decision**: The amnesty (Framework section 8) is the **first 30 days of a newly onboarded staff member**, not a calendar month for everyone.
- **Why**: User's answer on Q20.
- **Lands in**: requirement (details next round: where the start date comes from)

### G-23 — Who sees points and sanction tiers
- **Decision**: A separate permission **"Lihat poin insentif"**, on only for Owner and Brand Managers. "Lihat laporan" shows stations, returns and team thresholds, but no one's points or sanctions.
- **Why**: Points and sanctions are personnel matters, delivered privately under the Framework.
- **Lands in**: requirement

### G-24 — Events stay in Jira
- **Decision**: The Hub does **not** create Events. They keep being created and judged in Jira as the Framework describes; the Hub only reads judged Events.
- **Why**: Accepted recommendation; a shortcut can come later.
- **Lands in**: assumption (out of scope)

### G-25 — The Incentive Framework version the Hub follows
- **Decision**: **Incentive Framework v2.1** (PDF shared by the user 2026-09-26) is the rulebook for everything the Hub computes about Events, points and sanctions. It supersedes v2.0 wherever this ledger quoted v2.0 (G-21's rules stand; their source is now v2.1).
- **Why**: The user attached v2.1 as the current version.
- **Lands in**: assumption

### G-26 — What the Hub reads off a judged Event (answers Q23)
- **Decision**: Points are computed only from the fields v2.1 §6.2–6.3 lists: Event Type (Violation / Excellence / Self-report), Defect Category, Violation Category, Excellence Category, Judgment (P1), Event Observer (P2), Reporter's Own Mistake/Error and Problem/Error Solved (P3 ×0), Sudah Tahu Sebelumnya (P3 ×2). Violation points = 10 × P2 × P3, only when P1 = Kelalaian; Excellence points from the category (X1–X8). An Event missing a field its computation needs is shown "belum lengkap" and counts **nothing**; the Hub **never** guesses a value. Bringing the Jira judging screen in line with v2.1 (today it has Error Category, Event Observer, Reporter's Own Mistake, Problem/Error Solved and Person) is a Jira change made outside the Hub, and a prerequisite for correct points.
- **Why**: v2.1 already specifies the fields; option B (treat every Violation as negligence ×1) would be wrong in exactly the serious cases.
- **Lands in**: requirement + assumption (Jira screen change)

### G-27 — New staff adaptation (supersedes G-22)
- **Decision**: v2.1 §8 replaces the amnesty with **Masa Adaptasi**: for **30 calendar days from a new staff member's start date** the Framework does not apply to them. Their Events are still recorded, **without points**; violations in §4.3 (direct sanctions) apply **from day one**. The Hub stores a **start date ("Mulai bekerja")** on each Person, entered by whoever holds "Kelola orang"; people who started before v2.1 may leave it empty (no adaptation period).
- **Why**: The user's answer (G-22) matches v2.1 §8; the Hub has no start date today.
- **Lands in**: requirement

### G-28 — One category per Event
- **Decision**: Each Event carries one category. A mistake that fits two categories is recorded as two Events, each linked to the same content.
- **Why**: v2.1's category fields hold one value; an appeal can overturn one part without the other.
- **Lands in**: assumption

### G-29 — Warnings and SPs are recorded in the Hub
- **Decision**: Every teguran lisan and SP is recorded in the Hub (person, level, date, category code), behind "Lihat poin insentif", so the Hub knows the active SP, its 90-day validity and same-code teguran counts. **In addition, the user wants SPs issued automatically** once the v2.1 §3.2 calculation says so. What "automatically" means exactly: next round.
- **Why**: User: "agree but add auto SP after certain calculation based on IF".
- **Lands in**: requirement (auto-issue details pending)

### G-30 — Before Greenlight (Q27)
- **Decision**: The Greenlight screen shows rows per Bentuk against the Registry quota. **Blocking**: no Field Associate or Content Editor on the Client's Team, no Jira component, a row missing its date, Topik or Bentuk. **Warnings** (Greenlight allowed): quota mismatch, a date outside the month. Anything Jira would reject is repaired automatically. Anyone with "Kelola otomasi" can give a Greenlight.
- **Why**: Accepted recommendation.
- **Lands in**: requirement

### G-31 — A batch that partly fails (Q28)
- **Decision**: Rows Jira accepts are created and keyed; failures are listed per row with Jira's reason; the next press creates only those; one message goes to #eskala-otomasi only when a batch had failures; the press runs in the background with visible progress.
- **Why**: Accepted recommendation.
- **Lands in**: requirement + edge case

### G-32 — The Otomasi page (Q29)
- **Decision**: One card per automation (Jira, Harvest): last run, next run, result, failures, 90 days of history. Harvest: a table of every account with Client, own/competitor, last harvested, posts collected, status (blocked, not found, not yet harvested) and "Jalankan sekarang". Jira: the month's Content Plans (found / greenlit / changed since Greenlight / issues created / changed after issues) with Greenlight and "Buat issue Jira".
- **Why**: Accepted recommendation.
- **Lands in**: requirement

### G-33 — The performance report (Q30)
- **Decision**: Per Client per month, own accounts beside the competitors' average: posts by type; total and average views, likes, comments, shares; engagement rate (÷ views for video, ÷ followers where known); top 5 posts; change from last month. Marked posts excluded (G-17). A missing Instagram follower count is stated, never shown blank.
- **Why**: Accepted recommendation.
- **Lands in**: requirement

### G-34 — Who an Event concerns (Q31)
- **Decision**: The Event's **Person** field; Assignee only when Person is empty; neither → "belum lengkap". In practice it is mainly Person.
- **Why**: User: "agree, mainly it's Person".
- **Lands in**: requirement

### G-35 — Where a return comes from (Q32)
- **Decision**: The user has added a **Return Screen** to the Jira workflow on every return transition, with **Defect Category** and **Return Reason** as required fields. A return carrying a Defect Category counts against that **origin station**; the Return Reason is shown with it. Returns before the screen existed carry neither; they are shown as "tanpa kategori" under the station that sent them back and counted against no one. That gap in older history is accepted.
- **Why**: User added the screen on 2026-09-26; older issues lacking it is "okay".
- **Lands in**: requirement + edge case

### G-36 — How the team thresholds are counted (Q34)
- **Decision**: **Client first pass**: content that went to client review once and moved forward without being sent back by the client; content with no client review that month is left out of the rate. **QA round**: each time content enters *Designs in Review*; the team figure is the average rounds per content that reached QA that month. Both come from Jira history.
- **Why**: Accepted recommendation; v2.1 names Jira as the data source.
- **Lands in**: requirement

### G-37 — Direct sanctions need a "Butir 4.3" field
- **Decision**: A dropdown **"Butir 4.3"** (the 24 items of v2.1 §4.3) is added to the Jira verdict screen, filled only when the Violation Category is H2. The Hub reads the item to know the sanction. An H2 Event without it is **never** handled automatically: the Hub shows "Sanksi langsung, tentukan manual". The verdict screen is also brought in line with v2.1 §6.3 (G-26); the field list is in `jira-fields.md`.
- **Why**: A wrong guess from free text would mean an SP3 or a termination issued by mistake.
- **Lands in**: requirement + assumption (Jira screen change, made by the user)

### G-38 — Sanctions are issued automatically, with a hold window, and the Hub writes the SP letter
- **Decision**: Option A. On **working day 2** the Hub works out each person's sanction from the v2.1 §3.2 ladder (teguran lisan, SP1/SP2/SP3, PIP). A manager with "Lihat poin insentif" can **hold** it with a written reason until **working day 3**; if nobody holds it, it is recorded as **issued** on working day 3. A sanction resting on an Event under appeal is held automatically until the appeal is decided. **"Proses PHK" is only ever flagged for a person to start, never issued.** Direct sanctions (§4.3, via "Butir 4.3") follow the same path immediately instead of at month end. The Hub also **produces the SP letter** as a filled-in document.
- **Why**: Automatic as the user asked (G-29), but respecting the monthly close (§7.1) and appeals (§7.2), with one day for a manager to stop a wrong one. The user added the letter.
- **Lands in**: requirement (letter details next round) + ADR offered next round

### G-39 — Eskala only
- **Decision**: Spec 009 covers **Eskala only**: Otomasi, Laporan and the incentive section. Venyu's Clients do not appear there.
- **Why**: The Framework, Content Plans, project ESKL and the harvest are all Eskala's.
- **Lands in**: requirement (scope)

### G-40 — Laporan freshness
- **Decision**: Jira data for Laporan is refreshed every **15 minutes**, together with the plan watcher; each page shows "diperbarui HH:MM".
- **Why**: Accepted recommendation; no cost.
- **Lands in**: requirement

### G-41 — The performance report is internal
- **Decision**: Internal only. The monthly report each Client receives is a separate, later spec (deferred register H-3).
- **Why**: It has its own format and voice.
- **Lands in**: assumption (out of scope) + DEFERRED H-3

### G-42 — Alerts
- **Decision**: One Slack message to the Brand Manager on working day 2: "sanksi bulan ini siap ditinjau, batas tahan: hari kerja ke-3". Besides batch failures (G-31), nothing else is pushed; lateness alerts are out of this spec.
- **Why**: Nothing is issued unseen; staff already work from Jira.
- **Lands in**: requirement

### G-43 — The Event verdict screen as built (answers Q33)
- **Decision**: The user built the verdict screen with: Event Type, Event Observer, Person, **Known by the Person** (= v2.1 "Sudah Tahu Sebelumnya", P3 ×2), **Violation Judgment** (= v2.1 "Judgment (P1)"), Reporter's Own Mistake/Error, Problem/Error Solved, Violation Category, **Direct Sanction Violation** (the §4.3 item; supersedes the working name "Butir 4.3" in G-37), Excellence Category. The Hub reads these Jira names. The field sheet is `jira-fields.md`.
- **Why**: User's screenshot, 2026-09-26.
- **Lands in**: requirement + assumption

### G-44 — The SP letter
- **Decision**: I draft the template (`sp-letter-template.md`) for the user's approval. One template covers SP1, SP2, SP3 and the §4.3.2 Peringatan Pertama dan Terakhir; a teguran lisan gets no letter; termination is never written by the Hub. Letters are Google Docs in **Company > HR > Surat Peringatan** (restricted), one file per letter, created on working day 3 when the sanction is issued, signature left blank; a manager signs and hands it over; the Hub **never** sends it to the employee.
- **Why**: User: "Make the template and I agree for the directory."
- **Lands in**: requirement

### G-45 — Points are shown on the Event, signed
- **Decision**: Once an Event is judged, the Hub posts **one comment** on it with the computed points (comment only, never a field change). Everywhere the Hub shows points (comments, reports, letters), **Violation points are negative** (e.g. −30) and **Excellence points are positive** (e.g. +3).
- **Why**: User agreed to Q42 and set the sign convention "for further references".
- **Lands in**: requirement

### G-46 — Automatic sanctions are an ADR
- **Decision**: Recorded as ADR-0001, "Sanctions are issued automatically after a one-day hold".
- **Why**: Passes all three gates: issued SPs are HR records (hard to reverse), software issuing warnings is surprising, and hold / one-click / fully automatic was a real choice.
- **Lands in**: ADR-0001

### G-47 — When v2.1 starts counting (Q41)
- **Decision**: Points and sanctions count from **27 September 2026**. Events judged before that date are shown for reference only and **never** count toward a sanction. (The user first wrote "2028"; confirmed as 2026.)
- **Why**: Avoids a sanction resting on half-v2.0, half-v2.1 data.
- **Lands in**: requirement

### G-48 — The SP letter's fixed details (Q44)
- **Decision**: Company **CV Amerta Meta Data**; legal address Jl. Dr Cipto No. 9A, Perum Bimantara, Kolor, Kota Sumenep, Jawa Timur 69417; office address Jl. Perumnas Seturan No. 279, Kledokan, Caturtunggal, Sleman, D.I. Yogyakarta 55281. Number format `{urut}/SP/ESK/{bulan romawi}/{tahun}` (e.g. `003/SP/ESK/X/2026`), from 001 each year. Signed by the **CEO and the Brand Manager**. City **Yogyakarta**.
- **Why**: User's answer.
- **Lands in**: requirement (template in `sp-letter-template.md`)

### G-49 — A letter to the Brand Manager
- **Decision**: When the person receiving an SP is the Brand Manager, the CEO signs alone.
- **Why**: Confirmed with the closing summary.
- **Lands in**: edge case

## Amendments after close (2026-09-26, during rollout)

### G-50 — No Defect Category on Events (supersedes the Jira-screen part of G-26 and jira-fields.md note 2)
- **Decision**: The user will **not** add Defect Category to the Event screens: "that's not the place". An Event's category is its **Violation Category** (or Excellence Category). A content defect's origin station comes from the Return Screen on the Content ticket (G-35), not from the Event.
- **Why**: User, during rollout.
- **Lands in**: requirement (FR-070, FR-072) + assumption

### G-51 — Event Observer and Excellence Category stay multi-choice (supersedes G-28 for Excellence, and the "several values → belum lengkap" rule of G-26 for these two fields)
- **Decision**: Both fields stay as they are (several values allowed). **Event Observer** with both values ticked means the client knew: P2 = 3. **Excellence Category** with several values: each counts and the points add up (e.g. X1 + X4 = +4).
- **Why**: User, during rollout; consistent with "single/multiple categories" (G-15).
- **Lands in**: requirement (FR-071, FR-072)

### G-52 — On Hold and Shelved
- **Decision**: "On Hold" and "Shelved" are reserved statuses that mean what they say, set by managers. Neither is a Station: time there is charged to no one and neither is ever Late. Shelved counts as cancelled; On Hold is counted on its own in the Delivery report.
- **Why**: User, 2026-09-27 ("reserved for contents that are well according to title. It's managed by managerials").
- **Lands in**: requirement (FR-051, FR-052)

## Deferred register rows in scope
- A-1 — claimed for the Jira automation and the harvest (see G-2); songbird's part stays deferred. /speckit-specify narrows the row to songbird.
- H-2 — re-deferred (see G-2): target stays "the Client Card → Jira/Slack spec"; spec 009 changes when issues are created, not what they say.
- S-1 — resolved outside a spec by PR #13; row deleted (see G-2).
- H-3 — new, added by this session: the client monthly report (see G-41).

## Facts looked up (not decisions)
- ESKL Content issue statuses in use (JQL, last 60 days): Plan, Footages in Progress, Footage Taken, Designs in Progress, Designs in Review, Internally Reviewed, In Review by Client, Reviewed by Client, Scheduled, Published, Need Review, Done, Shelved.
- Example late issue: ESKL-11147, publication date 2026-09-05, still *Footages in Progress* on 2026-09-26.
- The content-plan → Jira flow never writes the issue key back and never checks for an existing issue; a second run duplicates every issue (`flows/content_plan_spreadsheet_to_jira_issue.py`, `tasks/utility_tasks.py`).
- Live Content Plans already have empty `TicketID` and `Key` columns, plus a per-row `Approval` column (e.g. "Content Plan - Pelita Delapan - September 2026", 19 columns, `No.` … `Key`). Rows carry no stable id besides `No.`.
- Issue fields set today: issue type Content (10009), component per Client, publication date (customfield_10040), Field Associate (10042), Content Editor (10043), start date (10015), content type (10039), due date, assignee = Field Associate, reporter = Noktah (`tasks/utility_tasks.py`).
- Harvest schedules today: 22 hand-written `harvest-monthly-*` deployments in `service/prefect/prefect.yaml`, days 31, from 17:30 WIB on the 1st, 30 minutes apart; competitors 02:30–05:00 WIB on the 2nd.
- Ad flags are reviewer-edited in the Account Social Harvest sheets and synced daily into the harvest store (`flows/social_harvest_sync.py`).
- Constitution, Data Management: `hashmap.py` is the source for static mappings; moving the Jira automation and harvest to the Registry needs an amendment.
- Incentive Framework v2.0 (memory): errors are charged to the **origin station**; responsibility moves only when the next station accepts; team thresholds are client first-pass ≥60% and ≤1.5 QC rounds per content.
- Incentive Framework v2.0 (Google Doc "Incentive Framework", Company > Eskala, read 2026-09-26): Error Category letters are origin stations (A Content Planner, B Field Associate footage, C Content Editor, D Quality Control, E Field Associate client & publication, F all, G managerial, H attitude, Z other). Judging screen fields: Judgment (P1), Event Observer (P2: internal 1, client 3), Reporter's Own Mistake + Problem/Error Solved (P3 ×0), Sudah Tahu Sebelumnya (P3 ×2); points = 10 × P2 × P3, only when P1 = Kelalaian. Excellence Category X1–X8 worth +1 or +3. Event flow: Reported → Judged → Appealed → Judged. Team rules: more than 3 rounds between two stations on one content goes to managerial review (4.2.1); team reward Rp100.000 when at least 60% of a person's client teams meet both thresholds (5.2). Sanction tiers by monthly Violation points (3.2). First month of enforcement is an amnesty (8).
- Recent Event tickets (ESKL-11164…11176, status Judged) carry Error Category (cascading), Event Observer, Reporter's Own Mistake, Problem/Error Solved and a **Person** field (customfield_10291) for who is charged; none of the sampled tickets show Judgment (P1), Sudah Tahu Sebelumnya, Event Type, Excellence Category or Poin.
- The Registry already holds each Client's Jira component, Content Plan folder and monthly quotas, and each Person's Jira account; the Team gives the Field Associate and Content Editor (`clients.jira_component_id`, `content_plan_folder_id`, `quota_*`, `people.jira_account_id`, `client_team_assignments`). People have **no start date** (`people`: status, left_at only).
- Incentive Framework **v2.1** (PDF, 2026-09-26) changes from v2.0: QC is now QA throughout; Error Category is split into **Defect Category** (A–E, Z; content defects, also set on the *Content* ticket at every return, giving the return's origin station) and **Violation Category** (W, F, G, K, S, H, Z; process, time, communication, behaviour); §3.2 sanction ladder: 10–20 points teguran lisan; 3 teguran lisan in 90 days with the same category code → SP1; 30–50 points → SP1, or SP2 if SP1 active, or SP3 if SP2 active; 60+ points or two months running ≥30 → one level above the active SP (none → SP1) + 30-day PIP; violating again with SP3 active, or a failed PIP → PHK. Teguran and SP are valid **90 days** from issue; SPs never skip a level except §4.3 direct sanctions; the heaviest applicable row wins. §4.3 direct sanctions (straight to SP1, straight to first-and-last warning, straight to PHK) are recorded as Violation Category **H2** with the item number in the Description. §5.2 team thresholds: client first pass ≥60%, average **QA rounds** ≤1.5 per content. §8 Masa Adaptasi replaces the amnesty. Assignee is named as the person concerned (§6.2); today's tickets use a **Person** field instead.
- Framework v2.0 3.2 (superseded by v2.1) covered the Surat Peringatan ladder: 30–50 points gives an SP; 60 or more, or 30 or more two months running, gives the next SP level plus a 30-day PIP or demotion; a failed PIP or SP Ketiga starts termination. An SP is valid 6 months, and the level resets when no new SP comes within 6 months. The tier carries over to the next month, the points do not.
- Event **create** fields (Jira create metadata, issue type Event 10034, 2026-09-26): Person (user, customfield_10291), Event Observer (**multi-choice**, customfield_10292), Excellence Category (**multi-choice**, customfield_10300, X1–X8 labelled as v2.1), Violation Category (cascading, customfield_10301: F, G, H, K, S, W, Z), Description, Attachment, Linked Issues. **No Defect Category** on the Event. The issue type descriptions still say "Incentive Framework v2.0", and the Event's says to link an "Asset" ticket (the type is Content).
- Content issue history (ESKL-11133): the assignee stays the Field Associate throughout; the Content Editor moves Footage Taken → Designs in Progress → Designs in Review; Quality Assurance approves by adding the label REVIEWED and returns by moving Designs in Review → Designs in Progress; the Field Associate moves the status on after QA's label. History uses older status names (*Reviewed*, *Published & Reviewed*) that were since renamed (*Internally Reviewed*, *Published, Need Review*).
