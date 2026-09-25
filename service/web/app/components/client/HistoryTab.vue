<script setup lang="ts">
import type { RegistryChange } from '~/types/hub'

/** Riwayat: every Registry change to this Client, newest first (FR-013). Read-only. */
const props = defineProps<{ clientId: string }>()
const { data: items, error } = await useFetch<RegistryChange[]>(
  () => `/api/hub/v1/clients/${props.clientId}/history`, { default: () => [] })

const FIELD: Record<string, string> = {
  created: 'Klien dibuat', display_name: 'Nama', status: 'Status', quota_post: 'Kuota Post',
  quota_story: 'Kuota Story', quota_short_video: 'Kuota Short Video', drive_folder_id: 'Folder Drive',
  content_plan_folder_id: 'Folder content plan', jira_component_id: 'Komponen Jira', noktah_brand: 'Noktah Brand',
  relation: 'Akun sosial'
}
const { roleName } = useCatalog()
const STATUS: Record<string, string> = { active: 'Aktif', pending: 'Menunggu', inactive: 'Tidak aktif' }
const RELATION: Record<string, string> = { owned: 'milik klien', own: 'milik klien', competitor: 'pesaing' }
const KINDS = [
  { label: 'Semua perubahan', value: 'all' }, { label: 'Data klien', value: 'client' },
  { label: 'Tim', value: 'team' }, { label: 'Akun sosial', value: 'account_link' }
]
const kind = ref('all')
const search = ref('')
const shown = computed(() => {
  const q = search.value.trim().toLowerCase()
  return items.value.filter(h => (kind.value === 'all' || h.entity === kind.value)
    && (!q || [FIELD[h.field] ?? roleName(h.field), show(h.old_value, h.field), show(h.new_value, h.field), h.person]
      .some(x => x.toLowerCase().includes(q))))
})
const { page, pageRows, pageSize, total } = usePaged(shown)

function show(value: unknown, field: string): string {
  if (value === null || value === undefined || value === '') return '—'
  if (field === 'status' && typeof value === 'string') return STATUS[value] ?? value
  if (typeof value === 'object') {
    const v = value as Record<string, unknown>
    if ('account' in v) {
      return `${String(v.account).replace(':', ' @')} · ${RELATION[String(v.relation)] ?? v.relation}${v.is_active === false ? ' · nonaktif' : ''}`
    }
    if ('name' in v) return String(v.name)
    return Object.values(v).filter(x => x !== null && x !== '').join(' · ')
  }
  return String(value)
}
</script>

<template>
  <UAlert
    v-if="error"
    color="error"
    icon="i-lucide-server-off"
    :title="hubError(error).message"
  />
  <div
    v-else
    class="space-y-4"
  >
    <div
      v-if="items.length"
      class="flex flex-wrap items-center gap-2"
    >
      <UInput
        v-model="search"
        icon="i-lucide-search"
        placeholder="Cari perubahan atau nama…"
        class="w-full sm:w-64"
        aria-label="Cari riwayat"
      />
      <USelect
        v-model="kind"
        :items="KINDS"
        class="w-44"
        aria-label="Filter jenis perubahan"
      />
    </div>
    <UEmpty
      v-if="!items.length"
      icon="i-lucide-history"
      title="Belum ada perubahan"
      description="Perubahan data klien, tim, dan akun akan tercatat di sini."
    />
    <UEmpty
      v-else-if="!shown.length"
      icon="i-lucide-search-x"
      title="Tidak ada perubahan yang cocok dengan filter"
    />
    <ol
      v-else
      class="relative space-y-3 border-s border-default ps-5"
    >
      <li
        v-for="h in pageRows"
        :key="h.id"
        class="relative"
      >
        <span class="absolute inset-s-[-1.6rem] top-1.5 size-2.5 rounded-full bg-primary ring-4 ring-default" />
        <p class="text-sm">
          <span class="font-medium">{{ FIELD[h.field] ?? roleName(h.field) }}</span>
          <template v-if="h.field !== 'created'">
            : <template v-if="h.old_value !== null && h.old_value !== undefined">
              <span class="text-muted line-through decoration-1 wrap-break-word">{{ show(h.old_value, h.field) }}</span> →
            </template>
            <span class="wrap-break-word">{{ show(h.new_value, h.field) }}</span>
          </template>
        </p>
        <p class="text-xs text-muted">
          {{ h.person }} · {{ formatDate(h.at) }}
        </p>
      </li>
    </ol>
    <ListPager
      v-model:page="page"
      :total="total"
      :page-size="pageSize"
    />
  </div>
</template>
