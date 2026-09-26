/**
 * Sample data for Otomasi and Laporan (spec 009), for the UI sweep and local development.
 * `full` has long names and many rows; `empty` has nothing yet (and Prefect unreachable on
 * the Harvest card, so that warning is swept too). See ./index.ts for how states are picked.
 */
const idOf = (i: number) => `00000000-0000-4000-8000-${String(i).padStart(12, '0')}`

export const PLAN_ID = idOf(300)
export const ACCOUNT_ID = idOf(700)

const CLIENTS = [
  'Klinik Mata Sampang', 'Balakosa Rewind and Play', 'Breko', 'Ecky Dental Center', 'Gudang Karung Jumbo Sidoarjo',
  'Klinik Mata Bireuen', 'Klinik Mata Boyolali', 'Klinik Mata Jogja', 'Klinik Mata SMEC Bitung', 'Klinik Pratama SWA',
  'Klinik Spesialis Langsa', 'Klinik Utama Gresik', 'Klinik Utama Sumenep', 'LASIK Asyik by SMEC Tebet', 'MCafe',
  'Nirwana Coffee Shop Sumenep', 'Pelita Delapan', 'RS Mata SMEC Medan', 'RS Mata SMEC Pekanbaru', 'The StarFit',
  'Rumah Sakit Mata Spesialis Nasional Eye Center Cabang Jakarta Utara Pluit'
]
const client = (i: number) => ({ id: idOf(i === 0 ? 7 : 20 + i), name: CLIENTS[i % CLIENTS.length]! })
const monthOf = (q: unknown, fallback: string) => (typeof q === 'string' && /^\d{4}-\d{2}$/.test(q) ? q : fallback)
const at = (day: number, hour = 9) => `2026-09-${String(day).padStart(2, '0')}T${String(hour).padStart(2, '0')}:15:00Z`

// ── Otomasi ────────────────────────────────────────────────────────────────────────

export function automations(empty: boolean) {
  const run = (i: number, deployment: string, state = 'COMPLETED') => ({
    id: idOf(1000 + i), name: `run-${i}`, deployment, state, state_name: state, started_at: at(1 + (i % 25), i % 23), ended_at: at(1 + (i % 25), (i % 23) + 0)
  })
  const jiraHistory = Array.from({ length: 60 }, (_, i) => run(i, i % 7 === 0 ? 'hub-jira-create' : i % 2 ? 'hub-plan-watch' : 'hub-jira-sync',
    i === 3 ? 'FAILED' : i === 0 ? 'RUNNING' : 'COMPLETED'))
  const harvestHistory = Array.from({ length: 3 }, (_, i) => run(100 + i, 'harvest-registry', i === 1 ? 'CRASHED' : 'COMPLETED'))
  return {
    automations: [
      {
        key: 'jira', label: 'Jira', deployments: ['hub-plan-watch', 'hub-jira-create', 'hub-jira-sync'], unavailable: false,
        last_run: empty ? null : jiraHistory[0], next_run_at: empty ? null : '2026-09-26T09:30:00Z',
        failures: empty ? 0 : 1, history: empty ? [] : jiraHistory
      },
      empty
        ? { key: 'harvest', label: 'Harvest', deployments: ['harvest-registry'], unavailable: true, last_run: null, next_run_at: null, history: [] }
        : {
            key: 'harvest', label: 'Harvest', deployments: ['harvest-registry'], unavailable: false,
            last_run: harvestHistory[0], next_run_at: '2026-10-01T10:30:00Z', failures: 1, history: harvestHistory
          }
    ]
  }
}

const STATUSES = ['found', 'greenlit', 'changed_since_greenlight', 'partly_created', 'issues_created', 'changed_after_issues',
  'missing', 'ambiguous', 'unreadable', 'not_scanned'] as const

