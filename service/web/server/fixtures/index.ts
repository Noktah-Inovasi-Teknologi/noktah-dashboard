import type { H3Event } from 'h3'
import definition from './card-definition.json'

/**
 * Sample data for the UI sweep and local development: the Hub's own login is
 * Cloudflare Access, so a local browser can't sign in and the API won't answer.
 *
 * Active ONLY when both hold:
 *   - this is the dev server (`import.meta.dev`; the whole branch is removed
 *     from production builds), and
 *   - NUXT_HUB_FIXTURES=1 (the sweep's Playwright config sets it).
 *
 * The state is picked per browser with the `hub-fixture` cookie:
 *   full (default; long names, many rows, one pending Guideline change) · empty ·
 *   down (office PC off) · no_access (signed in, no role) · failed (AI failed) ·
 *   cap (monthly AI cap reached)
 *
 * card-definition.json is config/hub/card_v1.yaml exported as JSON.
 */
export type FixtureState = 'full' | 'empty' | 'down' | 'no_access' | 'failed' | 'cap'
const STATES: FixtureState[] = ['full', 'empty', 'down', 'no_access', 'failed', 'cap']

export function fixtureState(event: H3Event): FixtureState | null {
  if (!import.meta.dev || process.env.NUXT_HUB_FIXTURES !== '1') return null
  const wanted = getCookie(event, 'hub-fixture') as FixtureState | undefined
  return wanted && STATES.includes(wanted) ? wanted : 'full'
}

export const FIXTURE_EMAIL = 'defila@noktah.co'

const CLIENT_NAMES = [
  'Balakosa Rewind and Play', 'Breko', 'Ecky Dental Center', 'Gudang Karung Jumbo Sidoarjo',
  'Klinik Mata Bireuen', 'Klinik Mata Boyolali', 'Klinik Mata Jogja', 'Klinik Mata Sampang',
  'Klinik Mata SMEC Bitung', 'Klinik Pratama SWA', 'Klinik Spesialis Langsa', 'Klinik Utama Gasa',
  'Klinik Utama Gresik', 'Klinik Utama Sumenep', 'LASIK Asyik by SMEC Tebet', 'MCafe',
  'Nirwana Coffee Shop Sumenep', 'Nirwana Coffee Space Pamekasan', 'Pelita Delapan',
  'RS Mata SMEC Medan', 'RS Mata SMEC Pekanbaru', 'The StarFit',
  'Rumah Sakit Mata Spesialis Nasional Eye Center Cabang Jakarta Utara Pluit'
]
const idOf = (i: number) => `00000000-0000-4000-8000-${String(i).padStart(12, '0')}`
const CLIENT_ID = idOf(7) // Klinik Mata Sampang

const ME = {
  person: { id: idOf(900), display_name: 'Defila Priana Falarima', email: FIXTURE_EMAIL },
  roles: [{ role: 'brand_manager', noktah_brand: 'eskala' }],
  brands: ['eskala'],
  can: { edit_registry: true, edit_profil: true, edit_guideline: true, approve: true, manage_people: true, appoint_bm: false, run_intake: true }
}

const PEOPLE = [
  { id: idOf(900), display_name: 'Defila Priana Falarima', status: 'active', jira_account_id: '712020:aaa', slack_user_id: 'U0C45JQ8CH0', emails: ['defila@noktah.co'], roles: [{ id: idOf(950), role: 'brand_manager', noktah_brand: 'eskala' }] },
  { id: idOf(901), display_name: 'Ardella Bernica', status: 'active', jira_account_id: '712020:bbb', slack_user_id: null, emails: ['ardellabernica8@gmail.com'], roles: [{ id: idOf(951), role: 'project_manager', noktah_brand: 'eskala' }] },
  { id: idOf(902), display_name: 'Juliana Devina Santosa', status: 'active', jira_account_id: '712020:ccc', slack_user_id: null, emails: ['juliana.devn@gmail.com'], roles: [{ id: idOf(952), role: 'content_planner', noktah_brand: 'eskala' }] },
  { id: idOf(903), display_name: 'Nadya Safira Alia Adinda', status: 'active', jira_account_id: '712020:ddd', slack_user_id: null, emails: [], roles: [{ id: idOf(953), role: 'field_associate', noktah_brand: 'eskala' }] },
  { id: idOf(904), display_name: 'Anaknya Mama Gufron', status: 'left', jira_account_id: null, slack_user_id: null, emails: ['romuty16@gmail.com'], roles: [] }
]

