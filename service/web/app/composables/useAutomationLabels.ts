import type { HarvestAccount, PlanFlagKind, PlanStatus, ReviewMark, SanctionLevel, SanctionState, EventState } from '~/types/hub'

/** Bahasa labels and badge colours for Otomasi and Laporan (spec 009), in one place. */
export type Tone = 'success' | 'error' | 'warning' | 'info' | 'neutral' | 'primary'
export interface Label { label: string, color: Tone }

const fallback = (key: string | null | undefined): Label => ({ label: key ?? '—', color: 'neutral' })

export const PLAN_STATUS: Record<PlanStatus, Label> = {
  found: { label: 'Siap di-Greenlight', color: 'info' },
  greenlit: { label: 'Sudah di-Greenlight', color: 'success' },
  changed_since_greenlight: { label: 'Berubah setelah Greenlight', color: 'warning' },
  partly_created: { label: 'Sebagian issue sudah dibuat', color: 'primary' },
  issues_created: { label: 'Issue sudah dibuat', color: 'neutral' },
  changed_after_issues: { label: 'Berubah setelah issue dibuat', color: 'warning' },
  missing: { label: 'Content Plan tidak ditemukan', color: 'error' },
  ambiguous: { label: 'Lebih dari satu file cocok', color: 'error' },
  unreadable: { label: 'Kolom wajib tidak ada', color: 'error' },
  not_scanned: { label: 'Belum diperiksa', color: 'neutral' }
}
export const planStatus = (s: string | null | undefined): Label => PLAN_STATUS[s as PlanStatus] ?? fallback(s)

export const FLAG_KIND: Record<PlanFlagKind, Label> = {
  changed_after_issue: { label: 'Berubah setelah issue dibuat', color: 'warning' },
  deleted_from_plan: { label: 'Dihapus dari Content Plan', color: 'error' },
  key_erased: { label: 'Key terhapus', color: 'error' },
  key_duplicated: { label: 'Key ganda', color: 'error' },
  key_unknown: { label: 'Key tidak dikenal', color: 'warning' }
}
export const flagKind = (k: string | null | undefined): Label => FLAG_KIND[k as PlanFlagKind] ?? fallback(k)

export const COMMENT_STATE: Record<string, Label> = {
  posted: { label: 'Komentar terkirim', color: 'success' },
  pending: { label: 'Menunggu', color: 'neutral' },
  failed: { label: 'Gagal', color: 'error' }
}

/** The Harvest's last outcome, or "Belum pernah" (FR-033). */
export function harvestOutcome(a: Pick<HarvestAccount, 'last_outcome' | 'status'>): Label {
  switch (a.last_outcome) {
    case 'collected': return { label: 'Terkumpul', color: 'success' }
    case 'nothing_new': return { label: 'Tidak ada post baru', color: 'neutral' }
    case 'blocked': return { label: 'Diblokir platform', color: 'error' }
    case 'not_found': return { label: 'Akun tidak ditemukan', color: 'error' }
    case 'failed': return { label: 'Gagal', color: 'error' }
    case 'skipped': return { label: 'Dilewati', color: 'neutral' }
    default: return { label: 'Belum pernah', color: 'warning' }
  }
}

export const HARVEST_STATUS_FILTER = [
  { label: 'Semua status', value: 'all' },
  { label: 'Terkumpul', value: 'ok' },
  { label: 'Diblokir platform', value: 'blocked' },
  { label: 'Akun tidak ditemukan', value: 'not_found' },
  { label: 'Gagal', value: 'failed' },
  { label: 'Belum pernah', value: 'never' }
]

export const ROLE_LABEL: Record<string, Label> = {
  own: { label: 'Milik klien', color: 'primary' },
  competitor: { label: 'Pesaing', color: 'neutral' }
}

export const REVIEW_MARKS: { key: ReviewMark, label: string }[] = [
  { key: 'iklan', label: 'Iklan' },
  { key: 'tidak_relevan', label: 'Tidak relevan' },
  { key: 'bukan_konten_akun_ini', label: 'Bukan konten akun ini' }
]
export const markLabel = (k: string) => REVIEW_MARKS.find(m => m.key === k)?.label ?? k

export const CONTENT_TYPE: Record<string, string> = {
  video: 'Video', image: 'Gambar', carousel: 'Carousel', story: 'Story', stories: 'Story'
}
export const contentType = (t: string | null | undefined) => (t ? CONTENT_TYPE[t] ?? t : '—')

export const SANCTION_LEVEL: Record<SanctionLevel, string> = {
  teguran_lisan: 'Teguran lisan',
  sp1: 'SP1',
  sp2: 'SP2',
  sp3: 'SP3',
  peringatan_terakhir: 'Peringatan Pertama dan Terakhir',
  phk_flag: 'Proses PHK (ditandai)'
}
export const sanctionLevel = (l: string) => SANCTION_LEVEL[l as SanctionLevel] ?? l

export const SANCTION_STATE: Record<SanctionState, Label> = {
  computed: { label: 'Menunggu terbit', color: 'info' },
  held: { label: 'Ditahan', color: 'warning' },
  issued: { label: 'Diterbitkan', color: 'error' },
  on_appeal: { label: 'Sedang banding', color: 'warning' },
  flagged: { label: 'Ditandai', color: 'error' }
}
export const sanctionState = (s: string) => SANCTION_STATE[s as SanctionState] ?? fallback(s)

export const EVENT_STATE: Record<EventState, Label> = {
  counted: { label: 'Dihitung', color: 'primary' },
  zero: { label: 'Poin 0', color: 'neutral' },
  belum_lengkap: { label: 'Belum lengkap', color: 'warning' },
  reference: { label: 'Sebelum 27 Sep 2026 (catatan)', color: 'neutral' },
  adaptation: { label: 'Masa adaptasi', color: 'neutral' },
  on_hold: { label: 'Sedang banding', color: 'warning' },
  not_judged: { label: 'Belum dinilai', color: 'neutral' }
}
export const eventState = (s: string) => EVENT_STATE[s as EventState] ?? fallback(s)