export function planList(empty: boolean, query?: Record<string, unknown>) {
  const month = monthOf(query?.month, '2026-10')
  if (empty) return { month, scanned_at: null, plans: [] }
  const label = 'Oktober'
  return {
    month, scanned_at: '2026-09-26T02:30:00Z',
    plans: CLIENTS.map((_, i) => {
      const status = i === 0 ? 'found' : STATUSES[i % STATUSES.length]!
      const state = ['missing', 'ambiguous', 'unreadable', 'not_scanned'].includes(status) ? status : 'found'
      const c = client(i)
      return {
        id: status === 'not_scanned' ? null : idOf(300 + i), client: c, state,
        file_name: state === 'found' ? `Content Plan - ${c.name} - ${label}` : null,
        file_url: state === 'found' ? 'https://docs.google.com/spreadsheets/d/example/edit' : null,
        problem: state === 'missing'
          ? `Tidak ada file "Content Plan - ${c.name} - ${label}" di folder Content Plan.`
          : state === 'ambiguous' ? 'Ada dua file dengan nama yang sama.' : state === 'unreadable' ? 'Kolom Tanggal, Bentuk atau Topik tidak ditemukan.' : state === 'not_scanned' ? 'Belum ada folder Content Plan di Registry.' : null,
        rows: state === 'found' ? 12 : 0,
        issues: status === 'issues_created' || status === 'changed_after_issues' ? 12 : status === 'partly_created' ? 10 : 0,
        status,
        greenlit: ['greenlit', 'partly_created', 'issues_created', 'changed_after_issues'].includes(status) ? { by: 'Defila Priana Falarima', at: '2026-09-25T03:00:00Z' } : null,
        open_flags: status === 'changed_after_issues' ? 3 : 0,
        scanned_at: '2026-09-26T02:30:00Z'
      }
    })
  }
}

const TOPICS = [
  'Promo LASIK Oktober: harga per mata dan syarat pemeriksaan pra-LASIK',
  'Kenali tanda katarak pada orang tua sebelum terlambat',
  'Behind the scene ruang operasi',
  'Testimoni pasien LASIK pelajar: lepas kacamata sebelum kuliah',
  'Q&A dokter: mata minus bertambah karena gawai?',
  'Jadwal praktik dr. Ayu Pramesti, Sp.M(K) bulan ini',
  'Tips menjaga mata anak saat belajar daring',
  'Hari Penglihatan Sedunia: pemeriksaan mata gratis untuk lansia di Sampang dan sekitarnya',
  'Mitos dan fakta lensa kontak',
  'Tur fasilitas klinik',
  'Kacamata atau LASIK? Bandingkan biaya lima tahun',
  'Reminder kontrol pasca operasi katarak'
]
const BENTUK = ['Post', 'Story', 'Short Video']

