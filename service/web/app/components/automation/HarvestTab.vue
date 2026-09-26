<script setup lang="ts">
import type { TableColumn } from '@nuxt/ui'
import type { HarvestAccount } from '~/types/hub'

/**
 * Otomasi → Harvest (US3, FR-033): every own and competitor account of every active Eskala
 * Client in the Registry, its last Harvest, and "Jalankan sekarang" for one account.
 */
const toast = useToast()
const { data, error, status } = await useFetch<{ accounts: HarvestAccount[] }>('/api/hub/v1/harvest/accounts', {
  default: () => ({ accounts: [] })
})

const ALL = '__all__'
const client = ref(ALL)
const role = ref(ALL)
const state = ref('all')
const search = ref('')
const clientItems = computed(() => {
  const names = new Map<string, string>()
  for (const a of data.value.accounts) names.set(a.client.id, a.client.name)
  return [{ label: 'Semua klien', value: ALL },
    ...[...names].sort((a, b) => a[1].localeCompare(b[1])).map(([value, label]) => ({ label, value }))]
})
const ROLE_FILTER = [
  { label: 'Milik klien & pesaing', value: ALL },
  { label: 'Milik klien', value: 'own' },
  { label: 'Pesaing', value: 'competitor' }
]
const rows = computed(() => {
  const q = search.value.trim().toLowerCase().replace(/^@/, '')
  return data.value.accounts.filter(a =>
    (!q || a.handle.toLowerCase().includes(q) || a.client.name.toLowerCase().includes(q))
    && (client.value === ALL || a.client.id === client.value)
    && (role.value === ALL || a.role === role.value)
    && (state.value === 'all' || a.status === state.value))
})
const { page, pageRows, pageSize, total } = usePaged(rows)
const filtered = computed(() => client.value !== ALL || role.value !== ALL || state.value !== 'all' || search.value.trim() !== '')
function clearFilters() {
  client.value = ALL
  role.value = ALL
  state.value = 'all'
  search.value = ''
}

const columns: TableColumn<HarvestAccount>[] = [
  { id: 'account', header: 'Akun', meta: { class: { td: 'whitespace-normal' } } },
  { id: 'client', header: 'Klien', meta: { class: { th: 'hidden lg:table-cell', td: 'hidden lg:table-cell whitespace-normal' } } },
  { id: 'role', header: 'Jenis', meta: { class: { th: 'hidden sm:table-cell w-px', td: 'hidden sm:table-cell w-px' } } },
  { id: 'last', header: 'Harvest terakhir', meta: { class: { th: 'hidden sm:table-cell', td: 'hidden sm:table-cell whitespace-normal' } } },
  { id: 'posts', header: 'Post', meta: { class: { th: 'hidden lg:table-cell text-right', td: 'hidden lg:table-cell text-right tabular-nums' } } },
  { id: 'actions', meta: { class: { th: 'w-px', td: 'w-px' } } }
]

const starting = ref<string | null>(null)
async function runNow(a: HarvestAccount) {
  starting.value = `${a.id}-${a.client.id}`
  try {
    await $fetch(`/api/hub/v1/harvest/accounts/${a.id}/run`, { method: 'POST' })
    toast.add({ color: 'success', title: 'Harvest dimulai', description: `@${a.handle} dikumpulkan sekarang; hasilnya tampil di sini setelah selesai.` })
  } catch (err) {
    toast.add({ color: 'error', title: 'Gagal', description: hubError(err).message })
  } finally {
    starting.value = null
  }
}
</script>

