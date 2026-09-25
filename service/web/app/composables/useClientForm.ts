import type { ClientRecord } from '~/types/hub'

/** The Client details form, shared by "Tambah klien" and the Registry tab. */
export interface ClientForm {
  name: string
  status: 'active' | 'pending' | 'inactive'
  quota_post: number | null
  quota_story: number | null
  quota_short_video: number | null
  drive_folder_id: string
  content_plan_folder_id: string
  jira_component_id: string
}

export const CLIENT_STATUS_ITEMS = [
  { label: 'Aktif', value: 'active' }, { label: 'Menunggu', value: 'pending' }, { label: 'Tidak aktif', value: 'inactive' }
]

export function clientForm(c?: ClientRecord | null): ClientForm {
  return {
    name: c?.name ?? '', status: c?.status ?? 'pending',
    quota_post: c?.quotas.post ?? null, quota_story: c?.quotas.story ?? null, quota_short_video: c?.quotas.short_video ?? null,
    drive_folder_id: c?.drive_folder_id ?? '', content_plan_folder_id: c?.content_plan_folder_id ?? '',
    jira_component_id: c?.jira_component_id ?? ''
  }
}
