/** Shapes of hub-api responses (specs/008-hub-registry-client-card/contracts/hub-api.md). */

export type Shape = 'text' | 'list' | 'object' | 'lines' | 'rating' | 'choice'

export interface SubfieldSpec {
  key: string
  label: string
  shape: Shape
  choices?: string
}

export interface FieldSpec {
  key: string
  label: string
  shape: Shape
  required?: boolean
  required_subfields?: string[]
  help?: string
  subfields?: SubfieldSpec[]
  choices?: string
}

export interface CardDefinition {
  version: string
  parts: Record<'profil' | 'guideline', { label: string, approval?: string, fields: FieldSpec[] }>
  choices: Record<string, { key: string, label: string }[]>
  always_banned: string[]
}

export interface Source { who: string | null, where: string | null, when: string | null }

export interface CurrentValue {
  id: string
  value: unknown
  valid_until: string | null
  expired: boolean
  source: Source
  set_by: string
  set_at: string
  from_intake: string | null
}

export interface PendingValue {
  id: string
  part: 'profil' | 'guideline'
  field_key: string
  value: unknown
  set_by: string
  set_at: string
}

export interface Card {
  card_version: number
  profil: Record<string, CurrentValue>
  guideline: Record<string, CurrentValue>
  pending: PendingValue[]
  completeness: {
    profil: { filled: number, required: number, missing: string[] }
    guideline: { filled: number, total: number, missing_required: string[] }
  }
  requests_open: number
  summary: null | {
    body: { siapa: string, promo_berjalan: string[], aturan_kunci: string[], permintaan_terbuka: string[] }
    generated_at: string
    stale: boolean
    cap_paused: boolean
  }
}

export interface HistoryItem extends CurrentValue {
  state: 'current' | 'pending' | 'superseded' | 'corrected' | 'rejected'
  reject_reason: string | null
  decided_by: string | null
  decided_at: string | null
}

export interface ClientRequest {
  id: string
  requested_on: string
  text: string
  requested_by: string | null
  is_pic: boolean
  channel: 'whatsapp_group' | 'meeting' | 'email' | 'lainnya'
  status: 'baru' | 'diproses' | 'selesai' | 'ditolak'
  reject_reason: string | null
  link: string | null
  version: number
}

export interface TeamMember { person_id: string, name: string, status: string }

export interface ClientRecord {
  id: string
  name: string
  status: 'active' | 'pending' | 'inactive'
  is_internal: boolean
  brand: string | null
  brand_name: string | null
  quotas: { post: number | null, story: number | null, short_video: number | null }
  drive_folder_id: string | null
  content_plan_folder_id: string | null
  jira_component_id: string | null
  team: Record<string, TeamMember | null>
  accounts: { account_id: string, platform: string, handle: string, relation: 'own' | 'competitor', is_active: boolean }[]
  version: number
  card_version: number
}

export type ProposalFlag = 'unverified' | 'from_image' | 'not_pic' | 'price_incomplete' | 'other_client' | 'contradicts' | 'not_bahasa'

export interface Proposal {
  id: string
  target: 'profil' | 'guideline' | 'request'
  field_key: string | null
  current_value: unknown
  proposed_value: unknown
  excerpt: string
  speaker: string | null
  spoke_at: string | null
  valid_until: string | null
  flags: ProposalFlag[]
  outcome: 'pending' | 'accepted' | 'edited' | 'rejected' | 'tidak_masuk_kartu'
  final_value: unknown
  ticks: { image_checked?: boolean, pic_confirmed?: boolean }
  decided_by: string | null
  decided_at: string | null
}

export interface Intake {
  id: string
  client: { id: string, name: string } | null
  kind: 'text' | 'image' | 'gdoc' | 'pdf_text' | 'pdf_scanned' | 'old_note'
  status: 'processing' | 'ready' | 'nothing_found' | 'failed' | 'unmatched' | 'discarded'
  failure_reason: string | null
  submitted_by: string | null
  submitted_at: string
  source_ref: string | null
  raw_available: boolean
  no_card_home: string[]
  proposals: Proposal[]
  cached?: boolean
  dropped?: { patient_data: number, invalid: number, unchanged: number }
}