/** One Person with the detail fields /v1/people/{id} returns. */
function personDetail(id: string) {
  const base = PEOPLE.find(x => x.id === id) ?? PEOPLE[1]!
  return {
    ...base, version: 2, left_at: base.status === 'left' ? '2026-08-31T10:00:00Z' : null,
    team: base.status === 'left'
      ? []
      : [
          { client_id: CLIENT_ID, client: 'Klinik Mata Sampang', team_role: 'account_executive' },
          { client_id: idOf(3), client: 'LASIK Asyik by SMEC Tebet', team_role: 'account_executive' }
        ],
    history: [
      { id: 41, entity: 'person_role', field: 'project_manager', old_value: null, new_value: { role: 'project_manager', noktah_brand: 'eskala' }, person: 'Defila Priana Falarima', at: '2026-09-20T02:00:00Z' },
      { id: 40, entity: 'person_email', field: 'email', old_value: null, new_value: base.emails[0] ?? 'nama@contoh.com', person: 'Defila Priana Falarima', at: '2026-09-20T01:59:00Z' },
      { id: 39, entity: 'person', field: 'created', old_value: null, new_value: { name: base.display_name }, person: 'Sistem', at: '2026-09-19T08:00:00Z' }
    ]
  }
}

function clientsList(status = 'active') {
  return CLIENT_NAMES.map((name, i) => ({
    id: idOf(i), name, status: name === 'Klinik Utama Gasa' ? 'inactive' : 'active', is_internal: false,
    brand: 'eskala', brand_name: 'Eskala', quotas: { post: 4, story: 4, short_video: 4 },
    team_summary: { account_executive: i % 3 ? 'Ardella Bernica' : null, field_associate: 'Nadya Safira Alia Adinda', content_editor: i % 4 ? 'Putri Indah Lestari' : null },
    card_completeness: { profil: `${i % 6}/5`.replace('5/5', '5/5'), guideline: `${i % 11}/10` },
    pending_approvals: i === 7 ? 1 : 0
  })).filter(c => status === 'all' || c.status === status)
}

function clientRecord(id: string) {
  const i = Math.max(0, CLIENT_NAMES.findIndex((_, k) => idOf(k) === id))
  return {
    id, name: CLIENT_NAMES[i], status: 'active', is_internal: false, brand: 'eskala', brand_name: 'Eskala',
    quotas: { post: 4, story: 4, short_video: 4 },
    drive_folder_id: '1AbCdEfGhIjKlMnOpQrStUvWxYz012345', content_plan_folder_id: '1ZyXwVuTsRqPoNmLkJiHgFeDcBa987654',
    jira_component_id: '10042',
    team: {
      account_executive: { person_id: idOf(901), name: 'Ardella Bernica', status: 'active' },
      content_planner: { person_id: idOf(902), name: 'Juliana Devina Santosa', status: 'active' },
      field_associate: { person_id: idOf(903), name: 'Nadya Safira Alia Adinda', status: 'active' },
      content_editor: null, qc: null
    },
    accounts: [
      { account_id: idOf(700), platform: 'instagram', handle: 'klinikmatasampang', relation: 'own', is_active: true },
      { account_id: idOf(701), platform: 'tiktok', handle: 'klinikmatasampang', relation: 'own', is_active: true },
      { account_id: idOf(702), platform: 'instagram', handle: 'kmneyecare', relation: 'competitor', is_active: true },
      { account_id: idOf(703), platform: 'instagram', handle: 'rsmatanasionaleyecentersurabayaofficial', relation: 'competitor', is_active: false }
    ],
    version: 3, card_version: 12
  }
}

const SOURCE = { who: 'Bu Rina (PIC)', where: 'WhatsApp grup', when: '2026-09-20' }
const val = (value: unknown, extra: Record<string, unknown> = {}) => ({
  value, valid_until: null, expired: false, source: SOURCE, set_by: 'Ardella Bernica', set_at: '2026-09-20T03:12:00Z', from_intake: null, ...extra
})

