import type { Person, PersonRole } from '~/types/hub'
import type { Me } from './useMe'

/**
 * The profile form shared by "Tambah orang" and a Person's page. Roles are picked
 * as role × Noktah Brand pairs, several at once (G-9). A role the signed-in Manager
 * may not grant or end (Owner, Brand Manager, unless they are the Owner) is shown
 * but locked, so saving never tries to remove it.
 */
export interface PersonForm {
  display_name: string
  jira_account_id: string
  slack_user_id: string
  emails: string[]
  roles: string[]
}

export const brandName = (key: string | null) => (key ? key.charAt(0).toUpperCase() + key.slice(1) : '')
export const roleValue = (role: string, brand: string | null) => `${role}:${role === 'owner' ? '' : brand ?? ''}`
export function parseRole(value: string): { role: string, brand: string | null } {
  const [role, brand] = value.split(':')
  return { role: role!, brand: brand || null }
}

export function personForm(p?: Person | null): PersonForm {
  return {
    display_name: p?.display_name ?? '',
    jira_account_id: p?.jira_account_id ?? '',
    slack_user_id: p?.slack_user_id ?? '',
    emails: [...(p?.emails ?? [])],
    roles: (p?.roles ?? []).map(r => roleValue(r.role, r.noktah_brand))
  }
}

/** The form as the API will store it: emails lowercased, emails and roles as sets. */
export function personFormKey(f: PersonForm): unknown {
  return {
    ...f,
    emails: sortedForm(f.emails.map(e => e.trim().toLowerCase()).filter(Boolean)),
    roles: sortedForm(f.roles)
  }
}

export function personBody(f: PersonForm) {
  return {
    display_name: f.display_name,
    jira_account_id: f.jira_account_id.trim() || null,
    slack_user_id: f.slack_user_id.trim() || null,
    emails: f.emails,
    roles: f.roles.map(parseRole)
  }
}

export function useRoleItems(me: Ref<Me | null | undefined>, held: () => PersonRole[]) {
  return computed(() => {
    const brands = me.value?.brands ?? []
    const mayGrant = (role: string) => !!me.value?.can.appoint_bm || !['owner', 'brand_manager'].includes(role)
    const label = (role: string, brand: string | null) =>
      `${ROLE_LABELS[role] ?? role}${brand && (brands.length > 1 || !brands.includes(brand)) ? ` · ${brandName(brand)}` : ''}`
    const items: { label: string, value: string, disabled?: boolean }[] = []
    for (const role of Object.keys(ROLE_LABELS)) {
      if (!mayGrant(role)) continue
      if (role === 'owner') items.push({ label: label(role, null), value: roleValue(role, null) })
      else for (const b of brands) items.push({ label: label(role, b), value: roleValue(role, b) })
    }
    for (const r of held()) {
      const value = roleValue(r.role, r.noktah_brand)
      const found = items.find(i => i.value === value)
      if (!found) items.push({ label: label(r.role, r.noktah_brand), value, disabled: true })
      else if (!mayGrant(r.role)) found.disabled = true
    }
    return items
  })
}