export function planDetail(empty: boolean, id: string, greenlit = false) {
  const c = client(0)
  if (empty) {
    return {
      id, client: c, month: '2026-10', state: 'missing', status: 'missing',
      file_name: null, file_url: null, problem: 'Tidak ada file "Content Plan - Klinik Mata Sampang - Oktober" di folder Content Plan.',
      fingerprint: 'fp-empty', scanned_at: '2026-09-26T02:30:00Z',
      quota: { 'Post': { planned: 0, quota: 4 }, 'Story': { planned: 0, quota: 4 }, 'Short Video': { planned: 0, quota: null } },
      blocking: [{ code: 'plan_not_found', message: 'Content Plan bulan ini belum ada.' }], warnings: [],
      team: { field_associate: null, content_editor: null },
      rows: [], rows_without_issue: 0, flags: [], greenlights: [], can_greenlight: false, can_create: false
    }
  }
  const rows = TOPICS.map((topik, i) => ({
    row_number: i + 2, tanggal: `${String(2 + i * 2).padStart(2, '0')}/10/2026`, bentuk: BENTUK[i % 3]!,
    topik, issue_key: i < 8 ? `ESKL-${11300 + i}` : null,
    flags: i === 4 ? ['changed_after_issue'] : i === 6 ? ['key_duplicated'] : []
  }))
  return {
    id, client: c, month: '2026-10', state: 'found', status: greenlit ? 'greenlit' : 'changed_after_issues',
    file_name: 'Content Plan - Klinik Mata Sampang - Oktober', file_url: 'https://docs.google.com/spreadsheets/d/example/edit',
    problem: null, fingerprint: 'fp-2026-10-kms', scanned_at: '2026-09-26T02:30:00Z',
    quota: { 'Post': { planned: 4, quota: 4 }, 'Story': { planned: 4, quota: 5 }, 'Short Video': { planned: 4, quota: 3 } },
    blocking: greenlit ? [] : [{ code: 'no_content_editor', message: 'Tim klien belum punya Content Editor.' }, { code: 'row_missing_fields', message: 'Baris 14 belum punya Topik.', row_number: 14 }],
    warnings: [
      { code: 'quota_mismatch', message: 'Story: 4 di Content Plan, kuota 5.', row_number: null },
      { code: 'date_outside_month', message: 'Baris 13: tanggal 02/11/2026 di luar bulan ini.', row_number: 13 }
    ],
    team: { field_associate: 'Nadya Safira Alia Adinda', content_editor: null },
    rows, rows_without_issue: 4,
    flags: [
      {
        id: idOf(320), kind: 'changed_after_issue', issue_key: 'ESKL-11304', row_number: 6,
        changes: [
          { column: 'Tanggal', old: '18/10/2026', new: '22/10/2026' },
          { column: 'Visualisasi Konten', old: 'Slide 1: dokter menjelaskan; slide 2: grafik minus', new: 'Slide 1: pertanyaan pasien; slide 2: dokter menjawab di ruang periksa dengan alat autorefraktometer' }
        ],
        detected_at: '2026-09-26T01:45:00Z', comment_state: 'posted'
      },
      { id: idOf(321), kind: 'deleted_from_plan', issue_key: 'ESKL-11299', row_number: 15, changes: [], detected_at: '2026-09-26T01:45:00Z', comment_state: 'not_needed' },
      { id: idOf(322), kind: 'key_duplicated', issue_key: 'ESKL-11306', row_number: 8, changes: [], detected_at: '2026-09-25T10:00:00Z', comment_state: 'failed' }
    ],
    greenlights: [{ by: 'Defila Priana Falarima', at: '2026-09-25T03:00:00Z' }, { by: 'Ardella Bernica', at: '2026-09-20T08:10:00Z' }],
    can_greenlight: false, can_create: greenlit
  }
}

const batch = (i: number, status: string) => ({
  id: idOf(340 + i), status, total: 60, created: status === 'running' ? 21 : 58, failed: status === 'running' ? 0 : 2,
  requested_by: 'Defila Priana Falarima', requested_at: at(25 - i, 3), finished_at: status === 'running' ? null : at(25 - i, 4), error: null
})

export function batches(empty: boolean) {
  return { batches: empty ? [] : [batch(0, 'done'), batch(1, 'done'), batch(2, 'failed')] }
}

export function batchDetail(id: string) {
  return {
    ...batch(0, 'done'), id,
    rows: [
      { plan_id: PLAN_ID, client: 'Klinik Mata Sampang', row_number: 5, outcome: 'failed', issue_key: null, reason: 'summary: The summary is invalid because it contains newline characters.' },
      { plan_id: idOf(316), client: 'Rumah Sakit Mata Spesialis Nasional Eye Center Cabang Jakarta Utara Pluit', row_number: 11, outcome: 'failed', issue_key: null, reason: 'customfield_10040: Tanggal publikasi bukan tanggal yang valid (31/09/2026).' },
      { plan_id: PLAN_ID, client: 'Klinik Mata Sampang', row_number: 2, outcome: 'created', issue_key: 'ESKL-11300', reason: null }
    ]
  }
}

const OUTCOMES = ['collected', 'nothing_new', 'blocked', 'not_found', 'failed', null] as const