function card(empty: boolean) {
  const profil = empty
    ? {}
    : {
        nama_penulisan: val({ nama_resmi: 'Klinik Mata Sampang', jangan_ditulis: ['Klinik Mata SMEC Sampang', 'KMS Boyolali'], grup: 'SMEC Group' }),
        lokasi_kontak_jam: val({ alamat: 'Jl. Jaksa Agung Suprapto No. 21, Sampang, Jawa Timur', google_maps: 'https://maps.app.goo.gl/example', nomor_publik: '0812-3456-7890', jam_buka: 'Senin–Sabtu 08.00–20.00' }),
        akun_hashtag: val({ akun_tag: ['@klinikmatasampang'], hashtag_wajib: ['#KlinikMataSampang', '#SehatMataBersama'], hashtag_dilarang: ['#KlinikMataBoyolali'] }),
        produk_layanan: val({ daftar: ['LASIK', 'Operasi katarak', 'Pemeriksaan mata anak', 'Kacamata & lensa kontak'], tidak_tersedia: ['ReLEx SMILE'] }),
        harga_promo: val([
          { item: 'LASIK', harga: 'Rp 9.500.000', satuan: 'per mata', syarat: 'Termasuk pemeriksaan pra-LASIK', berlaku_sampai: '2026-12-31' },
          { item: 'Pre-LASIK pelajar', harga: 'Rp 550.000', satuan: 'per mata', syarat: 'Khusus pelajar, bawa kartu pelajar', berlaku_sampai: '2026-10-31' }
        ], { valid_until: '2026-12-31' }),
        orang_yang_tampil: val([{ nama_gelar: 'dr. Ayu Pramesti, Sp.M(K)', peran: 'Dokter spesialis mata', jadwal: 'Selasa & Kamis 16.00–20.00', izin_tampil: 'Ya, tertulis 2026-08-02' }]),
        pic: val({ nama: 'Bu Rina', jabatan: 'Manajer Marketing', nomor: '0812-1111-2222', boleh_approve: ['Bu Rina', 'dr. Ayu'] }),
        target_audiens: val('Keluarga di Madura usia 25–55 yang peduli kesehatan mata, dan pelajar yang ingin lepas kacamata.', { expired: true, valid_until: '2026-06-30' })
      }
  const guideline = empty
    ? {}
    : {
        positioning: val({ target: 'Keluarga Madura', kategori: 'Klinik mata', pembeda: ['Dokter spesialis subspesialis di Sampang', 'Alat LASIK terbaru di Madura'], kesamaan: ['Harga jelas'], alasan_percaya: ['1.200+ tindakan LASIK sejak 2019'], pernyataan: 'Klinik mata keluarga di Sampang dengan dokter subspesialis, tanpa harus ke Surabaya.', tagline: 'Lihat Lebih Jelas, Hidup Lebih Dekat' }),
        suara: val({ serius_jenaka: 2, formal_santai: 3, hormat_berani: 2, antusias_datar: 2, sapaan: 'Anda', campur_bahasa: 'Istilah medis boleh Inggris, sisanya Bahasa', emoji: 'Hemat, maksimal 2 per caption', gaya_judul: 'Pertanyaan langsung', gaya_jualan: 'Edukasi dulu, ajakan di akhir', kata_dipakai: ['nyaman', 'aman'], kata_dihindari: ['murah'], contoh: ['Mata buram saat membaca? Bisa jadi tanda presbiopi.'] }),
        kepribadian_arketipe: val({ sincerity: 5, excitement: 2, competence: 5, sophistication: 3, ruggedness: 1, arketipe_utama: 'caregiver', arketipe_pendukung: 'sage' }),
        batasan: val({ klaim_dilarang_klien: ['tanpa rasa sakit'], topik_sensitif: ['Perbandingan dengan klinik lain'], aturan_pesaing: 'Jangan menyebut nama pesaing.' })
      }
  return {
    card_version: 12, profil, guideline,
    pending: empty
      ? []
      : [{ id: idOf(800), part: 'guideline', field_key: 'visual', value: { logo_file: 'https://drive.google.com/drive/folders/example', warna: ['#0E7C66 hijau utama', '#F5B700 kuning aksen'], font: ['Poppins'] }, set_by: 'Ardella Bernica', set_at: '2026-09-24T09:00:00Z' }],
    completeness: empty
      ? { profil: { filled: 0, required: 5, missing: ['nama_penulisan', 'lokasi_kontak_jam', 'akun_hashtag', 'produk_layanan', 'pic'] }, guideline: { filled: 0, total: 10, missing_required: ['positioning', 'suara', 'visual', 'batasan'] } }
      : { profil: { filled: 5, required: 5, missing: [] }, guideline: { filled: 4, total: 10, missing_required: ['visual'] } },
    requests_open: empty ? 0 : 2,
    summary: empty
      ? null
      : {
          body: {
            siapa: 'Klinik mata keluarga di Sampang (SMEC Group) dengan dokter subspesialis; fokus LASIK dan katarak.',
            promo_berjalan: ['LASIK Rp 9.500.000 per mata (s.d. 31 Des 2026)', 'Pre-LASIK pelajar Rp 550.000 per mata (s.d. 31 Okt 2026)'],
            aturan_kunci: ['Sapaan "Anda"', 'Jangan menyebut nama pesaing', 'Jangan klaim "tanpa rasa sakit"', 'Pakai #KlinikMataSampang, bukan hashtag cabang lain'],
            permintaan_terbuka: ['Konten promo LASIK minggu depan', 'Video testimoni pasien katarak (dengan izin)']
          },
          generated_at: '2026-09-24T01:00:00Z', stale: false, cap_paused: false
        }
  }
}

