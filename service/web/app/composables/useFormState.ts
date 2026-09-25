/**
 * "Is there anything to save?" for every form: Save stays disabled until the form
 * differs from what is stored, and disabled again when an edit is undone or merely
 * retyped the same. Compared the way the API stores values: strings trimmed, blank
 * and missing both mean empty, object keys in any order.
 */
export function normalizeForm(v: unknown): unknown {
  if (v === undefined || v === null) return null
  if (typeof v === 'string') return v.trim() === '' ? null : v.trim()
  if (Array.isArray(v)) return v.map(normalizeForm)
  if (typeof v === 'object') {
    return Object.fromEntries(Object.entries(v as Record<string, unknown>)
      .map(([k, x]) => [k, normalizeForm(x)] as const)
      .filter(([, x]) => x !== null)
      .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0)))
  }
  return v
}

export function sameForm(a: unknown, b: unknown): boolean {
  return JSON.stringify(normalizeForm(a)) === JSON.stringify(normalizeForm(b))
}

/** A list compared as a set (emails, roles): order doesn't make a change. */
export function sortedForm<T>(list: T[], key: (x: T) => string = x => String(x)): T[] {
  return [...list].sort((a, b) => key(a).localeCompare(key(b)))
}
