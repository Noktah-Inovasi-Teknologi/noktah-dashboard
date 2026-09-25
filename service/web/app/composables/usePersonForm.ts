import type { Person } from '~/types/hub'
import type { Me } from './useMe'

/**
 * The profile form shared by "Tambah orang" and a Person's page. A Person has
 * three separate parameters (migration 012): the Units they work in, their roles
 * (each from one of those Units' catalog), and their permissions. Anything the
 * signed-in manager may not change is shown but locked, so saving never tries to.
 */
export interface PersonForm {
  display_name: string
  jira_account_id: string
  slack_user_id: string
  emails: string[]
  units: string[]
  roles: string[]
  permissions: string[]
}

export interface Choice { label: string, value: string, disabled?: boolean }

export const roleValue = (role: string, unit: string | null) => `${role}:${unit ?? ''}`
export function parseRole(value: string): { role: string, brand: string | null } {
  const [role, unit] = value.split(':')
  return { role: role!, brand: unit || null }
}

export function personForm(p?: Person | null): PersonForm {
  return {
    display_name: p?.display_name ?? '',
    jira_account_id: p?.jira_account_id ?? '',
    slack_user_id: p?.slack_user_id ?? '',
    emails: [...(p?.emails ?? [])],
    units: [...(p?.units ?? [])],
    roles: (p?.roles ?? []).map(r => roleValue(r.role, r.noktah_brand)),
    permissions: [...(p?.permissions ?? [])]
  }
}

/** Any permission implies Masuk Hub, as the API stores it. */
export function withHubAccess(perms: string[]): string[] {
  return perms.length && !perms.includes('hub_access') ? ['hub_access', ...perms] : perms
}

/** The form as the API will store it: emails lowercased, lists as sets. */
export function personFormKey(f: PersonForm): unknown {
  return {
    ...f,
    emails: sortedForm(f.emails.map(e => e.trim().toLowerCase()).filter(Boolean)),
    units: sortedForm(f.units),
    roles: sortedForm(f.roles),
    permissions: sortedForm(withHubAccess(f.permissions))
  }
}

export function personBody(f: PersonForm) {
  return {
    display_name: f.display_name,
    jira_account_id: f.jira_account_id.trim() || null,
    slack_user_id: f.slack_user_id.trim() || null,
    emails: f.emails,
    units: f.units,
    roles: f.roles.map(parseRole),
    permissions: withHubAccess(f.permissions)
  }
}

const OWNER_ONLY = ['owner', 'brand_manager']

/**
 * The choices the form offers the signed-in manager, given what the Person holds
 * now (`held`) and the Units picked on the form (`picked`).
 */
export function usePersonChoices(me: Ref<Me | null | undefined>, held: () => Person | null | undefined,
  picked: () => string[]) {
  const { unitName } = useCatalog()
  const isOwner = computed(() => !!me.value?.can.appoint_bm)
  const mayUnit = (unit: string) => me.value?.manageable_units.includes(unit) ?? false
  const mayRole = (role: string, unit: string) => mayUnit(unit) && (isOwner.value || !OWNER_ONLY.includes(role))
  const mayPermission = (p: string) => isOwner.value || (me.value?.permissions.includes(p) ?? false)

  const units = computed<Choice[]>(() => (me.value?.catalog ?? [])
    .filter(u => mayUnit(u.key) || held()?.units.includes(u.key))
    .map(u => ({ label: u.name, value: u.key, disabled: !mayUnit(u.key) })))

  const roles = computed<Choice[]>(() => {
    const chosen = picked()
    const withUnit = chosen.length > 1
    const items: Choice[] = []
    for (const u of me.value?.catalog ?? []) {
      if (!chosen.includes(u.key)) continue
      for (const r of u.roles) {
        const value = roleValue(r.key, u.key)
        const heldNow = held()?.roles.some(x => roleValue(x.role, x.noktah_brand) === value)
        if (!mayRole(r.key, u.key) && !heldNow) continue
        items.push({ label: withUnit ? `${r.name} · ${u.name}` : r.name, value, disabled: !mayRole(r.key, u.key) })
      }
    }
    // A role held outside the catalog (a renamed one) still shows, locked.
    for (const x of held()?.roles ?? []) {
      const value = roleValue(x.role, x.noktah_brand)
      if (!items.some(i => i.value === value) && chosen.includes(x.noktah_brand ?? '')) {
        items.push({ label: `${x.role} · ${unitName(x.noktah_brand)}`, value, disabled: true })
      }
    }
    return items
  })

  const permissions = computed(() => PERMISSIONS.map(p => ({ ...p, disabled: !mayPermission(p.key) })))

  return { units, roles, permissions, mayPermission }
}
