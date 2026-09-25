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
  team_summary: { account_executive: string | null, field_associate: string | null }
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

const columns = computed<TableColumn<ClientRow>[]>(() => [
  { accessorKey: 'name', header: 'Klien', meta: { class: { td: 'whitespace-normal' } } },
  ...(showBrand.value ? [{ accessorKey: 'brand_name', header: 'Noktah Brand', meta: { class: { th: 'hidden md:table-cell', td: 'hidden md:table-cell' } } } as TableColumn<ClientRow>] : []),
  { accessorKey: 'status', header: 'Status', meta: { class: { th: 'w-px', td: 'w-px' } } },
  // On phones these two sit under the name (see the name cell) instead of
  // pushing the table wider than the screen (UI sweep, 360px screenshot).
  { id: 'completeness', header: 'Kelengkapan kartu', meta: { class: { th: 'hidden md:table-cell', td: 'hidden md:table-cell whitespace-nowrap' } } },
  { id: 'team', header: 'Tim', meta: { class: { th: 'hidden lg:table-cell', td: 'hidden lg:table-cell whitespace-normal' } } }
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
          <p class="md:hidden mt-1 text-xs text-muted">
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
          <span class="text-sm">Profil {{ row.original.card_completeness.profil }}</span>
          <span class="text-sm text-muted"> · Guideline {{ row.original.card_completeness.guideline }}</span>
        </template>
        <template #team-cell="{ row }">
          <span class="text-sm">
            AE: {{ row.original.team_summary.account_executive ?? '—' }} · FA: {{ row.original.team_summary.field_associate ?? '—' }}
          </span>
        </template>
      </UTable>
    </template>
  </UDashboardPanel>
</template>