<template>
  <div class="space-y-4">
    <p class="text-sm text-muted">
      Semua akun sosial klien Eskala yang aktif dan pesaingnya, diambil dari Registry. Akun baru dikumpulkan 90 hari ke belakang pada Harvest pertamanya, lalu 31 hari tiap bulan.
    </p>
    <div class="grid grid-cols-2 gap-2 sm:flex sm:flex-wrap sm:items-center">
      <UInput
        v-model="search"
        icon="i-lucide-search"
        placeholder="Cari akun atau klien…"
        class="col-span-2 w-full sm:w-64"
        aria-label="Cari akun"
      />
      <USelect
        v-model="client"
        :items="clientItems"
        class="col-span-2 w-full sm:w-56"
        aria-label="Filter klien"
      />
      <USelect
        v-model="role"
        :items="ROLE_FILTER"
        class="w-full sm:w-56"
        aria-label="Filter jenis akun"
      />
      <USelect
        v-model="state"
        :items="HARVEST_STATUS_FILTER"
        class="w-full sm:w-48"
        aria-label="Filter status"
      />
      <UButton
        v-if="filtered"
        color="neutral"
        variant="ghost"
        icon="i-lucide-x"
        label="Hapus filter"
        @click="clearFilters"
      />
    </div>

    <UAlert
      v-if="error"
      color="error"
      icon="i-lucide-server-off"
      :title="hubError(error).message"
    />
    <template v-else>
      <UTable
        :data="pageRows"
        :columns="columns"
        :loading="status === 'pending'"
        :empty="data.accounts.length ? 'Tidak ada akun yang cocok dengan filter.' : 'Belum ada akun sosial di Registry klien Eskala yang aktif.'"
      >
        <template #account-cell="{ row }">
          <div class="flex min-w-0 items-center gap-1.5">
            <UIcon
              :name="PLATFORM_ICONS[row.original.platform] ?? 'i-lucide-at-sign'"
              class="size-4 shrink-0 text-muted"
            />
            <ULink
              :to="row.original.url"
              target="_blank"
              class="min-w-32 break-all font-medium text-highlighted hover:underline"
            >
              @{{ row.original.handle }}
            </ULink>
          </div>
          <p class="mt-0.5 text-xs text-muted lg:hidden">
            {{ row.original.client.name }}
          </p>
          <div class="mt-1 flex flex-wrap items-center gap-1 sm:hidden">
            <UBadge
              :color="ROLE_LABEL[row.original.role]?.color ?? 'neutral'"
              variant="subtle"
              size="sm"
              :label="ROLE_LABEL[row.original.role]?.label ?? row.original.role"
            />
            <UBadge
              :color="harvestOutcome(row.original).color"
              variant="subtle"
              size="sm"
              :label="harvestOutcome(row.original).label"
            />
          </div>
          <p class="mt-0.5 text-xs text-muted sm:hidden">
            {{ row.original.last_harvested_at ? formatDate(row.original.last_harvested_at) : 'Belum pernah di-Harvest' }}
            <template v-if="row.original.posts_collected !== null">
              · {{ row.original.posts_collected }} post
            </template>
          </p>
        </template>
        <template #client-cell="{ row }">
          <NuxtLink
            :to="`/clients/${row.original.client.id}?tab=registry`"
            class="text-sm hover:underline"
          >
            {{ row.original.client.name }}
          </NuxtLink>
        </template>
        <template #role-cell="{ row }">
          <UBadge
            :color="ROLE_LABEL[row.original.role]?.color ?? 'neutral'"
            variant="subtle"
            :label="ROLE_LABEL[row.original.role]?.label ?? row.original.role"
          />
        </template>
        <template #last-cell="{ row }">
          <UBadge
            :color="harvestOutcome(row.original).color"
            variant="subtle"
            :label="harvestOutcome(row.original).label"
          />
          <p class="mt-1 text-xs text-muted">
            {{ row.original.last_harvested_at ? formatDate(row.original.last_harvested_at) : (row.original.first_harvest ? 'Harvest pertama: 90 hari' : '—') }}
          </p>
          <p
            v-if="row.original.last_reason"
            class="mt-0.5 text-xs text-muted wrap-break-word"
          >
            {{ row.original.last_reason }}
          </p>
        </template>
        <template #posts-cell="{ row }">
          {{ row.original.posts_collected ?? '—' }}
        </template>
        <template #actions-cell="{ row }">
          <div class="flex items-center justify-end gap-1">
            <UTooltip text="Jalankan sekarang">
              <UButton
                color="neutral"
                variant="ghost"
                icon="i-lucide-play"
                :loading="starting === `${row.original.id}-${row.original.client.id}`"
                :aria-label="`Jalankan Harvest @${row.original.handle} sekarang`"
                @click="runNow(row.original)"
              />
            </UTooltip>
            <!-- On phones the label would squeeze the handle into a narrow column; the icon stays. -->
            <UButton
              :to="`/automations/accounts/${row.original.id}`"
              color="neutral"
              variant="outline"
              size="sm"
              label="Tinjau post"
              class="hidden sm:inline-flex"
            />
            <UButton
              :to="`/automations/accounts/${row.original.id}`"
              color="neutral"
              variant="ghost"
              icon="i-lucide-list-checks"
              :aria-label="`Tinjau post @${row.original.handle}`"
              class="sm:hidden"
            />
          </div>
        </template>
      </UTable>
      <ListPager
        v-model:page="page"
        :total="total"
        :page-size="pageSize"
      />
    </template>
  </div>
</template>