export function harvestAccounts(empty: boolean) {
  if (empty) return { accounts: [] }
  const handles = ['klinikmatasampang', 'kmneyecare', 'rsmatanasionaleyecentersurabayaofficial', 'lasikasyik', 'eckydentalcenter', 'balakosa.rewindandplay', 'nirwanacoffee.sumenep', 'the.starfit.official']
  return {
    accounts: Array.from({ length: 28 }, (_, i) => {
      const outcome = OUTCOMES[i % OUTCOMES.length]!
      return {
        id: i === 0 ? idOf(700) : idOf(710 + i), platform: i % 3 === 1 ? 'tiktok' : 'instagram', handle: handles[i % handles.length]!,
        url: i % 3 === 1 ? `https://www.tiktok.com/@${handles[i % handles.length]}` : `https://www.instagram.com/${handles[i % handles.length]}/`, client: client(Math.floor(i / 2)), role: i % 2 ? 'competitor' : 'own',
        last_harvested_at: outcome ? at(1 + (i % 20), 11) : null, last_outcome: outcome,
        last_reason: outcome === 'blocked' ? 'Instagram meminta menunggu beberapa menit (429).' : null,
        posts_collected: outcome ? (outcome === 'collected' ? 12 + i : 0) : null,
        status: outcome === null ? 'never' : outcome === 'blocked' ? 'blocked' : outcome === 'not_found' ? 'not_found' : outcome === 'failed' ? 'failed' : 'ok',
        first_harvest: outcome === null
      }
    })
  }
}

const CAPTIONS = [
  'Mata buram saat membaca? Bisa jadi tanda presbiopi. Yuk periksa ke Klinik Mata Sampang, dokter spesialis kami siap membantu Anda setiap Senin sampai Sabtu. #KlinikMataSampang #SehatMataBersama',
  'Promo LASIK pelajar Rp 550.000 per mata untuk pemeriksaan pra-LASIK, khusus bulan ini!',
  'Selamat Hari Raya Idul Fitri 1447 H. Mohon maaf lahir dan batin dari seluruh keluarga besar klinik.',
  '',
  'Repost dari @dr.ayupramesti: tiga kebiasaan kecil yang menjaga mata tetap sehat di depan layar'
]

export function accountPosts(empty: boolean, id: string, query?: Record<string, unknown>) {
  const account = harvestAccounts(false).accounts.find(a => a.id === id) ?? harvestAccounts(false).accounts[0]!
  return {
    account: { ...account, clients: [{ client: client(0), role: 'own' }, { client: client(20), role: 'competitor' }] },
    month: monthOf(query?.month, '2026-08'),
    posts: empty
      ? []
      : Array.from({ length: 24 }, (_, i) => ({
          platform: 'instagram', content_id: `3712${String(i).padStart(4, '0')}_1`, url: 'https://www.instagram.com/p/DExample/',
          published_at: `2026-08-${String(28 - i).padStart(2, '0')}T05:00:00Z`, content_type: ['video', 'image', 'carousel'][i % 3],
          caption: CAPTIONS[i % CAPTIONS.length], views: i % 3 === 0 ? 1200 + i * 431 : null, likes: 30 + i * 7,
          comments: i % 3 === 0 ? 4 + i : null, shares: i % 3 === 0 ? i : null,
          marks: i === 1 ? ['iklan'] : i === 2 ? ['tidak_relevan'] : i === 4 ? ['iklan', 'bukan_konten_akun_ini'] : []
        }))
  }
}

// ── Laporan ────────────────────────────────────────────────────────────────────────

const REFRESHED = '2026-09-26T02:45:00Z'

