# Feature Specification: Noktah Hub: Otomasi & Laporan

**Feature Branch**: `feat/project/Hub-automations-and-reports`

**Created**: 2026-09-26

**Status**: Draft

**Input**: User description: "Noktah Hub Otomasi & Laporan, spec 009. Read specs/009-hub-otomasi-laporan/brief.md first; its decisions are settled."

**Grill ledger**: [grill.md](grill.md) · session 2026-09-26 (closed; 49 decisions, plus G-50/G-51 amended during rollout). Settled inputs: [brief.md](brief.md). Supporting: [jira-fields.md](jira-fields.md), [sp-letter-template.md](sp-letter-template.md), [ADR-0001](../../docs/adr/0001-sanctions-issued-automatically-after-a-one-day-hold.md).

## Inherited & Deferred *(mandatory)*

- Claimed from register: **A-1**, for the Jira automation and the Harvest only (G-2). Songbird's part stays deferred: the A-1 row in `docs/DEFERRED.md` is narrowed to songbird in the same PR, not deleted.
- Re-deferred: **H-2** → the Client Card → Jira/Slack spec. This spec changes when and how issues are created, not what they say (G-2). The row is already updated.
- Resolved outside a spec: **S-1** (songbird reads the Client Card, PR #13). The row is deleted (G-2).
- Deferred by this spec: **H-3**, the client-facing monthly report (G-41). Added to `docs/DEFERRED.md`.

## Context

Eskala's production runs on two automations and a spreadsheet habit, none of them visible in the Hub.

**Jira issues are created by a manual script run from a terminal.** It reads every Client's Content Plan for a month and creates one issue per row. It keeps no record and sends no alert. Running it twice creates every issue twice. Nothing ties a plan row to its issue, so when a Client changes a plan after its issues exist (which happens), nobody notices.

**The Harvest runs on 22 hand-written monthly schedules.** Each Client or competitor account has one, and adding an account to the Registry changes nothing until someone writes another. Reviewers flag ads in separate spreadsheets.

**No one can see how production is going.** Nobody can see which content is late, where it is waiting, how often work is sent back between stations, or how a Client's accounts perform against its competitors. The Incentive Framework v2.1 runs on Jira data, but its monthly rekap and sanctions are worked out by hand.

This feature adds two Hub menus for Eskala:
1. **Otomasi**: the Jira automation (Greenlight, then batch issue creation, then watching plans) and the Harvest (Registry-driven, reviewed in the Hub).
2. **Laporan**: delivery (planned, created, Published, Late), stations and people, account performance, and Incentive Framework points and sanctions.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Greenlight Content Plans and create their Jira issues in batches (Priority: P1)

It's 28 September. Five of Eskala's Clients have finished their October Content Plans. Defila opens **Otomasi → Jira** and sees October's plans: which were found, which are greenlit, which changed since their Greenlight, and which already have issues.

She opens Pelita Delapan's plan. The Hub shows 4/4 Post, 3/4 Story and 4/4 Short Video against the Registry quota, with a warning that one Story is missing. It shows no blocking problems. She presses **Greenlight**. She does the same for four more plans, selects all five, and presses **"Buat issue Jira"**. The creation runs in the background with progress shown. 58 of 60 rows become issues, and each issue's key appears in that row's **Key** column in the plan. Two rows are rejected, and the Hub lists them with Jira's reason. #eskala-otomasi gets one message. She fixes the two rows and presses again; only those two are created.

**Why this priority**: this is the automation Eskala's production starts from, and today it can duplicate every issue and loses track of which row made which issue. The user named it the most important part.

**Independent Test**: with a test month of plans, greenlight some, create issues in a batch, press again, and confirm no issue is duplicated and every created issue's key is in its row.

**Acceptance Scenarios**:

1. **Given** a Content Plan with no Field Associate on the Client's Team, **When** a manager opens it, **Then** Greenlight is not allowed and the blocking problem is named (G-30).
2. **Given** a plan whose row counts differ from the Registry quota, or with a date outside the month, **When** a manager opens it, **Then** the difference is shown as a warning and Greenlight is still allowed (G-30).
3. **Given** a greenlit plan that changes before its issues are created, **When** the Hub notices the change, **Then** the plan shows "Berubah sejak disetujui" and needs a new Greenlight before issues can be created (G-5, G-11).
4. **Given** a batch of greenlit plans, **When** "Buat issue Jira" is pressed, **Then** one issue is created per row, and each issue's key is written as a link into that row's **Key** column. **TicketID** is left untouched (G-8, G-12).
5. **Given** a plan whose issues already exist, **When** "Buat issue Jira" is pressed again, **Then** only rows without an issue are created, and it **never** duplicates (G-5).
6. **Given** a batch where Jira rejects 2 of 60 rows, **When** the batch ends, **Then** 58 are created and keyed, the 2 are listed per row with Jira's reason, the next press creates only those 2, and **one** message goes to #eskala-otomasi (G-31).
7. **Given** a Topik cell with a line break, **When** its issue is created, **Then** the Hub repairs it automatically instead of letting Jira reject it (G-30).

---

### User Story 2 - Keep plans and issues in step after creation (Priority: P1)

On 6 October, Pelita Delapan's Client moves row 5 from 18 to 22 October and rewrites its Visualisasi Konten. Row 5's issue is already in *Footage Taken*. Within 15 minutes the Hub flags the row as "berubah setelah issue dibuat" and shows old → new. It also posts a comment on the Jira issue listing the change, so the Field Associate and Content Editor see it where they work. The issue's fields stay as they are, and the Field Associate updates the date in Jira. The Client also adds a row 13, which waits for the next "Buat issue Jira".

**Why this priority**: the user reported that Clients change plans after issues exist, and nobody notices today.

**Independent Test**: change, delete and add rows in a plan that has issues, and confirm that within 15 minutes each is flagged correctly and changed rows get a Jira comment. No issue field may change.

**Acceptance Scenarios**:

1. **Given** a keyed row whose content changes, **When** the next check runs (every **15 minutes**), **Then** the row is flagged "berubah setelah issue dibuat" with old → new, and **one** comment listing the change is posted on its issue. The issue's fields are **not** changed (G-9, G-13).
2. **Given** a keyed row deleted from the plan, **When** the check runs, **Then** it is flagged "dihapus dari plan", and its issue is **never** shelved automatically (G-13).
3. **Given** a new row without a Key, **When** the check runs, **Then** it is shown as waiting for the next "Buat issue Jira" (G-13).
4. **Given** a row whose Key cell was erased, or a Key copied onto another row, **When** the check runs, **Then** it is flagged for a person to fix and **never** causes a second issue (G-12).

---

### User Story 3 - The Harvest follows the Registry (Priority: P1)

An Account Executive adds a competitor Instagram account to Klinik Mata Sampang in the Registry. No one touches a schedule. At the next monthly run, the new account is harvested with 90 days of history, and after that with the usual 31. Another Client is marked inactive, and its accounts are no longer harvested. In **Otomasi → Harvest**, a manager sees every account with its Client, whether it is own or competitor, when it was last harvested, how many posts were collected, and its status (blocked by the platform, not found, not yet harvested). They press "Jalankan sekarang" on one account to harvest it now.

**Why this priority**: the user named the Harvest alongside Jira as the priority. It also retires 22 hand-written schedules that drift from the Registry.

**Independent Test**: add, remove and deactivate accounts in the Registry, run the monthly Harvest, and confirm it harvested exactly the Registry's active accounts, the new one with 90 days.

**Acceptance Scenarios**:

1. **Given** the Registry's active Eskala Clients and their own and competitor accounts, **When** the monthly Harvest runs, **Then** every one of those accounts is harvested, one at a time, **30 minutes apart**, each over the last **31 days** (G-7).
2. **Given** an account never harvested before, **When** it is first harvested, **Then** it covers **90 days** (G-7).
3. **Given** an account removed from the Registry or a Client marked inactive, **When** the next run starts, **Then** those accounts are not harvested, with no schedule edited (G-7).
4. **Given** a manager with "Kelola otomasi", **When** they press "Jalankan sekarang" on one account, **Then** that account alone is harvested now (G-7, G-32).

---

### User Story 4 - Review harvested posts in the Hub (Priority: P2)

Reviewers open an account's harvested posts for September. Each post shows its link, caption and numbers. A boosted post is marked **Iklan**; a holiday greeting, **Tidak relevan**; a repost from a doctor's account, **Bukan konten akun ini**. A post may carry several marks. Marked posts stay stored and still count in "how many posts this month", but they are left out of averages, top posts and songbird's examples. The ad flags already set in the old Account Social Harvest sheets are brought over once, and those sheets stop being written.

**Why this priority**: the performance report and songbird both need clean data, and the sheets being retired are where review happens today.

**Independent Test**: mark posts with each mark and combinations of marks, and confirm what the performance report and songbird's examples include and exclude.

**Acceptance Scenarios**:

1. **Given** a harvested post, **When** a person with "Kelola otomasi" sets any of **Iklan**, **Tidak relevan** or **Bukan konten akun ini** (multiple allowed; default none), **Then** the marks are saved (G-6, G-17).
2. **Given** a post with any mark, **When** the performance report or songbird reads posts, **Then** it is excluded from averages, top posts and examples. It still counts in the month's post count and appears in a separate collapsed list in the report (G-17).
3. **Given** ad flags in the Account Social Harvest sheets, **When** the Hub's review goes live, **Then** they are brought over once as **Iklan**, and the sheets are not written again (G-6).

---

### User Story 5 - See delivery per Client per month (Priority: P2)

On 10 October, Defila opens **Laporan → Delivery**. For each Eskala Client she sees September's planned, created, Published, Late, published late and cancelled content. For example, ESKL-11147 was due 5 September and is still in *Footages in Progress*, so it shows as Late. The page says "diperbarui 09:45".

**Why this priority**: lateness is the first question a manager asks, and nothing answers it today.

**Independent Test**: compare the report for a past month against Jira for the same issues.

**Acceptance Scenarios**:

1. **Given** content whose issue reached *Published, Need Review* or *Done*, **When** the report is read, **Then** it counts as **Published** (G-4).
2. **Given** content whose publication date has passed and which is not Published, **When** the report is read, **Then** it counts as **Late**. Shelved content counts as cancelled, **never** as Late (G-4).
3. **Given** content that was Published after its publication date, **When** the report is read, **Then** it counts as **published late**, dated by the day Jira's history shows it reaching a published status (G-4).
4. **Given** any month from **January 2026** onward, **When** the report is opened, **Then** it has data, refreshed at most **15 minutes** ago, and says when (G-16, G-40).

---

### User Story 6 - See stations, returns, people and teams (Priority: P2)

Defila opens **Laporan → Stations** for September. For each of the five stations (A Planning, B Footage, C Editing, D Quality control, E Client & publication) she sees:
- how much content arrived and moved forward;
- how many returns, and from which origin station, with the Return Reason;
- how long content was held;
- what is still waiting, and for how long.

The same numbers are shown per person and per Client team. The team view shows the client first-pass rate and average QA rounds against the Framework's thresholds. Content that went back and forth more than 3 rounds between two stations is flagged for managerial review.

**Why this priority**: the user asked for detailed performance per station, individual and team, including how many times work went back and forth.

**Independent Test**: for one Client and month, trace every issue's history by hand and compare it with the station, person and team numbers.

**Acceptance Scenarios**:

1. **Given** a Jira status, **When** the Hub places content at a station, **Then** it uses exactly this mapping (G-14, G-19):
   - Plan → A Planning (Content Planner)
   - Footages in Progress → B Footage (Field Associate)
   - Footage Taken, Designs in Progress → C Editing (Content Editor)
   - Designs in Review, *Published, Need Review* → D Quality control (Quality Assurance)
   - Internally Reviewed, In Review by Client, Reviewed by Client, Scheduled → E Client & publication (Field Associate)
2. **Given** a station, **When** the Hub names its person, **Then** B, C and E come from the issue's own Field Associate or Content Editor, and A and D from the Client's Team. The Jira assignee is **not** used (G-19).
3. **Given** a return made through the Return Screen, **When** it is counted, **Then** it counts against the **origin station** of its Defect Category, with its Return Reason shown (G-35).
4. **Given** a return from before the Return Screen existed, **When** it is counted, **Then** it is shown as "tanpa kategori" under the station that sent it back and counted against no one (G-35).
5. **Given** any return, **When** the report shows it, **Then** it is counted but **never** charged to anyone by the Hub; only a judged Event charges (G-15).
6. **Given** a Client team's month, **When** thresholds are shown, **Then** client first pass counts content that went to client review once and moved forward without being sent back by the client (content with no client review is left out), and a QA round is each entry into *Designs in Review*, averaged per content that reached QA (G-36).
7. **Given** content with **more than 3 rounds** between two stations, **When** the report is read, **Then** it is flagged for managerial review, not charged (G-21).

---

### User Story 7 - See how a Client's accounts perform against its competitors (Priority: P2)

Defila opens **Laporan → Performa** for Klinik Mata Sampang, September. Beside its competitors' average, she sees:
- posts by type;
- total and average views, likes, comments and shares;
- engagement rate;
- the top 5 posts;
- the change from August.

The follower count for one Instagram account was unavailable from the platform, and the report says so rather than showing a blank.

**Why this priority**: it is the other half of Laporan in v1, built on data the Harvest already collects.

**Independent Test**: compute a Client's month by hand from harvested posts (excluding marked ones) and compare with the report.

**Acceptance Scenarios**:

1. **Given** a Client and month, **When** the report is opened, **Then** it shows, for its own accounts beside its competitors' average, posts by type; total and average views, likes, comments and shares; engagement rate (engagement ÷ views for video, ÷ followers where the follower count is known); the top 5 posts; and the change from last month (G-33).
2. **Given** a missing follower count, **When** the report shows engagement per follower, **Then** it states the count is unavailable, **never** a blank (G-33).
3. **Given** marked posts, **When** the report is computed, **Then** they are excluded (G-17, G-33).

---

### User Story 8 - Compute Incentive Framework points from judged Events (Priority: P3)

A Quality Assurance lead judges ESKL-11200 in Jira. They set Violation Judgment = Kelalaian, Event Observer = External (Client), Reporter's Own Mistake = No and Known by the Person = No, with Defect Category C2 and Person = the Content Editor. Within 15 minutes the Hub:
- computes **−30**;
- posts one comment on the Event: "Poin: −30 (Klien × ditemukan orang lain)";
- adds it to the Content Editor's month.

Another Event lacks a Violation Judgment and shows as "belum lengkap", counting nothing. A Content Editor who started 10 days ago has an Event recorded with no points (Masa Adaptasi).

**Why this priority**: it depends on Jira screen changes the user is still making, and on the reports above. Its value is replacing the monthly hand rekap.

**Independent Test**: judge a set of test Events covering every combination in v2.1 §4.1.1 and the Excellence table, and confirm each computed value and comment.

**Acceptance Scenarios**:

1. **Given** a judged Violation or Self-report with Violation Judgment = Kelalaian, **When** points are computed, **Then** points = −(10 × P2 × P3), where P2 = 1 (Internal) or 3 (External), and P3 = ×0 when Reporter's Own Mistake = Yes **and** Problem Solved = Yes, ×2 when Known by the Person = Yes, and ×1 otherwise. Any other Violation Judgment gives **0** (G-26, G-43, G-45).
2. **Given** a judged Excellence, **When** points are computed, **Then** they follow its category, positive: X1, X2, X3, X8 = **+1**; X4, X5, X6, X7 = **+3** (G-26, G-45).
3. **Given** an Event missing a field its computation needs, or with none or more than one value in a single-choice field, **When** it is read, **Then** it is shown "belum lengkap" and counts **nothing**; the Hub **never** guesses (G-26).
4. **Given** an Event, **When** the Hub finds whom it concerns, **Then** it reads the **Person** field, the Assignee only when Person is empty, and neither → "belum lengkap" (G-34).
5. **Given** an Event whose incident falls within a Person's first **30 calendar days** from their start date, **When** points are computed, **Then** it is recorded with **no** points, except §4.3 direct-sanction violations, which apply from day one (G-27).
6. **Given** an Event judged before **27 September 2026**, **When** it is shown, **Then** it is for reference only and **never** counts toward a sanction (G-47).
7. **Given** an Event under appeal, **When** points are shown, **Then** they are marked on hold until the appeal is judged, and the final judgement counts (G-26).
8. **Given** a judged Event, **When** its points are computed, **Then** the Hub posts **one** comment with them on the Event and changes none of its fields (G-45).

---

### User Story 9 - Sanctions issued automatically after a one-day hold, with the SP letter (Priority: P3)

On the second working day of November, the Hub works out October's sanctions from the ladder. A Field Associate has −30 points with an active SP1, which gives **SP2**. The Brand Manager gets a Slack message: "sanksi bulan ini siap ditinjau, batas tahan: hari kerja ke-3". Nobody holds it. On working day 3 the SP2 is recorded as issued, and its letter appears as a Google Doc in Company > HR > Surat Peringatan, numbered `004/SP/ESK/XI/2026`, with blank signature lines for the CEO and the Brand Manager. The Brand Manager prints it, both sign, and it is handed over.

A second person's sanction rests on an Event under appeal, so it is held automatically. A third person's Event is H2 with Direct Sanction Violation "4.3.2-1 Posting ke akun Klien tanpa ACC Klien". Their Peringatan Pertama dan Terakhir follows the same path immediately, not at month end.

**Why this priority**: it builds on User Story 8 and on warnings being recorded in the Hub. It is Incentive Framework v2.1 made automatic, as the user asked.

**Independent Test**: seed recorded warnings and SPs plus judged Events for each ladder row in v2.1 §3.2, run the month's calculation, and compare each result, hold, issue and letter with the Framework.

**Acceptance Scenarios**:

1. **Given** a person's month, **When** sanctions are worked out on **working day 2**, **Then** the v2.1 §3.2 ladder applies exactly, and the heaviest applicable row wins (G-29, G-38):
   - 10–20 points → teguran lisan;
   - 3 teguran lisan within 90 days with the same category code → SP1;
   - 30–50 points with no active SP → SP1; with SP1 active → SP2; with SP2 active → SP3;
   - 60+ points, or 30+ points two months running → one level above the active SP (none → SP1), plus a 30-day PIP;
   - violating again while SP3 is active, or a failed PIP → flagged "proses PHK".
2. **Given** the sanctions are ready, **When** working day 2 arrives, **Then** the Brand Manager gets **one** Slack message: "sanksi bulan ini siap ditinjau, batas tahan: hari kerja ke-3" (G-42).
3. **Given** a manager with "Lihat poin insentif", **When** they hold a sanction with a written reason before the end of **working day 3**, **Then** it is not issued (G-38).
4. **Given** a sanction nobody held, **When** working day 3 ends, **Then** it is recorded as **issued**, and its SP letter is produced (SP1, SP2, SP3 or Peringatan Pertama dan Terakhir; a teguran lisan gets no letter) (G-38, G-44).
5. **Given** a sanction resting on an Event under appeal, **When** it would be issued, **Then** it is held automatically until the appeal is decided (G-38).
6. **Given** "proses PHK", **When** it applies, **Then** it is only flagged for a person to start, **never** issued, and no letter is written (G-38, G-44).
7. **Given** a judged H2 Event with a Direct Sanction Violation, **When** it is judged, **Then** the sanction its item names follows the same hold-and-issue path immediately, not at month end. An H2 Event **without** that field is **never** handled automatically: it shows "Sanksi langsung, tentukan manual" (G-37, G-38, G-43).
8. **Given** an issued SP, **When** its letter is produced, **Then** it is saved as a Google Doc in **Company > HR > Surat Peringatan**, one file per letter, with:
   - the fixed letterhead (CV Amerta Meta Data, both addresses);
   - the number `{urut}/SP/ESK/{bulan romawi}/{tahun}`, counting from 001 each year;
   - the city Yogyakarta;
   - blank signature lines for the **CEO and the Brand Manager** (the CEO alone when the person is the Brand Manager).

   The Hub **never** sends it to the employee (G-44, G-48, G-49).
9. **Given** a teguran lisan or SP issued outside the Hub, **When** a manager records it, **Then** it counts in the ladder from its issue date for **90 days** (G-29).

---

### Edge Cases

- A plan changes between Greenlight and "Buat issue Jira": creation is refused for that plan until it is greenlit again (G-5).
- A row's Key is erased, or a Key is copied onto a second row: the Hub relies on its own record of which row made which issue, flags the plan for a person to fix, and **never** creates a second issue (G-12).
- Jira rejects some rows in a batch: the rest are created, the rejected rows are listed with Jira's reason, and only they are retried on the next press (G-31).
- A keyed row is deleted from the plan: it is flagged "dihapus dari plan", and the issue is **never** shelved automatically (G-13).
- A return from before the Return Screen existed has no Defect Category: it is shown "tanpa kategori" and charged to no one (G-35).
- An Event is judged with both Event Observer values ticked: the client knew, P2 = 3. Several Excellence Categories: their points add up (G-51).
- One mistake fits two categories: it is recorded as two Events, each linked to the same content; the Hub treats each separately (G-28).
- An Event has neither Person nor Assignee: it is "belum lengkap" (G-34).
- An H2 Event lacks Direct Sanction Violation: "Sanksi langsung, tentukan manual"; it is never automatic (G-37).
- The person receiving an SP is the Brand Manager: the CEO signs alone (G-49).
- A sanction rests on an Event that is appealed after the sanction was issued: v2.1 §7.2 suspends the sanction during the appeal, and the Hub marks it suspended until the appeal is judged (G-38).
- A Person has no start date: no adaptation period applies to them (G-27).
- A Client is Venyu's: it does not appear in Otomasi, Laporan or the incentive section (G-39).
- The history uses renamed statuses (*Reviewed*, *Published & Reviewed*): they map to the same stations as their current names (*Internally Reviewed*, *Published, Need Review*).

## Requirements *(mandatory)*

### Functional Requirements

**Scope and access**

- **FR-001**: The Hub MUST add two menus, **Otomasi** and **Laporan**, covering **Eskala only**; Venyu's Clients MUST NOT appear in them (G-1, G-39).
- **FR-002**: The Jira automation and the Harvest MUST read Clients, Teams, social accounts, Jira components and Content Plan folders from the **Registry**, not from the Clients or Hashmaps sheet copies (brief, A-1, G-2).
- **FR-003**: The Hub MUST add three permissions, each ticked per Person: **"Kelola otomasi"** (on by default for Owner and Brand Managers), **"Lihat laporan"** (on by default for Owner and Brand Managers, tickable for Production Managers; covers per-person numbers) and **"Lihat poin insentif"** (on only for Owner and Brand Managers) (brief, G-3, G-18, G-23).
- **FR-004**: "Lihat laporan" MUST NOT show anyone's points or sanctions; those need "Lihat poin insentif" (G-23).
- **FR-005**: There MUST be no "see only my own" view (G-18).

**Otomasi: Jira**

- **FR-010**: Otomasi → Jira MUST list a month's Content Plans, each with its state: found, greenlit, changed since Greenlight, issues created, changed after issues (G-32).
- **FR-011**: Before a Greenlight, the Hub MUST show rows per Bentuk against the Client's Registry quota. It MUST block Greenlight when the Client's Team has no Field Associate or Content Editor, the Client has no Jira component, or a row lacks its date, Topik or Bentuk. It MUST warn but allow Greenlight on a quota mismatch or a date outside the month (G-30).
- **FR-012**: Anyone with "Kelola otomasi" MUST be able to give a **Greenlight** to a Content Plan. No issue can be created from a plan without a current Greenlight (brief, G-11, G-30).
- **FR-013**: A plan that changes after its Greenlight and before its issues are created MUST show "Berubah sejak disetujui" and need a new Greenlight (G-5).
- **FR-014**: "Buat issue Jira" MUST create issues for several greenlit plans in one press, run in the background with visible progress, and **never** run on its own schedule (brief, G-31).
- **FR-015**: Creation MUST create one issue per row that has none and MUST **never** duplicate. A row already turned into an issue is not updated by a later press (G-5).
- **FR-016**: After creating an issue, the Hub MUST write its key as a link into the row's **Key** column, MUST NOT use **TicketID**, and MUST keep its own record of which row made which issue (G-8, G-12).
- **FR-017**: An erased or duplicated Key MUST be flagged for a person to fix and MUST **never** cause a second issue (G-12).
- **FR-018**: Anything Jira would reject (e.g. a line break in Topik) MUST be repaired automatically before sending (G-30).
- **FR-019**: When Jira rejects rows, the others MUST still be created and keyed; the rejected rows MUST be listed with Jira's reason and be the only rows the next press creates. **One** message MUST go to #eskala-otomasi only when a batch had failures (G-31).
- **FR-020**: Every **15 minutes**, the Hub MUST check each Content Plan that has issues. A changed keyed row is flagged "berubah setelah issue dibuat" with old → new, and **one** comment listing the change is posted on its issue. A deleted keyed row is flagged "dihapus dari plan". A new row without a Key waits for the next press (G-9, G-13).
- **FR-021**: The Hub MUST NOT change any Jira issue field after creation, and MUST **never** shelve an issue. Its only writes to existing issues are comments (G-1, G-13, G-45).
- **FR-022**: The issue's content fields, assignment and dates MUST be filled as today's automation fills them, with the Field Associate and Content Editor taken from the Registry Team (brief, A-1).

**Otomasi: Harvest**

- **FR-030**: **One** monthly Harvest MUST go through every own and competitor social account of every active Eskala Client in the Registry, one account at a time, **30 minutes apart**, each over the last **31 days**. It replaces the per-account schedules (brief, G-7).
- **FR-031**: An account's first Harvest MUST cover **90 days** (G-7).
- **FR-032**: Adding, removing or deactivating an account or Client in the Registry MUST change the next Harvest accordingly, with no schedule edited (G-7).
- **FR-033**: Otomasi → Harvest MUST list every account with its Client, own or competitor, last harvested, posts collected, and status (blocked by the platform, not found, not yet harvested), and offer **"Jalankan sekarang"** per account to anyone with "Kelola otomasi" (G-7, G-32).
- **FR-034**: Each harvested post MUST be reviewable in the Hub per account and month, with its link, caption and numbers, and **review marks** (multiple allowed, default none): **Iklan**, **Tidak relevan**, **Bukan konten akun ini**. Anyone with "Kelola otomasi" can set them (brief, G-6, G-17).
- **FR-035**: A post with any review mark MUST be excluded from the performance report's averages and top posts and from songbird's examples. It MUST still count in the month's post count, stay stored, and be listed separately (collapsed) in the report (G-17).
- **FR-036**: Ad flags in the Account Social Harvest sheets MUST be brought over once as **Iklan**. After that, those sheets MUST NOT be written (brief, G-6).

**Otomasi: overview**

- **FR-040**: Otomasi MUST show one card per automation (Jira, Harvest) with last run, next run, result, failures and **90 days** of history (G-32).

**Laporan: delivery and stations**

- **FR-050**: The Hub MUST keep Jira's Content issues and their status history from **1 January 2026**, refreshed every **15 minutes**. Each Laporan page MUST show "diperbarui HH:MM" (G-16, G-40).
- **FR-051**: Laporan → Delivery MUST show per Client per month: planned, issues created, **Published**, **Late**, **published late**, cancelled and On Hold content, as defined in the glossary and G-4. Shelved and On Hold content is **never** Late: both are managers' decisions (G-4, G-52).
- **FR-052**: The Hub MUST place content at a station by its Jira status using exactly the mapping in User Story 6, scenario 1. Renamed statuses keep their station; On Hold and Shelved belong to no Station (G-14, G-19, G-52).
- **FR-053**: A station's person MUST come from the issue's Field Associate (B, E) or Content Editor (C), or from the Client's Team (A Content Planner, D Quality Assurance). The Jira assignee MUST NOT be used (G-19).
- **FR-054**: Per station, per person and per Client team per month, Laporan MUST show content in, content moved forward, returns (by origin station, with Return Reason), time held (median and longest), and what is still waiting and for how long (G-10, G-15, G-19, G-35).
- **FR-055**: A return MUST count against the origin station of its Defect Category. A return with none MUST be shown "tanpa kategori" under the station that sent it back and counted against no one. The Hub MUST **never** charge a return to anyone by itself (G-15, G-35).
- **FR-056**: Per Client team per month, Laporan MUST show the client first-pass rate and average QA rounds as defined in G-36, against the v2.1 thresholds (first pass **≥60%**, QA rounds **≤1.5**), and whether both are met (G-21, G-36).
- **FR-057**: Content with **more than 3 rounds** between two stations MUST be flagged for managerial review, not charged (G-21).

**Laporan: performance**

- **FR-060**: Laporan → Performa MUST show per Client per month, own accounts beside the competitors' average: posts by type; total and average views, likes, comments, shares; engagement rate (÷ views for video, ÷ followers where known); top 5 posts; change from last month (G-33).
- **FR-061**: A missing follower count MUST be stated, **never** shown blank (G-33).
- **FR-062**: The performance report is internal; no client-facing export is produced (G-41).

**Laporan: incentive (behind "Lihat poin insentif")**

- **FR-070**: The Hub MUST read judged Events from Jira and compute points **only** from the fields listed in [jira-fields.md](jira-fields.md), by their Jira names: Event Type, Violation Judgment, Event Observer, Reporter's Own Mistake/Error, Problem/Error Solved, Known by the Person, Violation Category, Excellence Category, Direct Sanction Violation, Person. Events carry no Defect Category (G-26, G-43, G-50).
- **FR-071**: Violation and Self-report points MUST be −(10 × P2 × P3), and **0** unless Violation Judgment = Kelalaian. Event Observer may hold both values, which counts as External (P2 = 3). Excellence points MUST follow each ticked category (+1 or +3), added up (G-51). Points MUST be shown **negative** for Violations and **positive** for Excellences everywhere (G-26, G-45).
- **FR-072**: An Event missing a needed field, or with several values in a single-choice field (Event Type, Violation Judgment, the Yes/No fields), MUST be shown "belum lengkap" and count **nothing**. The Hub MUST **never** guess a value (G-26).
- **FR-073**: An Event concerns its **Person**, else its Assignee, else it is "belum lengkap" (G-34).
- **FR-074**: Once an Event is judged, the Hub MUST post **one** comment with its computed points on it, changing no field (G-45).
- **FR-075**: Each Person MUST have an optional start date ("Mulai bekerja"), set by whoever holds "Kelola orang". Events in a Person's first **30 calendar days** carry no points, except §4.3 direct-sanction violations (G-27).
- **FR-076**: Only Events judged on or after **27 September 2026** MUST count toward points and sanctions. Earlier Events are shown for reference only (G-47).
- **FR-077**: An Event under appeal MUST be shown on hold until judged again, and only the final judgement counts (G-26, G-38).
- **FR-078**: Per person per month, the incentive view MUST show Violation points and Excellence points (never netted), the sanction the ladder gives, and the Events behind them. It MUST also show whether the person qualifies for the team reward (at least **60%** of their Client teams meet both thresholds). It MUST NOT show rupiah amounts for Tunjangan or reward payouts (G-21).
- **FR-079**: Managers with "Lihat poin insentif" MUST be able to record each teguran lisan and SP issued (person, level, date, category code). The Hub uses these for the active SP, **90-day** validity and same-code teguran counts (G-29).

**Sanctions (ADR-0001)**

- **FR-080**: On **working day 2** of each month, the Hub MUST work out each Eskala person's sanction for the previous month from the v2.1 §3.2 ladder (User Story 9, scenario 1), and send **one** Slack message to the Brand Manager: "sanksi bulan ini siap ditinjau, batas tahan: hari kerja ke-3" (G-38, G-42).
- **FR-081**: A manager with "Lihat poin insentif" MUST be able to **hold** a sanction with a written reason until the end of **working day 3**. A sanction nobody held MUST be recorded as **issued** at the end of working day 3 (G-38).
- **FR-082**: A sanction resting on an Event under appeal MUST be held automatically until the appeal is decided (G-38).
- **FR-083**: "Proses PHK" MUST only be flagged for a person to start, and MUST **never** be issued or written by the Hub (G-38, G-44).
- **FR-084**: A judged H2 Event with a Direct Sanction Violation MUST follow the same hold-and-issue path immediately, with the sanction its item names (4.3.1 → SP1, 4.3.2 → Peringatan Pertama dan Terakhir, 4.3.3 → flagged for termination). An H2 Event without it MUST **never** be handled automatically and shows "Sanksi langsung, tentukan manual" (G-37, G-38, G-43).
- **FR-085**: Every issued SP1, SP2, SP3 or Peringatan Pertama dan Terakhir MUST produce a letter from [sp-letter-template.md](sp-letter-template.md) with:
  - the fixed details in G-48, including the number format `{urut}/SP/ESK/{bulan romawi}/{tahun}`;
  - blank signature lines for the CEO and the Brand Manager (the CEO alone when the person is the Brand Manager).

  The letter is saved as a Google Doc in **Company > HR > Surat Peringatan**, one file per letter. A teguran lisan gets no letter. The Hub MUST **never** send it to the employee (G-44, G-48, G-49).
- **FR-086**: Nothing besides FR-019 and FR-080 is pushed to Slack or WhatsApp by this feature; lateness alerts are out of scope (G-42).

### Key Entities *(names from CONTEXT.md)*

- **Content Plan**: one Client's month. In this capability it carries a Greenlight state, the Hub's record of which row made which issue, and change flags.
- **Greenlight**: a Manager's go-ahead on a Content Plan: who, when, and the plan as it stood then, so a later change can be detected.
- **Content** (a Jira Content issue): one plan row once created, with its status history, publication date, Field Associate and Content Editor, and its Returns.
- **Station**: A–E; the status mapping and the role behind each.
- **Return**: one move back to an earlier Station, with Defect Category (origin station) and Return Reason when recorded through the Return Screen.
- **Harvest**: one run for one account: when, the window, posts collected, outcome. The account list comes from the Registry.
- **Review mark**: Iklan, Tidak relevan or Bukan konten akun ini on one harvested post, with who set it and when.
- **Event**: a judged Jira Event as read by the Hub: its fields, the Person it concerns, computed points (signed), completeness, appeal state.
- **Sanction**: a teguran lisan, SP1–SP3, Peringatan Pertama dan Terakhir or PIP for one Person: level, category code, issue date, 90-day validity, and state (computed, held with reason, issued, suspended by appeal). Recorded by a manager or issued by the Hub.
- **SP letter**: the document produced for an issued SP, with its number and file.
- **Person**: gains a start date ("Mulai bekerja") and the three new Permissions.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Across the first **three** months of use, **zero** duplicate Jira issues are created from Content Plans. Today a second run duplicates every issue.
- **SC-002**: **100%** of issues created through the Hub carry their key in their plan row within one minute of creation.
- **SC-003**: A change to a keyed plan row is flagged, and commented on its issue, within **15 minutes** in at least **95%** of cases.
- **SC-004**: A manager greenlights and creates a month's issues for **10** Clients in under **15 minutes** of their own time, without opening a terminal.
- **SC-005**: Every monthly Harvest covers exactly the Registry's active Eskala accounts: **0** missed, **0** extra. An account added to the Registry is harvested at the next run with no schedule edited.
- **SC-006**: For a sampled month, the Delivery report's Published, Late and cancelled counts per Client match a hand count from Jira **exactly**.
- **SC-007**: For a sampled month, computed points match a manager's hand calculation under v2.1 for **100%** of complete Events. **0** incomplete Events are counted.
- **SC-008**: The monthly rekap (v2.1 §7.1 step 2), done by hand today, needs no manual summing; managers only review and hold.
- **SC-009**: **0** sanctions are issued without the working-day-2 notice and the working-day-3 hold window. **0** "proses PHK" are issued by the Hub.
- **SC-010**: Every issued SP has its letter in the restricted folder by the end of **working day 3**.

## Clarifications

### Session 2026-09-26

- Grilled before specification: 49 decisions in [grill.md](grill.md), confirmed by the user at close. Every `(G-n)` above cites one. The Incentive Framework rules come from v2.1 (G-25).

## Assumptions

- **Out of scope** (G-1, G-24, G-39, G-41):
  - songbird (including its monthly schedule bug);
  - any change to how roach collects posts;
  - editing or moving Jira issues from the Hub;
  - creating Events from the Hub (Events stay created and judged in Jira);
  - the client-facing monthly report (DEFERRED H-3);
  - Venyu;
  - lateness alerts;
  - rupiah amounts for Tunjangan and reward payouts.
- **The sheet copy keeps running**: songbird still reads the Clients and Hashmaps sheets, so the Registry → sheet copy (spec 008) continues until songbird switches (G-2).
- **Constitution amendment**: the constitution's Data Management section makes `hashmap.py` the source for static mappings. Moving the Jira automation and the Harvest to the Registry needs an amendment (a version bump), made in this spec's plan before implementation (brief).
- **Jira screens, changed by the user, outside the Hub** (G-20, G-26, G-37, G-43):
  - the Event verdict screen now has the fields in G-43;
  - Events carry no Defect Category, and Event Observer and Excellence Category stay multi-choice (G-50, G-51);
  - the Return Screen on Content issues carries Defect Category and Return Reason (G-35).

  Points are only as correct as these screens.
- **One category per Event**: a mistake that fits two categories is two Events (G-28).
- **Incentive Framework v2.1** is the rulebook; a later version is a spec change (G-25).
- **Existing people without a start date** get no adaptation period (G-27). G-22 (the v2.0 "amnesty") is dropped: superseded by G-27 (v2.1 Masa Adaptasi).
- **Working days** are Monday to Friday, excluding Indonesian national public holidays.
- **Jira access** is the same Noktah account the automation uses today, and Jira and Google calls cost nothing beyond quota (G-13).
- **Hub-internal decisions** (G-46: ADR-0001) are recorded as an ADR, not repeated here.
