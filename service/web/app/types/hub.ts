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
  roles: PersonRole[]
  version?: number
}

export interface RegistryChange {
  id: number
  entity: 'client' | 'team' | 'account_link' | 'person' | 'person_email' | 'person_role'
  field: string
  old_value: unknown
  new_value: unknown
  person: string
  at: string
}