export function delivery(empty: boolean, query?: Record<string, unknown>) {
  const month = monthOf(query?.month, '2026-08')
  if (empty) return { month, refreshed_at: null, clients: [], totals: { planned: 0, created: 0, published: 0, late: 0, published_late: 0, cancelled: 0, on_hold: 0 } }
  const clients = CLIENTS.map((_, i) => {
    const late = i % 4 === 0 ? 2 : i % 5 === 0 ? 1 : 0
    return {
      client: client(i), planned: i === 3 ? null : 12, created: 12 - (i % 3 === 0 ? 1 : 0), published: 9 - late, late, published_late: i % 3, cancelled: i % 6 === 0 ? 1 : 0, on_hold: i % 5 === 0 ? 1 : 0,
      late_items: Array.from({ length: late }, (_, k) => ({
        key: `ESKL-${11147 + i * 3 + k}`, topik: TOPICS[(i + k) % TOPICS.length], publication_date: `${month}-0${5 + k}`,
        status: k ? 'In Review by Client' : 'Footages in Progress', station: k ? 'E' : 'B', station_label: k ? 'Client & publication' : 'Footage', days_late: 21 - k * 4
      }))
    }
  })
  const sum = (k: 'planned' | 'created' | 'published' | 'late' | 'published_late' | 'cancelled' | 'on_hold') => clients.reduce((s, c) => s + (c[k] ?? 0), 0)
  return {
    month, refreshed_at: REFRESHED, clients,
    totals: { planned: sum('planned'), created: sum('created'), published: sum('published'), late: sum('late'), published_late: sum('published_late'), cancelled: sum('cancelled'), on_hold: sum('on_hold') }
  }
}

export function stations(empty: boolean, query?: Record<string, unknown>) {
  const month = monthOf(query?.month, '2026-08')
  if (empty) return { month, refreshed_at: null, stations: [], people: [], teams: [], round_flags: [], returns: [], unknown_statuses: [] }
  const S = [
    ['A', 'Planning', 'Content Planner'], ['B', 'Footage', 'Field Associate'], ['C', 'Editing', 'Content Editor'],
    ['D', 'Quality control', 'Quality Assurance'], ['E', 'Client & publication', 'Field Associate']
  ]
  return {
    month, refreshed_at: REFRESHED,
    stations: S.map(([station, label, role], i) => ({
      station, label, role, in: 40 + i * 3, out: 36 + i,
      returns: { total: i === 0 ? 0 : 5 - (i % 2), by_origin: i === 0 ? {} : { C: 3, B: 1, tanpa_kategori: i % 2 ? 0 : 1 } },
      held_hours: { median: 18.5 + i * 6, longest: 96 + i * 30 },
      waiting: i === 0 ? [] : Array.from({ length: i + 3 }, (_, k) => ({ key: `ESKL-${11200 + i * 10 + k}`, client: CLIENTS[(i + k) % CLIENTS.length]!, since: at(20), hours: 30 + k * 20 }))
    })),
    people: [
      ...['Juliana Devina Santosa', 'Nadya Safira Alia Adinda', 'Putri Indah Lestari', 'Putri Indah Lestari', 'Nadya Safira Alia Adinda'].map((name, i) => ({
        person: { id: idOf(902 + i), name }, station: S[i]![0], in: 20 - i, out: 18 - i, returns_against: i, held_hours: { median: 12 + i, longest: 50 + i * 10 }, waiting: i
      })),
      { person: { name: 'tidak terdaftar', account_id: '712020:0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0' }, station: 'C', in: 3, out: 3, returns_against: 0, held_hours: { median: 8, longest: 20 }, waiting: 0 }
    ],
    teams: CLIENTS.slice(0, 22).map((_, i) => ({
      client: client(i),
      first_pass: { rate: i % 5 === 4 ? null : 0.4 + (i % 4) * 0.1, counted: 9, target: 0.6 },
      qa_rounds: { average: i % 7 === 6 ? null : 1 + (i % 4) * 0.2, counted: 10, target: 1.5 },
      both_met: i % 4 >= 2 && i % 5 !== 4, returns: i % 5
    })),
    round_flags: [{ key: 'ESKL-11188', client: 'Rumah Sakit Mata Spesialis Nasional Eye Center Cabang Jakarta Utara Pluit', stations: ['C', 'D'], rounds: 4 }],
    returns: Array.from({ length: 24 }, (_, i) => ({
      key: `ESKL-${11150 + i}`, client: CLIENTS[i % CLIENTS.length], at: at(1 + i, 8), sent_back_by: 'D', bucket: null, from_status: 'Designs in Review', to_status: i % 2 ? 'Designs in Progress' : 'Footages in Progress',
      origin: i % 3 === 2 ? null : i % 2 ? 'C' : 'B', defect: i % 3 === 2 ? null : i % 2 ? 'C2 Teks atau caption salah' : 'B1 Footage kurang',
      reason: i % 3 === 2 ? null : 'Logo klien terpotong di detik ke-3 dan warna teks tidak sesuai Guideline visual'
    })),
    unknown_statuses: ['Waiting for Asset']
  }
}

