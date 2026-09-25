import type { Ref } from 'vue'

/**
 * Client-side pages for a filtered list. The page goes back to 1 whenever the
 * filtered rows change (a new filter would otherwise leave you on an empty page 3).
 */
export function usePaged<T>(rows: Ref<T[]>, pageSize = 20) {
  const page = ref(1)
  watch(() => rows.value.length, () => (page.value = 1))
  watch(rows, () => {
    const last = Math.max(1, Math.ceil(rows.value.length / pageSize))
    if (page.value > last) page.value = last
  })
  const pageRows = computed(() => rows.value.slice((page.value - 1) * pageSize, page.value * pageSize))
  return { page, pageRows, pageSize, total: computed(() => rows.value.length) }
}