function requests(empty: boolean) {
  if (empty) return []
  return [
    { id: idOf(600), requested_on: '2026-09-25', text: 'Tolong minggu depan bikin konten promo LASIK ini ya.', requested_by: 'Bu Rina', is_pic: true, channel: 'whatsapp_group', status: 'baru', reject_reason: null, link: null, version: 0 },
    { id: idOf(601), requested_on: '2026-09-18', text: 'Bisa dibuatkan video testimoni pasien katarak? Pasiennya sudah setuju tampil.', requested_by: 'dr. Ayu', is_pic: false, channel: 'meeting', status: 'diproses', reject_reason: null, link: 'https://noktah.atlassian.net/browse/ESKL-11201', version: 2 },
    { id: idOf(602), requested_on: '2026-09-02', text: 'Ganti foto dokter di highlight Instagram.', requested_by: 'Bu Rina', is_pic: true, channel: 'whatsapp_group', status: 'selesai', reject_reason: null, link: null, version: 3 },
    { id: idOf(603), requested_on: '2026-08-28', text: 'Buat konten perbandingan harga dengan klinik sebelah.', requested_by: 'Bu Rina', is_pic: true, channel: 'whatsapp_group', status: 'ditolak', reject_reason: 'Melanggar Batasan: tidak menyebut pesaing.', link: null, version: 1 }
  ]
}

function proposals() {
  return [
    { id: idOf(500), target: 'profil', field_key: 'harga_promo', current_value: [{ item: 'LASIK', harga: 'Rp 9.000.000', satuan: 'per mata', syarat: '', berlaku_sampai: '' }], proposed_value: [{ item: 'LASIK', harga: 'Rp 9.500.000', satuan: 'per mata', syarat: 'Promo pelajar tetap', berlaku_sampai: '' }], excerpt: 'Mulai 1 Oktober harga LASIK jadi 9,5 jt per mata ya, promo pelajar tetap.', speaker: 'Bu Rina (PIC)', spoke_at: '2026-09-25', valid_until: null, flags: [], outcome: 'pending', final_value: null, ticks: {}, decided_by: null, decided_at: null },
    { id: idOf(501), target: 'request', field_key: null, current_value: null, proposed_value: 'Tolong minggu depan bikin konten promo ini.', excerpt: 'Tolong minggu depan bikin konten promo ini.', speaker: 'Bu Rina (PIC)', spoke_at: '2026-09-25', valid_until: null, flags: [], outcome: 'pending', final_value: null, ticks: {}, decided_by: null, decided_at: null },
    { id: idOf(502), target: 'profil', field_key: 'lokasi_kontak_jam', current_value: { jam_buka: 'Senin–Sabtu 08.00–20.00' }, proposed_value: { jam_buka: 'Senin–Minggu 08.00–21.00' }, excerpt: 'mulai bulan depan buka tiap hari sampai jam 9 malam', speaker: 'Mbak Sari (resepsionis)', spoke_at: '2026-09-25', valid_until: null, flags: ['not_pic', 'from_image'], outcome: 'pending', final_value: null, ticks: {}, decided_by: null, decided_at: null },
    { id: idOf(503), target: 'guideline', field_key: 'batasan', current_value: null, proposed_value: { klaim_dilarang_klien: ['tanpa rasa sakit', 'dijamin normal'] }, excerpt: 'jangan pernah tulis dijamin normal ya', speaker: 'Bu Rina (PIC)', spoke_at: '2026-09-25', valid_until: null, flags: ['unverified', 'contradicts'], outcome: 'pending', final_value: null, ticks: {}, decided_by: null, decided_at: null }
  ]
}