export interface IntakeListItem {
  id: string
  kind: Intake['kind']
  status: Intake['status']
  failure_reason: string | null
  submitted_at: string
  submitted_by: string | null
  source_ref: string | null
  proposals: number
  pending: number
}

export interface AiUsage { month: string, spent_usd: number, cap_usd: number, paused: boolean }

export interface PersonRole { id: string, role: string, noktah_brand: string | null }

export interface Person {
  id: string
  display_name: string
  status: 'active' | 'left'
  jira_account_id: string | null
  slack_user_id: string | null
  emails: string[]
  units: string[]
  roles: PersonRole[]
  permissions: string[]
  /** "Mulai bekerja" (YYYY-MM-DD): starts the Incentive Framework's 30-day adaptation. */
  started_on?: string | null
  version?: number
}

export interface RegistryChange {
  id: number
  entity: 'client' | 'team' | 'account_link' | 'person' | 'person_email' | 'person_role' | 'person_unit' | 'person_permission'
  field: string
  old_value: unknown
  new_value: unknown
  person: string
  at: string
}

export interface AiCostMonth { calls: number, cost_usd: number }
export interface AiCostCase {
  key: string
  label: string
  used_by: string
  description: string
  models: string[]
  /** The model in use now; only known for the Hub's own cases. */
  current: string | null
  months: Record<string, AiCostMonth>
  this_month: number
  last_month: number
  total: number
}
export interface AiCosts {
  months: string[]
  cases: AiCostCase[]
  totals: Record<string, number>
  hub_cap: { cap_usd: number, spent_usd: number }
}

// ── spec 009: Otomasi & Laporan (specs/009-hub-otomasi-laporan/contracts/hub-api.md) ──

export interface ClientRef { id: string, name: string }

export interface AutomationRun {
  id?: string
  name: string | null
  deployment?: string
  /** Prefect state type: COMPLETED, FAILED, CRASHED, RUNNING, … */
  state: string | null
  state_name?: string | null
  started_at: string | null
  ended_at?: string | null
  failures?: number
}

export interface Automation {
  key: string
  label: string
  deployments: string[]
  /** Prefect couldn't be reached; runs are then empty. */
  unavailable?: boolean
  last_run: AutomationRun | null
  next_run_at: string | null
  /** Failed or crashed runs in the history. */
  failures?: number
  /** 90 days, newest first. */
  history: AutomationRun[]
}

export type PlanState = 'found' | 'missing' | 'ambiguous' | 'unreadable' | 'not_scanned'
export type PlanStatus = 'found' | 'greenlit' | 'changed_since_greenlight' | 'partly_created' | 'issues_created'
  | 'changed_after_issues' | 'missing' | 'ambiguous' | 'unreadable' | 'not_scanned'

export interface PlanSummary {
  /** Null when the plan was never scanned. */
  id: string | null
  client: ClientRef
  state: PlanState
  file_name: string | null
  file_url: string | null
  problem: string | null
  rows: number
  issues: number
  status: PlanStatus
  greenlit: { by: string, at: string } | null
  open_flags: number
  scanned_at?: string | null
}

export interface PlanList { month: string, scanned_at: string | null, plans: PlanSummary[] }

export type PlanFlagKind = 'changed_after_issue' | 'deleted_from_plan' | 'key_erased' | 'key_duplicated' | 'key_unknown'

export interface PlanFlag {
  id: string
  kind: PlanFlagKind
  issue_key: string | null
  row_number: number | null
  changes: { column: string, old: string | null, new: string | null }[]
  detected_at: string
  comment_state: 'not_needed' | 'pending' | 'posted' | 'failed'
}

export interface PlanProblem { code: string, message: string, row_number?: number | null }

export interface PlanRow {
  row_number: number
  tanggal: string | null
  bentuk: string | null
  topik: string | null
  issue_key: string | null
  flags: PlanFlagKind[]
}

