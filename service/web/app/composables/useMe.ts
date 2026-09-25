export interface Me {
  person: { id: string, display_name: string, email: string }
  roles: { role: string, noktah_brand: string | null }[]
  brands: string[]
  can: {
    edit_registry: boolean
    edit_profil: boolean
    edit_guideline: boolean
    approve: boolean
    manage_people: boolean
    appoint_bm: boolean
    run_intake: boolean
  }
}

/** The signed-in Person and what their roles allow; fetched once and shared. */
export function useMe() {
  return useFetch<Me>('/api/hub/v1/me', { key: 'me', dedupe: 'defer' })
}

export const ROLE_LABELS: Record<string, string> = {
  owner: 'Owner',
  brand_manager: 'Brand Manager',
  project_manager: 'Project Manager',
  account_executive: 'Account Executive',
  sales_marketing: 'Sales & Marketing',
  content_planner: 'Content Planner',
  field_associate: 'Field Associate',
  content_editor: 'Content Editor',
  qc: 'QC'
}
