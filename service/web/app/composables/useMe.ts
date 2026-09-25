export interface CatalogRole { key: string, name: string, team_slot: boolean, default_permissions: string[] }
export interface CatalogUnit { key: string, name: string, kind: 'group' | 'brand', roles: CatalogRole[] }

export interface Me {
  person: { id: string, display_name: string, email: string }
  roles: { role: string, noktah_brand: string | null }[]
  units: string[]
  permissions: string[]
  /** Client brands this Person can see. */
  brands: string[]
  /** Units this Person may put people in. */
  manageable_units: string[]
  /** Every Unit and its roles: the one place roles are defined (API, migration 012). */
  catalog: CatalogUnit[]
  can: {
    edit_registry: boolean
    edit_profil: boolean
    edit_guideline: boolean
    approve: boolean
    edit_requests: boolean
    manage_people: boolean
    appoint_bm: boolean
    run_intake: boolean
    view_ai_costs: boolean
  }
}

/** The signed-in Person and what they may do; fetched once and shared. */
export function useMe() {
  return useFetch<Me>('/api/hub/v1/me', { key: 'me', dedupe: 'defer' })
}

/** What each permission lets someone do, in the order the form lists them. */
export const PERMISSIONS: { key: string, label: string, help: string }[] = [
  { key: 'hub_access', label: 'Masuk Hub', help: 'Melihat klien dan kartunya. Ikut tercentang bila ada izin lain.' },
  { key: 'edit_clients', label: 'Ubah Registry', help: 'Status, kuota, folder, tim, dan akun sosial klien.' },
  { key: 'edit_profil', label: 'Ubah Profil', help: 'Mengubah Profil klien dan mengusulkan perubahan Guideline.' },
  { key: 'approve_guideline', label: 'Setujui Guideline', help: 'Mengubah Guideline langsung dan menyetujui perubahan yang menunggu persetujuan.' },
  { key: 'edit_requests', label: 'Kelola permintaan', help: 'Mencatat dan memperbarui permintaan klien.' },
  { key: 'run_intake', label: 'Jalankan Intake', help: 'Mengirim chat, screenshot, atau dokumen klien untuk dibaca AI.' },
  { key: 'manage_people', label: 'Kelola orang', help: 'Menambah orang dan mengatur unit, peran, dan izinnya.' }
]

// Role keys renamed by migration 012; old history rows still carry them.
const LEGACY_ROLES: Record<string, string> = { project_manager: 'Production Manager', qc: 'Quality Assurance' }

/** Names from the catalog: units, roles, and a role's label with its Unit when that helps. */
export function useCatalog() {
  const { data: me } = useNuxtData<Me>('me')
  const units = computed(() => me.value?.catalog ?? [])
  const unitName = (key: string | null | undefined) =>
    units.value.find(u => u.key === key)?.name ?? (key ? key.charAt(0).toUpperCase() + key.slice(1) : '')
  const roleName = (key: string) => {
    for (const u of units.value) {
      const r = u.roles.find(x => x.key === key)
      if (r) return r.name
    }
    return LEGACY_ROLES[key] ?? key
  }
  const roleIn = (role: string, unit: string | null | undefined) =>
    units.value.find(u => u.key === unit)?.roles.find(r => r.key === role)
  return { units, unitName, roleName, roleIn }
}