export interface PlanDetail {
  id: string
  client: ClientRef
  month: string
  state: PlanState
  status: PlanStatus
  file_name: string | null
  file_url: string | null
  problem: string | null
  /** The plan as the manager sees it; sent back with a Greenlight. */
  fingerprint: string
  scanned_at: string | null
  quota: Record<string, { planned: number, quota: number | null }>
  blocking: PlanProblem[]
  warnings: PlanProblem[]
  team?: { field_associate: string | null, content_editor: string | null }
  rows: PlanRow[]
  rows_without_issue?: number
  flags: PlanFlag[]
  greenlights: { by: string, at: string }[]
  can_greenlight: boolean
  can_create: boolean
}

export type BatchStatus = 'queued' | 'running' | 'done' | 'failed'

export interface JiraBatchRow {
  plan_id?: string
  client: string
  row_number: number
  outcome: 'created' | 'failed' | 'refused'
  issue_key: string | null
  reason: string | null
}

export interface JiraBatch {
  id: string
  status: BatchStatus
  total: number
  created: number
  failed: number
  requested_by: string
  requested_at: string
  finished_at: string | null
  error?: string | null
  rows?: JiraBatchRow[]
}

export type HarvestOutcome = 'collected' | 'nothing_new' | 'blocked' | 'not_found' | 'failed' | 'skipped'

export interface HarvestAccount {
  id: string
  platform: string
  handle: string
  url: string
  client: ClientRef
  role: 'own' | 'competitor'
  last_harvested_at: string | null
  last_outcome: HarvestOutcome | null
  last_reason: string | null
  posts_collected: number | null
  status: 'ok' | 'blocked' | 'not_found' | 'failed' | 'never'
  first_harvest: boolean
}

export type ReviewMark = 'iklan' | 'tidak_relevan' | 'bukan_konten_akun_ini'

export interface HarvestPost {
  platform: string
  content_id: string
  url: string | null
  published_at: string | null
  content_type: string | null
  caption: string | null
  views: number | null
  likes: number | null
  comments: number | null
  shares: number | null
  marks: ReviewMark[]
}

export interface HarvestPosts {
  /** `clients`: every Client this account belongs to (an account may be shared). */
  account: HarvestAccount & { clients?: { client: ClientRef, role: 'own' | 'competitor' }[] }
  month: string
  posts: HarvestPost[]
}

// Laporan

export interface DeliveryCounts {
  /** Null when the Client has no Content Plan the Hub knows of. */
  planned: number | null
  created: number
  published: number
  late: number
  published_late: number
  cancelled: number
  on_hold: number
}

export interface LateItem {
  key: string
  topik: string | null
  publication_date: string | null
  status: string
  /** Station letter A–E, or null for a status outside the map. */
  station: string | null
  station_label?: string | null
  days_late: number
}

export interface DeliveryReport {
  month: string
  refreshed_at: string | null
  clients: (DeliveryCounts & { client: ClientRef, late_items: LateItem[] })[]
  totals: DeliveryCounts
}

export interface HeldHours { median: number | null, longest: number | null }

export interface StationRow {
  station: string
  label: string
  role: string
  in: number
  out: number
  returns: { total: number, by_origin: Record<string, number> }
  held_hours: HeldHours
  waiting: { key: string, client: string, since: string, hours: number }[]
}

export interface StationPerson {
  /** A Person, or someone Jira names who isn't in the Hub: {name: 'tidak terdaftar', account_id}. */
  person: { id?: string | null, name: string, account_id?: string }
  station: string
  in: number
  out: number
  returns_against: number
  held_hours: HeldHours
  waiting: number
}

export interface StationTeam {
  client: ClientRef
  first_pass: { rate: number | null, counted: number, target: number }
  qa_rounds: { average: number | null, counted: number, target: number }
  both_met: boolean
  returns: number
}

export interface StationReturn {
  key: string
  client?: string | null
  at: string
  from_status: string
  to_status: string
  /** Station of the Defect Category; null = "tanpa kategori" (G-35). */
  origin: string | null
  /** The Station that sent it back (where a "tanpa kategori" return is shown). */
  sent_back_by?: string | null
  defect: string | null
  reason: string | null
  bucket?: string | null
}