function intake(state: FixtureState, id = idOf(400)) {
  return {
    id, client: { id: CLIENT_ID, name: 'Klinik Mata Sampang' }, kind: 'text',
    status: state === 'failed' ? 'failed' : state === 'empty' ? 'nothing_found' : 'ready',
    failure_reason: state === 'failed' ? 'validation_failed' : null,
    submitted_by: 'Ardella Bernica', submitted_at: '2026-09-25T02:10:00Z', source_ref: null, raw_available: true,
    no_card_home: state === 'full' ? ['Laporan jumlah pasien Agustus'] : [],
    proposals: state === 'full' ? proposals() : []
  }
}

function history() {
  return [
    { id: 33, entity: 'client', field: 'status', old_value: 'pending', new_value: 'active', person: 'Defila Priana Falarima', at: '2026-09-25T01:00:00Z' },
    { id: 32, entity: 'client', field: 'drive_folder_id', old_value: null, new_value: '1AbCdEfGhIjKlMnOpQrStUvWxYz012345', person: 'Sistem', at: '2026-09-24T20:00:00Z' },
    { id: 31, entity: 'team', field: 'field_associate', old_value: 'Anaknya Mama Gufron', new_value: 'Nadya Safira Alia Adinda', person: 'Defila Priana Falarima', at: '2026-09-24T08:00:00Z' },
    { id: 30, entity: 'client', field: 'quota_post', old_value: 3, new_value: 4, person: 'Ardella Bernica', at: '2026-09-20T02:00:00Z' },
    { id: 29, entity: 'account_link', field: 'relation', old_value: null, new_value: { account: 'instagram:kmneyecare', relation: 'competitor', is_active: true }, person: 'Defila Priana Falarima', at: '2026-09-10T02:00:00Z' }
  ]
}

function fail(statusCode: number, error: string, message: string): never {
  throw createError({ statusCode, statusMessage: message, data: { error, message } })
}

