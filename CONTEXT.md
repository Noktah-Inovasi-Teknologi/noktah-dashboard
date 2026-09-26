# Noktah

The vocabulary of Noktah's work: Eskala's content agency operations, and the automation and Hub that serve them. One word per thing; the words to avoid are listed under each term.

## Language

### The company

**Noktah Brand**:
One of Noktah's business lines, each run by its own Brand Manager: Eskala (the content agency) and Venyu (the software product). Every Client belongs to exactly one Noktah Brand. Noktah itself is the company group, not a Noktah Brand.
_Avoid_: Brand (on its own, for Eskala or Venyu), lini bisnis, divisi

**Unit**:
Where a Person works: Noktah (the company group, for roles that span every Noktah Brand, such as Owner and Sales & Marketing), Eskala or Venyu. A Person may be in several Units; their permissions apply to the Clients of their Units, and Noktah covers every Noktah Brand.
_Avoid_: divisi, departemen, tim (that is a Client's Team), Brand (for Noktah)

**Role**:
What a Person is in one Unit, from that Unit's catalog. Noktah: Owner, Sales & Marketing. Eskala: Brand Manager, Production Manager, Account Executive, Content Planner, Field Associate, Content Editor, Quality Assurance. Venyu: Brand Manager, Production Manager, Quality Assurance, Front-end Developer, Back-end Developer, DevOps, Mobile Developer. A role decides which Client team slots a Person may fill; apart from Owner it grants nothing in the Hub by itself (that is the Permission).
_Avoid_: jabatan, QC (write Quality Assurance), Project Manager (the role is Production Manager), PM (in written text)

**Permission**:
One thing a Person may do in the Hub (Izin): Masuk Hub, Ubah Registry, Ubah Profil, Setujui Guideline, Kelola permintaan, Jalankan Intake, Kelola orang, Kelola otomasi, Lihat laporan, Lihat poin insentif. Picking a role pre-ticks its usual permissions; the Person's own set is what counts. Nobody gives a permission they don't hold; the Owner holds them all.
_Avoid_: akses (loosely), hak, privilege

**Owner**:
The Noktah role that sees and can do everything across every Noktah Brand, and appoints Brand Managers.
_Avoid_: admin, superuser, CEO (as a Hub role)

**Brand Manager**:
The highest position within one Noktah Brand. Production Managers, Account Executives and the Noktah Brand's staff report to them, and they approve changes to a Client's brand.
_Avoid_: BM (in written text), head, lead

**Manager**:
Anyone who may sign in to the Hub: the Owner, or a Person with the Masuk Hub permission. Staff hold roles but usually no permissions, so they are not Managers.
_Avoid_: admin, user (for a Manager)

**Person**:
Someone at Noktah on the Hub's people list: a Manager or a staff member. One Person may sign in with several emails, be in several Units and hold several roles, each within one of their Units; history always names the Person, never just an email.
_Avoid_: user, worker, account (for a Person)

### Clients and their facts

**Client**:
One customer location a Noktah Brand makes content for. Each branch is its own Client, even when several branches belong to the same group. A Noktah Brand's own social media (e.g. Eskala's) is an **internal** Client.
_Avoid_: customer, account, cabang (as a separate concept)

**Registry**:
The Hub's list of Clients with their status, monthly quotas, folders, team and social accounts: the one place these are edited.
_Avoid_: client list (for the edited source), roster, Clients sheet (that is a copy)

**Client Card**:
Everything Noktah knows about one Client, in four parts: Profil, Guideline, Riwayat Permintaan (requests) and Ringkasan (summary).
_Avoid_: knowledge base, profil klien (for the whole card), client data, fakta klien

**Profil**:
The facts about who a Client is that content must get right: names, contacts, products and prices, people who appear, audience, PIC, production rules. Each fact has a valid-until date, a source and a history.
_Avoid_: data klien, info

**Guideline**:
The Client's brand as content must express it: positioning, personality, voice, visuals, required elements and banned claims. Every change needs the Brand Manager's approval.
_Avoid_: brand book (for the card part), SOP, aturan konten

**Request**:
One thing a Client asked for, recorded word for word with its date, who asked, where, and its status (baru, diproses, selesai, ditolak). Shown on the card as Riwayat Permintaan.
_Avoid_: task, tiket (a Request may link to a Jira ticket but is not one), revisi

