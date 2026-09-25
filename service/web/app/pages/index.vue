<script setup lang="ts">
import type { TableColumn } from '@nuxt/ui'

interface ClientRow {
  id: string
  name: string
  status: 'active' | 'pending' | 'inactive'
  is_internal: boolean
  brand: string | null
  brand_name: string | null
  card_completeness: { profil: string, guideline: string }
  pending_approvals: number
  team_summary: { account_executive: string | null, field_associate: string | null, content_editor: string | null }
}

const STATUS = [
  { label: 'Aktif', value: 'active' },
  { label: 'Menunggu', value: 'pending' },
  { label: 'Tidak aktif', value: 'inactive' },
  { label: 'Semua', value: 'all' }
]
const STATUS_LABEL: Record<string, string> = { active: 'Aktif', pending: 'Menunggu', inactive: 'Tidak aktif' }
const STATUS_COLOR: Record<string, 'success' | 'warning' | 'neutral'> = { active: 'success', pending: 'warning', inactive: 'neutral' }

const status = ref('active')
const search = ref('')
const { data: me } = await useMe()
const { data: clients, error, status: loading } = await useFetch<ClientRow[]>('/api/hub/v1/clients', {
  query: { status },
  default: () => []
})

const showBrand = computed(() => (me.value?.brands?.length ?? 0) > 1)
const rows = computed(() => {
  const q = search.value.trim().toLowerCase()
  return q ? clients.value.filter(c => c.name.toLowerCase().includes(q)) : clients.value
})

const TEAM = [
  { key: 'account_executive', label: 'Account Executive' },
  { key: 'field_associate', label: 'Field Associate' },
  { key: 'content_editor', label: 'Content Editor' }
] as const

const columns = computed<TableColumn<ClientRow>[]>(() => [
  { accessorKey: 'name', header: 'Klien', meta: { class: { td: 'whitespace-normal' } } },
  ...(showBrand.value ? [{ accessorKey: 'brand_name', header: 'Noktah Brand', meta: { class: { th: 'hidden md:table-cell', td: 'hidden md:table-cell' } } } as TableColumn<ClientRow>] : []),
  { accessorKey: 'status', header: 'Status', meta: { class: { th: 'w-px', td: 'w-px' } } },
  // Below xl, completeness sits under the name (see the name cell) instead of taking a
  // column: at 1024px it would push the Content Editor column off-screen, and on phones
  // off the table (UI sweep screenshots). Two short lines rather than one long one.
  { id: 'completeness', header: 'Kartu', meta: { class: { th: 'hidden xl:table-cell', td: 'hidden xl:table-cell whitespace-nowrap' } } },
  // The team, one column per role; below lg they would push the table off-screen. The
  // name inside each cell carries a minimum width (table layout ignores a cell's own),
  // which keeps a name to two lines at most (the sweep measured four at 46px).
  ...TEAM.map(t => ({ id: t.key, header: t.label, meta: { class: { th: 'hidden lg:table-cell', td: 'hidden lg:table-cell whitespace-normal' } } }))
])
</script>

<template>
  <UDashboardPanel id="clients">
    <template #header>
      <UDashboardNavbar title="Klien">
        <template #leading>
          <UDashboardSidebarCollapse />
        </template>
      </UDashboardNavbar>
      <UDashboardToolbar>
        <template #left>
          <UInput
            v-model="search"
            icon="i-lucide-search"
            placeholder="Cari klien…"
            class="w-full sm:w-64"
            aria-label="Cari klien"
          />
        </template>
        <template #right>
          <USelect
            v-model="status"
            :items="STATUS"
            class="w-36"
            aria-label="Filter status"
          />
        </template>
      </UDashboardToolbar>
    </template>

    <template #body>
      <UAlert
        v-if="error"
        color="error"
        icon="i-lucide-server-off"
        :title="hubError(error).message"
      />
      <UTable
        v-else
        :data="rows"
        :columns="columns"
        :loading="loading === 'pending'"
        empty="Belum ada klien."
      >
        <template #name-cell="{ row }">
          <NuxtLink
            :to="`/clients/${row.original.id}`"
            class="font-medium text-highlighted hover:underline"
          >
            {{ row.original.name }}
          </NuxtLink>
          <UBadge
            v-if="row.original.is_internal"
            label="Internal"
            variant="subtle"
            color="neutral"
            size="sm"
            class="ms-2"
          />
          <UBadge
            v-if="row.original.pending_approvals"
            :label="`${row.original.pending_approvals} menunggu persetujuan`"
            variant="subtle"
            color="warning"
            size="sm"
            class="ms-2"
          />
          <p class="xl:hidden mt-1 text-xs text-muted">
            Profil {{ row.original.card_completeness.profil }} · Guideline {{ row.original.card_completeness.guideline }}
          </p>
        </template>
        <template #status-cell="{ row }">
          <UBadge
            :color="STATUS_COLOR[row.original.status]"
            variant="subtle"
          >
            {{ STATUS_LABEL[row.original.status] }}
          </UBadge>
        </template>
        <template #completeness-cell="{ row }">
          <span class="block text-sm">Profil {{ row.original.card_completeness.profil }}</span>
          <span class="block text-sm text-muted">Guideline {{ row.original.card_completeness.guideline }}</span>
        </template>
        <template
          v-for="t in TEAM"
          :key="t.key"
          #[`${t.key}-cell`]="{ row }"
        >
          <span
            v-if="row.original.team_summary[t.key]"
            class="block min-w-28 text-sm"
          >{{ row.original.team_summary[t.key] }}</span>
          <span
            v-else
            class="text-sm text-muted"
          >—</span>
        </template>
      </UTable>
    </template>
  </UDashboardPanel>
</template>