const side = (scale: number) => ({
  accounts: [{ platform: 'instagram', handle: 'klinikmatasampang', followers: 1342 }, { platform: 'tiktok', handle: 'klinikmatasampang', followers: null }],
  posts: Math.round(12 * scale), posts_by_type: { video: 6 * scale, image: 3 * scale, carousel: 3 * scale },
  views: { total: 12450 * scale, average: 2075 * scale, n: 6 }, likes: { total: 812 * scale, average: 73.8 * scale, n: 11 },
  comments: { total: 64 * scale, average: 10.7 * scale, n: 6 }, shares: { total: 21 * scale, average: 7 * scale, n: 3 },
  engagement_rate_views: { value: 0.052 * scale, n: 6 },
  engagement_rate_followers: { value: null, unavailable: 'Jumlah pengikut Instagram tidak tersedia bulan ini' }
})

export function performance(empty: boolean, query?: Record<string, unknown>) {
  const month = monthOf(query?.month, '2026-08')
  if (!query?.client_id) {
    return { month, refreshed_at: REFRESHED, clients: empty ? [] : CLIENTS.map((_, i) => ({ client: client(i), accounts: 2, posts: 12, views_total: 12450, engagement_rate_views: 0.052 })) }
  }
  const id = String(query.client_id)
  const c = CLIENTS.map((_, i) => client(i)).find(x => x.id === id) ?? client(0)
  const posts = accountPosts(false, ACCOUNT_ID, query).posts
  return {
    client: c, month, refreshed_at: REFRESHED,
    own: {
      ...side(1), posts_counted: 9, top_posts: posts.filter(p => !p.marks.length).slice(0, 5), marked: posts.filter(p => p.marks.length),
      change: { posts: 0.2, views_total: 0.12, likes_total: -0.08, comments_total: null, shares_total: 0, engagement_rate_views: 0.03 }
    },
    competitors: {
      accounts: [{ platform: 'instagram', handle: 'kmneyecare' }, { platform: 'instagram', handle: 'rsmatanasionaleyecentersurabayaofficial' }, { platform: 'tiktok', handle: 'lasikasyik' }], n_accounts: 3,
      average: { ...side(1.4), engagement_rate_followers: { value: 0.031, n: 14 } }
    }
  }
}

const LEVELS = { teguran_lisan: 'Teguran lisan', sp1: 'SP1', sp2: 'SP2', sp3: 'SP3', peringatan_terakhir: 'Peringatan Pertama dan Terakhir', phk_flag: 'Proses PHK' }

const sanction = (n: number, level: keyof typeof LEVELS, state: string, extra: Record<string, unknown> = {}) => ({
  id: idOf(360 + n), level, level_label: LEVELS[level], with_pip: false, source: 'ladder', state, period: '2026-08',
  category_code: 'C2', event_keys: [], points: -30, hold_until: null, hold_reason: null, issued_on: null, valid_until: null,
  letter_number: null, letter_state: 'not_needed', letter_url: null, note: null, ...extra
})