export interface StationsReport {
  month: string
  refreshed_at: string | null
  stations: StationRow[]
  people: StationPerson[]
  teams: StationTeam[]
  round_flags: { key: string, client: string, stations: string[], rounds: number }[]
  returns: StationReturn[]
  unknown_statuses: string[]
}

export interface MetricSummary { total: number | null, average: number | null, n: number, accounts?: number }
export interface RateSummary { value: number | null, n?: number, followers?: number | null, unavailable?: string | null }

export interface PerfPost {
  platform: string
  content_id: string
  url: string | null
  handle?: string | null
  published_at: string | null
  content_type: string | null
  caption: string | null
  views: number | null
  likes: number | null
  comments: number | null
  shares: number | null
  engagement?: number | null
  marks?: ReviewMark[]
}

export type PerfAccount = { platform: string, handle: string, followers?: number | null } | string

export interface PerfSide {
  accounts: PerfAccount[]
  posts: number
  /** Posts left after marked ones are set aside. */
  posts_counted?: number
  posts_by_type: Record<string, number>
  views: MetricSummary
  likes: MetricSummary
  comments: MetricSummary
  shares: MetricSummary
  engagement_rate_views: RateSummary
  engagement_rate_followers: RateSummary
  top_posts?: PerfPost[]
  marked?: PerfPost[]
  /** Change from last month, as a fraction (0.12 = +12%), per figure. */
  change?: Record<string, number | null>
}

export interface PerformanceReport {
  client: ClientRef
  month: string
  refreshed_at?: string | null
  own: PerfSide
  /** The competitors' average per account, with the same figures as `own`. */
  competitors: { accounts: PerfAccount[], average: Partial<PerfSide>, n_accounts: number } | null
}

export interface PerformanceList {
  month: string
  refreshed_at?: string | null
  clients: { client: ClientRef, accounts?: number, posts?: number, views_total?: number | null, engagement_rate_views?: number | null }[]
}

export type SanctionLevel = 'teguran_lisan' | 'sp1' | 'sp2' | 'sp3' | 'peringatan_terakhir' | 'phk_flag'
export type SanctionState = 'computed' | 'held' | 'issued' | 'on_appeal' | 'flagged'
export type EventState = 'counted' | 'zero' | 'belum_lengkap' | 'reference' | 'adaptation' | 'on_hold' | 'not_judged'

export interface IncentiveSanction {
  id: string
  level: SanctionLevel
  level_label?: string
  with_pip?: boolean
  source?: 'recorded' | 'ladder' | 'direct'
  state: SanctionState
  /** `YYYY-MM` of the points behind it; null for a recorded or direct sanction. */
  period?: string | null
  category_code?: string | null
  event_keys?: string[]
  points?: number | null
  hold_until: string | null
  hold_reason?: string | null
  issued_on?: string | null
  valid_until?: string | null
  letter_number?: string | null
  letter_state?: 'not_needed' | 'pending' | 'written' | 'failed'
  letter_url?: string | null
  note?: string | null
}

export interface IncentiveEvent {
  key: string
  summary?: string | null
  type: 'violation' | 'excellence' | string | null
  category: string | null
  /** Signed: negative for a Violation, positive for an Excellence. */
  points: number | null
  state: EventState
  reason: string | null
  missing?: string[]
  formula?: string | null
  direct_item?: string | null
  status?: string | null
  created_at?: string | null
  judged_at?: string | null
}

export interface IncentivePerson {
  person: { id: string, name: string }
  /** ≤ 0 */
  violation_points: number
  /** ≥ 0 */
  excellence_points: number
  sanctions?: IncentiveSanction[]
  /** The newest of `sanctions`. */
  sanction: IncentiveSanction | null
  team_reward: { qualifies: boolean, teams_met: number, teams: number } | null
  events: IncentiveEvent[]
}

export interface IncentiveReport {
  month: string
  refreshed_at: string | null
  levels?: Record<string, string>
  people: IncentivePerson[]
  incomplete: { key: string, summary?: string | null, missing: string[], person?: string | null }[]
  /** Events whose Person/Assignee isn't anyone in the Hub. */
  unmatched?: { key: string, account_id: string | null }[]
  direct_sanctions_manual: { key: string, person: string | null, summary?: string | null }[]
}