/** The response `path` would return from hub-api, for the chosen state. */
export function fixtureResponse(path: string, method: string, state: FixtureState, body?: unknown, query?: Record<string, unknown>): unknown {
  if (state === 'down') fail(503, 'unavailable', 'Server kantor tidak bisa dihubungi. Pastikan PC kantor menyala, lalu coba lagi.')
  if (state === 'no_access') fail(403, 'no_access', 'Anda belum punya akses ke Noktah Hub.')
  const empty = state === 'empty'
  const p = path.split('?')[0]!

  if (method !== 'GET') {
    if (state === 'cap' && /\/intakes$/.test(p)) fail(402, 'ai_cap_reached', 'Batas biaya AI bulan ini sudah tercapai. Intake dijeda.')
    if (/\/intakes$/.test(p)) return { ...intake(state), cached: false, dropped: { patient_data: 0, invalid: 0, unchanged: 0 } }
    // Registry writes answer with the updated record (PATCH client, PUT team, POST/PATCH accounts).
    const registry = p.match(/^\/v1\/clients\/([^/]+)(\/team\/[^/]+|\/accounts(\/[^/]+)?)?$/)
    if (registry) {
      const rec = clientRecord(registry[1]!)
      return { ...rec, version: rec.version + 1 }
    }
    // Creating answers with the new record (the page then opens it).
    if (p === '/v1/clients') return clientRecord(CLIENT_ID)
    if (p === '/v1/people') return personDetail(idOf(902))
    const person = p.match(/^\/v1\/people\/([^/]+)/)
    if (person) return personDetail(person[1]!)
    const decide = p.match(/^\/v1\/proposals\/([^/]+)\/decide$/)
    if (decide) {
      const b = (body ?? {}) as { outcome?: string, final_value?: unknown }
      const found = proposals().find(x => x.id === decide[1]) ?? proposals()[0]!
      const outcome = b.outcome === 'reject' ? 'rejected' : b.outcome === 'edit' ? 'edited' : 'accepted'
      return {
        outcome,
        proposal: { ...found, outcome, final_value: b.outcome === 'edit' ? b.final_value : null, decided_by: ME.person.display_name, decided_at: '2026-09-25T03:00:00Z' },
        card: found.target === 'request' ? undefined : { id: idOf(901), state: found.target === 'guideline' ? 'pending' : 'current', card_version: 8 }
      }
    }
    return { ok: true, echo: body ?? null }
  }

  if (p === '/v1/me') return ME
  if (p === '/v1/card-definition') return definition
  if (p === '/v1/ai/usage') return { month: '2026-09', spent_usd: state === 'cap' ? 5.0 : 0.37, cap_usd: 5, paused: state === 'cap' }
  if (p === '/v1/clients') return empty ? [] : clientsList(String(query?.status ?? 'active'))
  if (p === '/v1/approvals') {
    return empty ? [] : [{ id: idOf(800), client: { id: CLIENT_ID, name: 'Klinik Mata Sampang' }, part: 'guideline', field_key: 'visual', current_value: null, proposed_value: card(false).pending[0]!.value, set_by: 'Ardella Bernica', set_at: '2026-09-24T09:00:00Z' }]
  }
  if (p === '/v1/people') {
    const wanted = String(query?.status ?? 'active')
    return empty ? [] : PEOPLE.filter(x => wanted === 'all' || x.status === wanted)
  }
  let m = p.match(/^\/v1\/people\/([^/]+)$/)
  if (m) return personDetail(m[1]!)
  m = p.match(/^\/v1\/clients\/([^/]+)$/)
  if (m) return clientRecord(m[1]!)
  m = p.match(/^\/v1\/clients\/([^/]+)\/card$/)
  if (m) return card(empty)
  m = p.match(/^\/v1\/clients\/([^/]+)\/card\/(profil|guideline)\/([^/]+)\/history$/)
  if (m) {
    return empty
      ? []
      : [
          { id: idOf(810), state: 'current', value: card(false).profil.harga_promo?.value ?? 'Nilai sekarang', valid_until: '2026-12-31', source: SOURCE, set_by: 'Ardella Bernica', set_at: '2026-09-25T02:12:00Z', from_intake: idOf(400) },
          { id: idOf(811), state: 'superseded', value: [{ item: 'LASIK', harga: 'Rp 9.000.000', satuan: 'per mata', syarat: '', berlaku_sampai: '' }], valid_until: null, source: { who: 'Bu Rina (PIC)', where: 'WhatsApp grup', when: '2026-07-01' }, set_by: 'Defila Priana Falarima', set_at: '2026-07-01T03:00:00Z', from_intake: null },
          { id: idOf(812), state: 'corrected', value: [{ item: 'LASIK', harga: 'Rp 1.550.000', satuan: '', syarat: '', berlaku_sampai: '' }], valid_until: null, source: { who: 'Catatan lama', where: 'AnythingLLM', when: '2026-06-10' }, set_by: 'Defila Priana Falarima', set_at: '2026-06-10T03:00:00Z', from_intake: null }
        ]
  }
  m = p.match(/^\/v1\/clients\/([^/]+)\/requests$/)
  if (m) return requests(empty)
  m = p.match(/^\/v1\/clients\/([^/]+)\/intakes$/)
  if (m) {
    const i = intake(state)
    return [{ id: i.id, kind: i.kind, status: i.status, failure_reason: i.failure_reason, submitted_at: i.submitted_at, submitted_by: i.submitted_by, source_ref: null, proposals: i.proposals.length, pending: i.proposals.length }]
  }
  m = p.match(/^\/v1\/intakes\/([^/]+)$/)
  if (m) return intake(state, m[1])
  m = p.match(/^\/v1\/clients\/([^/]+)\/history$/)
  if (m) return empty ? [] : history()

  throw createError({ statusCode: 404, statusMessage: `No fixture for ${method} ${path}` })
}