const event = (key: string, type: string | null, category: string | null, points: number | null, state: string, extra: Record<string, unknown> = {}) => ({
  key, summary: null, type, category, points, state, reason: null, missing: [], formula: null, direct_item: null,
  status: 'Judged', created_at: at(10), judged_at: at(12), ...extra
})

export function incentive(empty: boolean, query?: Record<string, unknown>) {
  const month = monthOf(query?.month, '2026-08')
  if (empty) return { month, refreshed_at: null, levels: LEVELS, people: [], incomplete: [], unmatched: [], direct_sanctions_manual: [] }
  const person = (i: number, name: string) => ({ id: idOf(902 + i), name })
  const withNewest = <T extends { sanctions: unknown[] }>(p: T) => ({ ...p, sanction: p.sanctions[0] ?? null })
  return {
    month, refreshed_at: REFRESHED, levels: LEVELS,
    people: [
      withNewest({
        person: person(1, 'Nadya Safira Alia Adinda'), violation_points: -30, excellence_points: 3,
        sanctions: [sanction(0, 'sp2', 'computed', { hold_until: '2099-10-05T10:00:00Z' })],
        team_reward: { qualifies: true, teams_met: 4, teams: 6 },
        events: [
          event('ESKL-11200', 'violation', 'C2 Teks atau caption salah', -30, 'counted', { summary: 'Caption promo LASIK menyebut harga lama', reason: 'Klien × ditemukan orang lain', formula: '−(10 × 3 × 1)' }),
          event('ESKL-11201', 'excellence', 'X4 Ide konten dipakai klien', 3, 'counted'),
          event('ESKL-11202', 'violation', null, null, 'belum_lengkap', { missing: ['Violation Judgment', 'Event Observer'] }),
          event('ESKL-11090', 'violation', 'B1 Footage kurang', -10, 'reference', { reason: 'Dinilai sebelum 27 September 2026' })
        ]
      }),
      withNewest({
        person: person(3, 'Putri Indah Lestari'), violation_points: -60, excellence_points: 0,
        sanctions: [
          sanction(1, 'sp1', 'held', { with_pip: true, hold_until: '2099-10-05T10:00:00Z', hold_reason: 'Menunggu klarifikasi dari klien soal revisi caption yang diminta lewat telepon' }),
          sanction(4, 'teguran_lisan', 'issued', { source: 'recorded', period: null, issued_on: '2026-08-04', valid_until: '2026-11-02', note: 'Teguran lisan dari Brand Manager' })
        ],
        team_reward: { qualifies: false, teams_met: 2, teams: 6 },
        events: [
          event('ESKL-11210', 'violation', 'C1 Salah aset', -30, 'counted'),
          event('ESKL-11211', 'violation', 'C3 Terlambat', -30, 'on_hold', { reason: 'Sedang banding' })
        ]
      }),
      withNewest({
        person: person(0, 'Juliana Devina Santosa'), violation_points: -10, excellence_points: 1,
        sanctions: [sanction(2, 'teguran_lisan', 'issued', { issued_on: '2026-09-03', valid_until: '2026-12-02', points: -10 })],
        team_reward: { qualifies: true, teams_met: 5, teams: 6 },
        events: [event('ESKL-11220', 'violation', 'A2 Brief kurang lengkap', -10, 'counted', { summary: 'Self-report: brief shooting tanpa jadwal dokter' })]
      }),
      withNewest({
        person: person(4, 'Anindya Kusumawardhani Prameswari Wicaksono'), violation_points: 0, excellence_points: 0,
        sanctions: [
          sanction(3, 'peringatan_terakhir', 'issued', { source: 'direct', period: null, issued_on: '2026-09-04', valid_until: '2026-12-03', letter_number: '004/SP/ESK/IX/2026', letter_state: 'written', letter_url: 'https://docs.google.com/document/d/example/edit' }),
          sanction(5, 'phk_flag', 'flagged', { period: null })
        ],
        team_reward: null,
        events: [event('ESKL-11230', 'violation', 'H2 Posting ke akun Klien tanpa ACC Klien', 0, 'adaptation', { reason: 'Masa adaptasi 30 hari', direct_item: '4.3.2-1' })]
      }),
      withNewest({
        person: person(5, 'Rizky Maulana'), violation_points: 0, excellence_points: 4, sanctions: [],
        team_reward: { qualifies: true, teams_met: 3, teams: 3 },
        events: [event('ESKL-11240', 'excellence', 'X1 Bantu tim lain', 1, 'counted'), event('ESKL-11241', null, 'X5 Konten viral', 3, 'not_judged', { reason: 'Belum dinilai' })]
      })
    ],
    incomplete: [
      { key: 'ESKL-11202', summary: 'Salah tag akun dokter', missing: ['Violation Judgment', 'Event Observer'], person: 'Nadya Safira Alia Adinda' },
      { key: 'ESKL-11250', summary: null, missing: ['Person'], person: null }
    ],
    unmatched: [{ key: 'ESKL-11270', account_id: '712020:0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0' }],
    direct_sanctions_manual: [{ key: 'ESKL-11260', person: 'Putri Indah Lestari', summary: 'Posting story tanpa ACC klien' }]
  }
}

