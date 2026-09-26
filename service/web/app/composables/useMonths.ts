/**
 * Months and times for Otomasi and Laporan (spec 009). Months travel as `YYYY-MM`; times are
 * shown in WIB whatever the Worker's or browser's zone, so the server render and the page agree.
 */
const ZONE = 'Asia/Jakarta'

/** The month (`YYYY-MM`) it is now in WIB, moved by `offset` months. */
export function monthKey(offset = 0, now = new Date()): string {
  const [y, m] = new Intl.DateTimeFormat('en-CA', { timeZone: ZONE, year: 'numeric', month: '2-digit' })
    .format(now).split('-').map(Number)
  const d = new Date(Date.UTC(y!, m! - 1 + offset, 1))
  return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}`
}

/** "Oktober 2026". */
export function monthLabel(key: string | null | undefined): string {
  if (!key || !/^\d{4}-\d{2}/.test(key)) return key ?? ''
  return new Intl.DateTimeFormat('id-ID', { month: 'long', year: 'numeric', timeZone: 'UTC' })
    .format(new Date(`${key.slice(0, 7)}-01T00:00:00Z`))
}

/** Every month from `from` to `to` (both `YYYY-MM`), newest first, for a month picker. */
export function monthItems(from: string, to: string): { label: string, value: string }[] {
  const out: { label: string, value: string }[] = []
  let [y, m] = to.split('-').map(Number) as [number, number]
  const [fy, fm] = from.split('-').map(Number) as [number, number]
  while (y > fy || (y === fy && m >= fm)) {
    const key = `${y}-${String(m).padStart(2, '0')}`
    out.push({ label: monthLabel(key), value: key })
    m -= 1
    if (m === 0) {
      m = 12
      y -= 1
    }
  }
  return out
}

/** A month from the page's `?month=`, if it is one of `items`; otherwise `fallback`. */
export function monthFromQuery(value: unknown, fallback: string): string {
  return typeof value === 'string' && /^\d{4}-\d{2}$/.test(value) ? value : fallback
}

/** "26 Sep 2026 09.45" in WIB. */
export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return new Intl.DateTimeFormat('id-ID', { timeZone: ZONE, day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' }).format(d)
}

/** "Diperbarui 09:45" today, "Diperbarui 25 Sep 2026 09:45" otherwise; the Jira copy's age (FR-050). */
export function refreshedLine(iso: string | null | undefined): string {
  if (!iso) return 'Belum ada data Jira'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return 'Belum ada data Jira'
  const time = new Intl.DateTimeFormat('en-GB', { timeZone: ZONE, hour: '2-digit', minute: '2-digit' }).format(d)
  const day = (x: Date) => new Intl.DateTimeFormat('en-CA', { timeZone: ZONE }).format(x)
  if (day(d) === day(new Date())) return `Diperbarui ${time}`
  const date = new Intl.DateTimeFormat('id-ID', { timeZone: ZONE, day: 'numeric', month: 'short', year: 'numeric' }).format(d)
  return `Diperbarui ${date} ${time}`
}

/** Hours held or waiting: "18,5 jam" under two days, "4 hari" from then on. */
export function formatHours(hours: number | null | undefined): string {
  if (hours === null || hours === undefined) return '—'
  const n = (v: number) => new Intl.NumberFormat('id-ID', { maximumFractionDigits: 1 }).format(v)
  return hours < 48 ? `${n(hours)} jam` : `${n(hours / 24)} hari`
}

/** A count in Bahasa style (1.234), or "—" when unknown. */
export function formatCount(v: number | null | undefined, digits = 0): string {
  if (v === null || v === undefined) return '—'
  return new Intl.NumberFormat('id-ID', { maximumFractionDigits: digits }).format(v)
}

/** A fraction as a percentage: 0.667 → "66,7%". */
export function formatPercent(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined) return '—'
  return `${new Intl.NumberFormat('id-ID', { maximumFractionDigits: digits }).format(v * 100)}%`
}

/** Points with their sign, as the Incentive Framework writes them: −30, +3, 0. */
export function formatPoints(v: number | null | undefined): string {
  if (v === null || v === undefined) return '—'
  if (v > 0) return `+${v}`
  if (v < 0) return `−${Math.abs(v)}`
  return '0'
}

/** The five Stations of the Incentive Framework (CONTEXT.md). */
export const STATIONS: Record<string, { label: string, role: string }> = {
  A: { label: 'Planning', role: 'Content Planner' },
  B: { label: 'Footage', role: 'Field Associate' },
  C: { label: 'Editing', role: 'Content Editor' },
  D: { label: 'Quality control', role: 'Quality Assurance' },
  E: { label: 'Client & publication', role: 'Field Associate' }
}

/** "C · Editing"; "tanpa kategori" for a return with no origin (G-35). */
export function stationName(letter: string | null | undefined): string {
  if (!letter || letter === 'tanpa_kategori') return 'tanpa kategori'
  const s = STATIONS[letter]
  return s ? `${letter} · ${s.label}` : letter
}

export const JIRA_BROWSE = 'https://noktah.atlassian.net/browse/'

export const PLATFORM_ICONS: Record<string, string> = { instagram: 'i-simple-icons-instagram', tiktok: 'i-simple-icons-tiktok' }
