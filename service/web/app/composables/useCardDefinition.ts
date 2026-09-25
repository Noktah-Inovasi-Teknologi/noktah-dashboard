import type { CardDefinition, FieldSpec } from '~/types/hub'

/** The Client Card definition (config/hub/card_v1.yaml via hub-api); fetched once. */
export function useCardDefinition() {
  return useFetch<CardDefinition>('/api/hub/v1/card-definition', { key: 'card-definition', dedupe: 'defer' })
}

export function fieldSpec(def: CardDefinition | null | undefined, part: 'profil' | 'guideline', key: string): FieldSpec | undefined {
  return def?.parts[part].fields.find(f => f.key === key)
}

export function choiceLabel(def: CardDefinition | null | undefined, list: string | undefined, key: unknown): string {
  if (!list || typeof key !== 'string') return String(key ?? '')
  return def?.choices[list]?.find(c => c.key === key)?.label ?? key
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString('id-ID', { day: 'numeric', month: 'short', year: 'numeric' })
}

export function isEmptyValue(v: unknown): boolean {
  if (v === null || v === undefined) return true
  if (typeof v === 'string') return v.trim() === ''
  if (Array.isArray(v)) return v.every(isEmptyValue)
  if (typeof v === 'object') return Object.values(v as Record<string, unknown>).every(isEmptyValue)
  return false
}