/** Otomasi / Laporan responses for GET, or undefined when the path isn't one of them. */
export function otomasiGet(p: string, empty: boolean, query?: Record<string, unknown>): unknown {
  if (p === '/v1/automations') return automations(empty)
  if (p === '/v1/content-plans') return planList(empty, query)
  if (p === '/v1/jira-batches') return batches(empty)
  if (p === '/v1/harvest/accounts') return harvestAccounts(empty)
  if (p === '/v1/reports/delivery') return delivery(empty, query)
  if (p === '/v1/reports/stations') return stations(empty, query)
  if (p === '/v1/reports/performance') return performance(empty, query)
  if (p === '/v1/reports/incentive') return incentive(empty, query)
  let m = p.match(/^\/v1\/content-plans\/([^/]+)$/)
  if (m) return planDetail(empty, m[1]!)
  m = p.match(/^\/v1\/jira-batches\/([^/]+)$/)
  if (m) return batchDetail(m[1]!)
  m = p.match(/^\/v1\/harvest\/accounts\/([^/]+)\/posts$/)
  if (m) return accountPosts(empty, m[1]!, query)
  return undefined
}

/** Otomasi / Laporan responses for writes, or undefined when the path isn't one of them. */
export function otomasiWrite(p: string, body: unknown): unknown {
  let m = p.match(/^\/v1\/content-plans\/([^/]+)\/greenlight$/)
  if (m) return planDetail(false, m[1]!, true)
  if (/^\/v1\/content-plans\/flags\/[^/]+\/resolve$/.test(p)) return { ...planDetail(false, PLAN_ID), flags: [] }
  if (/^\/v1\/content-plans\/[^/]+\/rescan$/.test(p) || p === '/v1/content-plans/scan-month') return { flow_run_id: idOf(1500) }
  if (p === '/v1/jira-batches') return { batch: { id: idOf(340), status: 'queued', total: 60 } }
  if (/^\/v1\/harvest\/accounts\/[^/]+\/run$/.test(p)) return { flow_run_id: idOf(1501) }
  m = p.match(/^\/v1\/harvest\/posts\/[^/]+\/[^/]+\/marks$/)
  if (m) return { marks: ((body ?? {}) as { marks?: string[] }).marks ?? [] }
  if (p === '/v1/sanctions') return { id: idOf(370), source: 'recorded', state: 'issued', ...(body as Record<string, unknown>) }
  m = p.match(/^\/v1\/sanctions\/([^/]+)\/(hold|release)$/)
  if (m) return { id: m[1], state: m[2] === 'hold' ? 'held' : 'computed' }
  return undefined
}
