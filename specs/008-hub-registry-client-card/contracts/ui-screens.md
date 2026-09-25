# Contract: Hub screens (spec 008)

All text is in Bahasa. The layout is a `UDashboardGroup` with a sidebar that collapses on desktop and becomes a drawer on phones. Every page is listed in the UI sweep's `ROUTES`, with fixture states `full`, `empty` and `down` (and `pending`/`no_access` where noted). The web app calls only `/api/hub/**`.

## Sidebar (role-aware)

- Klien, and Persetujuan with a count badge (Brand Manager and Owner only)
- Orang
- Catatan lama (only while unmatched or unprocessed old notes exist)
- Footer: the signed-in Person's name, role and Noktah Brand

Items the role can't use are hidden, not disabled.

## `/`: Klien

- A table of Clients: name, status badge, Noktah Brand (Owner only), card completeness (Profil x/5 · Guideline x/10), pending approvals, team (AE, FA).
- Filter by status (default *Aktif*); search by name.
- The *Tambah klien* button needs edit_registry.
- States: `full`, `empty` ("Belum ada klien"), `down`.

## `/clients/[id]`: a Client

The header shows the name, status, Noktah Brand, internal badge and completeness, plus the *Intake* button (run_intake). Tabs:

1. **Ringkasan**: the Summary ("dibuat otomatis · tanggal"; out-of-date and cap-paused notes), open Requests (the top 5), and Profil and Guideline completeness with the missing required fields listed.
2. **Profil**: the 10 fields, each shown as a card with value, valid-until (expired badge), source, set by/at and an evidence link. *Ubah* and *Koreksi* buttons need edit_profil. *Riwayat* opens a slideover with the history. Empty required fields are highlighted.
3. **Guideline**: the 10 sections, grouped with brand-theory helper text.
   - Archetype: a select from the 12.
   - Traits and voice scales: 1–5 sliders (`USlider`).
   - A pending change shows "menunggu persetujuan", with the approved value still displayed.
   - *Salin dari klien lain* opens a slideover (same Noktah Brand).
   - Approve and reject buttons appear for the Brand Manager and Owner.
4. **Permintaan**: a list of Requests, newest first, with a status filter. It has a *Tambah* button, a status change (a reason is required for *ditolak*), and a link field.
5. **Registry**: status, quotas, folders and Jira component (a form); the team (5 role selects from People); social accounts (own and competitor), with add and deactivate.
6. **Riwayat**: Registry change history.

States: `full`, `empty` (a new Client with no Card), `down`, `pending` (Guideline changes waiting).

## `/clients/[id]/intake`: Intake

1. **Input**: tabs Teks, Screenshot, Google Doc and PDF, with a *Proses* button showing a loading state of up to about 60 s. The monthly AI usage appears as a small meter; when the cap is reached, the button is disabled with an explanation.
2. **Proposals**: one card per Proposal:
   - target field and current → proposed value (a diff)
   - the quoted excerpt, speaker and date
   - flags as badges: "dari gambar — cek manual", "belum dikonfirmasi PIC", "kutipan tidak ditemukan", "harga tanpa satuan", "menyebut klien lain", "bertentangan", "bukan Bahasa"
   - the required ticks as checkboxes: *Sudah dicek manual*, *PIC sudah konfirmasi*
   - *Terima*, *Ubah lalu terima* and *Tolak* buttons

   A Request Proposal shows as "Permintaan baru". "Tidak ada yang perlu diperbarui" appears when nothing is found.

States: `full`, `empty` (nothing found), `down`, `failed` ("AI gagal memproses — coba lagi"), `cap` (paused).

## `/approvals`: Persetujuan (Brand Manager and Owner)

Pending Guideline changes, grouped by Client, each showing current vs proposed, who proposed it and when, with *Setujui* and *Tolak* (a reason is required). States: `full`, `empty` ("Tidak ada yang menunggu"), `down`.

## `/people` and `/people/[id]`: Orang

- **List:** name, roles (with Noktah Brand), emails and status. *Tambah orang* needs manage_people.
- **Detail:** emails (add and remove), roles (grant and end, following the rules), Jira and Slack IDs, *Tandai keluar*, and a history of this Person's changes.

States: `full`, `empty`, `down`.

## `/notes`: Catatan lama

- Processing progress (processed, matched, unmatched, remaining).
- The "belum ada klien" list: each note shows its text and a client picker, with *Tetapkan* and *Buang*.

States: `full`, `empty`, `down`.

## `/no-access`

"Anda belum punya akses ke Noktah Hub. Minta Brand Manager Anda untuk menambahkan email {email}." There is no sidebar and no data.
