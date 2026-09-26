# Jira fields for Event judging (Verdict Screen)

What the ESKL **Event** verdict screen must hold so the Hub can compute points and sanctions
under Incentive Framework v2.1 (§6.3, §6.4–6.6, §4.3). Put the screen on the *Forward to
Judgment* and *Decide Appeal* transitions. Source: spec 009 grill G-26, G-37.

Nothing on this screen needs to be required in Jira. Which fields matter depends on the
Event Type, and Jira can't make a field required only for some types. The Hub checks each
judged Event itself and marks one that is missing a needed field as "belum lengkap".

## Fields

| # | Field | Jira field type | Options | Needed when | Status today |
|---|---|---|---|---|---|
| 1 | Event Type | Select List (single choice) | Violation · Excellence · Self-report | always | on verdict screen; not on the create screen |
| 2 | Violation Judgment (P1) | Select List (single choice) | Kelalaian · Catatan sistem · Skill gap dalam 30 hari · Diajukan sebelum deadline | Violation, Self-report | on verdict screen |
| 3 | Event Observer (P2) | Checkboxes (several allowed, G-51) | Internal (Staff) · External (Client) | Violation, Self-report | exists; both ticked = the client knew (P2 = 3) |
| 4 | Reporter's Own Mistake/Error | Select List (single choice) | Yes · No | Violation, Self-report | exists |
| 5 | Problem/Error Solved | Select List (single choice) | Yes · No | Violation, Self-report | exists |
| 6 | Known by the Person (v2.1 "Sudah Tahu Sebelumnya") | Select List (single choice) | Yes · No | Violation, Self-report | on verdict screen |
| 7 | Defect Category | Select List (cascading) | see below | **not on Events** (G-50): only on the Content ticket's Return Screen | Return Screen only |
| 8 | Violation Category | Select List (cascading) | see below | a process, time, communication or behaviour violation | exists (cascading) |
| 9 | Excellence Category | Checkboxes (several allowed, G-51) | see below | Excellence | exists; several ticked = their points add up |
| 10 | Direct Sanction Violation | Select List (single choice) | see below | Violation Category = H2 | on verdict screen |
| 11 | Person | User Picker (single user) | — | always | exists |

A Violation carries a Violation Category (G-50).
Points are **not** a Jira field: the Hub computes them (10 × P2 × P3, or the Excellence table).

**Decided 2026-09-26 (G-50, G-51):** Events carry no Defect Category, and Event Observer and
Excellence Category stay multi-choice. The Hub reads both ticked observers as "the client knew"
and adds up several Excellence categories.

## Defect Category (cascading: letter, then code)

- **A — Brief & Content Plan (Content Planner)**
  - A1 Brief tidak lengkap (tujuan, CTA, referensi, atau spesifikasi kosong)
  - A2 Brief tidak sesuai brand guideline / arahan Klien
  - A3 Copy/caption dari brief salah (typo, nama, nomor, klaim medis)
  - A4 Shoot guide tidak jelas atau tidak bisa dieksekusi
  - A5 Informasi Klien kedaluwarsa (harga, promo, jadwal, nama dokter)
- **B — Footage (Field Associate)**
  - B1 Footage kurang / tidak sesuai shoot guide
  - B2 Kualitas teknis kurang (blur, goyang, audio, cahaya, framing)
  - B3 File kurang, rusak, atau salah folder/penamaan
  - B4 Wajah atau identitas pasien terekam tanpa persetujuan
- **C — Design, Edit, & Copy (Content Editor)**
  - C1 Tidak sesuai brief
  - C2 Tidak sesuai brand guideline (logo, font, warna, layout)
  - C3 Copy/teks salah pada desain (typo, nama, nomor, klaim)
  - C4 Spesifikasi teknis salah (rasio, resolusi, safe area, durasi, format ekspor)
  - C5 Revisi sebelumnya tidak dikerjakan / tidak lengkap
  - C6 Salah versi file (draft terkirim sebagai final, logo lama)
- **D — Quality Assurance**
  - D1 Kesalahan lolos ke Klien
  - D2 Catatan revisi tidak jelas ("perbaiki lagi" tanpa detail)
- **E — Publication (Field Associate)**
  - E1 Revisi Klien (permintaan Klien, bukan kesalahan internal)
  - E2 Salah akun, salah jadwal, atau salah format saat posting
  - E3 Caption atau tag saat posting berbeda dari versi yang sudah di-ACC
- **Z — Lainnya**
  - Z Lainnya (jelaskan di Description)

## Violation Category (cascading: letter, then code)

- **W — Proses & Ketepatan Waktu**
  - W1 Brief masuk terlambat dari jadwal Content Plan (Content Planner)
  - W2 Footage di-upload terlambat dari batas Role Responsibility (Field Associate)
  - W3 Konten diserahkan tanpa self-check: checklist kosong atau tidak jujur (Content Editor)
  - W4 Pengembalian tanpa alasan / tanpa Defect Category (QA)
  - W5 ACC tanpa mengecek ulang poin revisi (QA)
  - W6 Konten menunggu di QA lebih dari 8 jam kerja tanpa kabar ke PM (QA)
  - W7 Jadwal publish terlewat (Field Associate)
  - W8 Feedback Klien tidak diteruskan / terlambat diteruskan (Field Associate)
  - W9 Komplain lisan Klien tidak dicatat di grup pada hari yang sama (Field Associate)
  - W10 Briefing sheet lewat dari H-3 / talent belum dikonfirmasi di H-2 (Field Associate)
  - W11 Visit report tidak dikirim di hari yang sama (Field Associate)
