<script setup lang="ts">
import type { TableColumn } from '@nuxt/ui'
import type { ClientRef, DeliveryCounts, DeliveryReport, LateItem } from '~/types/hub'

/**
 * Laporan → Delivery (US5, FR-051): per Client, content planned, created, Published, Late,
 * published late, cancelled and On Hold for the month, by publication date. Shelved content is
 * cancelled and On Hold is a manager's pause; neither is ever Late (G-4, G-52).
 */
const props = defineProps<{ month: string }>()
const { data, error, status } = await useFetch<DeliveryReport>('/api/hub/v1/reports/delivery', {
  query: { month: toRef(props, 'month') }
})

type Row = DeliveryCounts & { client: ClientRef | null, late_items: LateItem[] }
const rows = computed<Row[]>(() => data.value?.clients.length
  ? [...data.value.clients, { ...data.value.totals, client: null, late_items: [] }]
  : [])
const num = (label: string, key: keyof DeliveryCounts, show = 'sm'): TableColumn<Row> => ({
  accessorKey: key, header: label,
  meta: { class: { th: `hidden ${show}:table-cell text-right whitespace-nowrap`, td: `hidden ${show}:table-cell text-right tabular-nums` } }
})
const columns: TableColumn<Row>[] = [
  { id: 'client', header: 'Klien', meta: { class: { td: 'whitespace-normal' } } },
  num('Direncanakan', 'planned', 'lg'),
  num('Issue dibuat', 'created', 'lg'),
  num('Published', 'published'),
  { accessorKey: 'late', header: 'Late', meta: { class: { th: 'text-right', td: 'text-right tabular-nums' } } },
  num('Published late', 'published_late', 'md'),
  num('Dibatalkan', 'cancelled', 'md'),
  num('On Hold', 'on_hold', 'md')
]

const where = (i: LateItem) => (i.station ? (i.station_label ? `${i.station} · ${i.station_label}` : stationName(i.station)) : '—')
const late = computed(() => (data.value?.clients ?? []).flatMap(c => c.late_items.map(i => ({ ...i, client: c.client.name })))
  .sort((a, b) => b.days_late - a.days_late))
const { page, pageRows, pageSize, total } = usePaged(late)
const lateColumns: TableColumn<LateItem & { client: string }>[] = [
  { accessorKey: 'key', header: 'Issue', meta: { class: { th: 'w-px', td: 'w-px whitespace-nowrap' } } },
  { accessorKey: 'topik', header: 'Topik', meta: { class: { td: 'whitespace-normal' } } },
  { accessorKey: 'publication_date', header: 'Tanggal publikasi', meta: { class: { th: 'hidden md:table-cell', td: 'hidden md:table-cell whitespace-nowrap' } } },
  { accessorKey: 'status', header: 'Status', meta: { class: { th: 'hidden lg:table-cell', td: 'hidden lg:table-cell whitespace-normal' } } },
  { accessorKey: 'station', header: 'Station', meta: { class: { th: 'hidden sm:table-cell', td: 'hidden sm:table-cell whitespace-normal' } } },
  { accessorKey: 'days_late', header: 'Terlambat', meta: { class: { th: 'hidden sm:table-cell text-right', td: 'hidden sm:table-cell text-right tabular-nums whitespace-nowrap' } } }
]
</script>

<template>
  <div class="space-y-4">
    <UAlert
      v-if="error"
      color="error"
      icon="i-lucide-server-off"
      :title="hubError(error).message"
    />
    <template v-else-if="data">
      <p class="flex items-center gap-1.5 text-sm text-muted">
        <UIcon
          name="i-lucide-history"
          class="size-4 shrink-0"
        />
        {{ refreshedLine(data.refreshed_at) }}
      </p>
      <p class="max-w-3xl text-sm text-muted">
        Dihitung dari tanggal publikasi di Jira. Published berarti sudah sampai Published, Need Review atau Done. Late berarti tanggal publikasinya lewat dan belum Published; konten yang di-shelve dihitung dibatalkan, bukan Late.
      </p>
      <UCard :ui="{ body: 'p-0 sm:p-0' }">
        <UTable
          :data="rows"
          :columns="columns"
          :loading="status === 'pending'"
          :empty="`Belum ada konten Eskala dengan tanggal publikasi di ${monthLabel(month)}.`"
        >
          <template #client-cell="{ row }">
            <template v-if="row.original.client">
              <span class="font-medium text-highlighted">{{ row.original.client.name }}</span>
              <p class="mt-0.5 text-xs text-muted tabular-nums lg:hidden">
                {{ row.original.planned ?? '—' }} direncanakan · {{ row.original.created }} issue dibuat
                <span class="md:hidden"> · {{ row.original.published_late }} published late · {{ row.original.cancelled }} dibatalkan · {{ row.original.on_hold }} on hold</span>
                <span class="sm:hidden"> · {{ row.original.published }} published</span>
              </p>
            </template>
            <span
              v-else
              class="font-semibold text-highlighted"
            >Total</span>
          </template>
          <template #planned-cell="{ row }">
            {{ row.original.planned ?? '—' }}
          </template>
          <template #late-cell="{ row }">
            <span :class="row.original.late ? 'font-semibold text-error' : (row.original.client ? '' : 'font-semibold text-highlighted')">{{ row.original.late }}</span>
          </template>
        </UTable>
      </UCard>

      <UCard
        v-if="late.length"
        :ui="{ body: 'p-0 sm:p-0' }"
      >
        <template #header>
          <h3 class="font-medium text-highlighted">
            Konten Late
          </h3>
          <p class="mt-1 text-sm text-muted">
            {{ late.length }} konten, yang paling lama terlambat di atas.
          </p>
        </template>
        <UTable
          :data="pageRows"
          :columns="lateColumns"
        >
          <template #key-cell="{ row }">
            <ULink
              :to="`${JIRA_BROWSE}${row.original.key}`"
              target="_blank"
              class="text-sm font-medium text-primary hover:underline"
            >
              {{ row.original.key }}
            </ULink>
          </template>
          <template #topik-cell="{ row }">
            <span class="block min-w-36">{{ row.original.topik ?? '—' }}</span>
            <span class="mt-0.5 block text-xs text-muted">{{ row.original.client }}</span>
            <span class="mt-0.5 block text-xs text-muted lg:hidden">
              <span class="md:hidden">{{ formatDate(row.original.publication_date) }} · </span>{{ row.original.status }}<span class="sm:hidden"> · {{ where(row.original) }} · {{ row.original.days_late }} hari terlambat</span>
            </span>
          </template>
          <template #publication_date-cell="{ row }">
            {{ formatDate(row.original.publication_date) }}
          </template>
          <template #status-cell="{ row }">
            <span class="block min-w-24">{{ row.original.status }}</span>
          </template>
          <template #station-cell="{ row }">
            <span class="block min-w-20">{{ where(row.original) }}</span>
          </template>
          <template #days_late-cell="{ row }">
            {{ row.original.days_late }} hari
          </template>
        </UTable>
        <div class="px-4 pb-3 sm:px-6">
          <ListPager
            v-model:page="page"
            :total="total"
            :page-size="pageSize"
          />
        </div>
      </UCard>
    </template>
  </div>
</template>