**Summary**:
A short overview of a Client written by AI from the confirmed parts of its Client Card only, shown as Ringkasan. A view, never a source; never edited by hand.
_Avoid_: profile, notes

**brand**:
A Client's own identity as content must show it: logo, tagline, tone of voice, banned claims. Always the Client's, never Noktah's.
_Avoid_: identitas merek (in written text), Noktah Brand (for this)

**PIC**:
The one named person at a Client whose written word can change a fact on its Client Card.
_Avoid_: contact person, client (for the person)

**Intake**:
One submission of raw information (a pasted chat, notes, or a screenshot) for one Client, from which AI drafts Proposals.
_Avoid_: upload, import, ingestion

**Team**:
The staff assigned to one Client, one Person per team-slot role of its Noktah Brand's catalog (for Eskala: Account Executive, Content Planner, Field Associate, Content Editor and Quality Assurance; for Venyu: Production Manager and Quality Assurance). Each slot takes only a Person holding that role in the Client's Noktah Brand; ending the role releases the slot.
_Avoid_: crew, PIC (that is the Client's person)

**Approval**:
The Brand Manager's (or the Owner's) decision on a change to a Client's Guideline. Until it is approved, the card keeps showing the approved value.
_Avoid_: review (for this step), sign-off

**Proposal**:
A suggested change to one Client Card fact, with the exact excerpt it came from, that a person accepts, edits or rejects. Nothing reaches a Client Card as a Proposal alone.
_Avoid_: suggestion, AI result, draft (for a single change)

### Production and reports

**Content Plan**:
One Client's planned content for one month: a Google Sheet named "Content Plan - {Client} - {month}", one row per piece of content with its date, Bentuk and Topik. Each row becomes one Jira issue.
_Avoid_: plan (on its own), jadwal, kalender konten

**Greenlight**:
A Manager's go-ahead on a whole Content Plan, given in the Hub, without which none of its Jira issues can be created. A plan that changes before its issues are created needs a new Greenlight.
_Avoid_: Approval (that is a Guideline change), setujui (for this), ACC

**Station**:
One role's turn in making a piece of content, from the moment the work is theirs until they pass it on. There are five, lettered as in the Incentive Framework: A Planning (Content Planner), B Footage (Field Associate), C Editing (Content Editor), D Quality control (Quality Assurance), E Client & publication (Field Associate). The content's Jira status says which station holds it; an error is charged to its origin station.
_Avoid_: stage, step, divisi

**Return**:
A piece of content sent back to an earlier station because something was not approved. Counted, but only an Event can charge it to someone.
_Avoid_: revisi (for the movement itself), reject, bolak-balik (for a single return; that is several)

**Event**:
One judged record under the Incentive Framework, kept as a Jira Event ticket: a Violation or an Excellence, with its category, origin station, the person it concerns and its evidence. Only managers judge it; points follow from the judgement, written negative for a Violation (−30) and positive for an Excellence (+3).
_Avoid_: insiden, pelanggaran (for the ticket), laporan

**Sanction**:
What the Incentive Framework gives a Person for a month's Violations or a direct-sanction violation: a teguran lisan, an SP (1 to 3), a Peringatan Pertama dan Terakhir, or a PIP. Each is valid 90 days from issue and decides the next rung of the ladder.
_Avoid_: hukuman, punishment, SP (for a teguran lisan)

**Review mark**:
A reviewer's label on one harvested post that keeps it out of performance numbers: Iklan, Tidak relevan or Bukan konten akun ini. A post may carry several.
_Avoid_: tag, flag (loosely), ad flag (for all three)

**Competitor**:
A social account of another business that a Client is measured against, listed on the Client in the Registry and harvested like its own accounts. Written "pesaing" in the Hub.
_Avoid_: kompetitor (in the Hub), rival, reference (for this)

**Harvest**:
Collecting a social account's recent posts and their public numbers, for a Client's own accounts and its competitors'.
_Avoid_: scrape, crawl, sync (for this)

**Published**:
A piece of content whose Jira issue has reached *Published, Need Review* or *Done*.
_Avoid_: tayang (in reports), selesai (Done alone), posted

**Late**:
A piece of content whose publication date has passed without it being Published. Shelved (cancelled) and On Hold (paused) are managers' decisions and never Late; content Published after its date is **published late**.
_Avoid_: overdue, telat (in reports), missed