- **F — Communication & Coordination**
  - F1 Pesan internal tidak dibalas ≤2 jam kerja
  - F2 Pesan Klien tidak dibalas ≤1x24 jam
  - F3 Eskalasi tidak dijawab dalam batas Role Responsibility
  - F4 Risiko terlambat tidak diberitahu sebelum deadline
  - F5 Jira tidak diperbarui hari yang sama / pekerjaan tanpa tiket Jira
  - F6 Menerima brief/footage/desain yang bermasalah tanpa dikembalikan, lalu masalahnya baru muncul di station berikutnya
- **G — Managerial & Admin**
  - G1 Tugas overdue tidak ditindaklanjuti dalam 1 hari kerja
  - G2 Laporan bulanan / Brand Guideline terlambat atau tidak diserahkan sebelum produksi
  - G3 Event dicatat tanpa bukti, atau tidak dicatat saat dilaporkan
  - G4 Prospek tidak dijawab >24 jam / outreach tidak tercatat seminggu
  - G5 Deadline diubah tanpa alasan tercatat
  - G6 Event tidak dinilai dalam 1 hari kerja
- **K — Presency**
  - K1 Terlambat tanpa kabar
  - K2 Tidak masuk tanpa kabar
  - K3 Izin atau cuti tanpa handover
  - K4 Tidak hadir di rapat terjadwal atau 1-on-1 tanpa kabar
- **S — Security**
  - S1 Peralatan rusak atau hilang tidak dilaporkan di hari yang sama
- **H — Attitude & Behavior**
  - H1 Pembalasan terhadap pelapor (30 poin + peringatan pertama dan terakhir)
  - H2 Pelanggaran dengan sanksi langsung (isi juga "Direct Sanction Violation")
  - H3 Pelanggaran Peraturan Perusahaan Pasal 33 lainnya (SARA, penggunaan aset untuk kepentingan pribadi, dll.)
- **Z — Lainnya**
  - Z Lainnya (jelaskan di Description)

## Excellence Category (single choice)

| Option | Points (Hub) |
|---|---|
| X1 Menangkap kesalahan dari station sebelumnya sebelum sampai ke Klien | +1 |
| X2 Usul atau ide yang dipakai tim atau Klien | +1 |
| X3 Sebulan tanpa kesalahan berulang, atau first pass di atas ambang jabatan | +1 |
| X4 Pujian tertulis dari Klien | +3 |
| X5 Usulan layanan tambahan diterima Klien (upsell) | +3 |
| X6 Klien bertahan berkat upaya sendiri | +3 |
| X7 Perbaikan sistem dengan penghematan terukur | +3 |
| X8 Excellence lain yang tidak ada di daftar, dengan bukti | +1 |

## Direct Sanction Violation (single choice, only with H2)

The prefix decides the sanction: **4.3.1** straight to SP1, **4.3.2** straight to the first and
last warning (equal to SP3), **4.3.3** termination without SP.

- 4.3.1-1 Tidak masuk kerja tanpa kabar (≥ 3 hari berturut-turut)
- 4.3.1-2 Menolak perintah kerja yang wajar dari atasan tanpa alasan yang sah
- 4.3.1-3 Menyimpan file kerja di perangkat pribadi atau membagikan akses akun Klien ke luar tim tanpa izin
- 4.3.1-4 Merekam wajah atau identitas pasien tanpa persetujuan tertulis (belum dipublikasikan)
- 4.3.1-5 Mengubah deadline, status, atau isi tiket Jira milik orang lain tanpa wewenang
- 4.3.2-1 Posting ke akun Klien tanpa ACC Klien
- 4.3.2-2 Mengirim konten kepada Klien tanpa ACC QA
- 4.3.2-3 Mempublikasikan konten yang memuat wajah atau identitas pasien tanpa persetujuan tertulis
- 4.3.2-4 Menggunakan musik atau aset tanpa lisensi sampai konten di-takedown atau Klien menerima teguran hak cipta
- 4.3.2-5 Membalas dendam kepada pelapor dalam bentuk apa pun
- 4.3.2-6 Menerima imbalan dalam bentuk apa pun dari Klien, pemasok, atau pihak ketiga terkait pekerjaan
- 4.3.2-7 Bekerja di usaha sejenis atau menerima pekerjaan sampingan sejenis tanpa izin tertulis Direktur
- 4.3.2-8 Datang atau bekerja dalam keadaan mabuk minuman keras
- 4.3.2-9 Mengulangi pelanggaran dari daftar Langsung SP1 saat SP1 masih berlaku
- 4.3.3-1 Memalsukan data, bukti, laporan, tiket Jira, catatan kehadiran, atau dokumen saat rekrutmen
- 4.3.3-2 Mencuri, menggelapkan, atau menipu terkait uang atau barang milik Perusahaan, Klien, atau rekan kerja
- 4.3.3-3 Mengambil alih atau menyalahgunakan akun media sosial Klien
- 4.3.3-4 Membocorkan data pasien, data Klien, atau rahasia Perusahaan kepada pihak luar
- 4.3.3-5 Mengambil Klien atau calon Klien Perusahaan untuk kepentingan pribadi atau pihak lain
- 4.3.3-6 Memakai, membawa, atau mengedarkan narkotika di tempat kerja atau pada waktu kerja
- 4.3.3-7 Menganiaya, mengancam, mengintimidasi, atau melakukan pelecehan
- 4.3.3-8 Melakukan perjudian atau perbuatan asusila di tempat kerja
- 4.3.3-9 Dengan sengaja merusak aset Perusahaan atau Klien, atau menghapus file kerja
- 4.3.3-10 Membujuk rekan kerja atau Klien melakukan perbuatan yang melanggar hukum

## Return Screen (already added, 2026-09-26)

On every return transition of a **Content** issue: **Defect Category** (the cascading list
above, required) and **Return Reason** (Paragraph text, required).
