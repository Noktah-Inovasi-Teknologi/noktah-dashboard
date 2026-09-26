<script setup lang="ts">
import type { TableColumn } from '@nuxt/ui'
import type { StationPerson, StationReturn, StationsReport, StationTeam } from '~/types/hub'

/**
 * Laporan → Stations (US6, FR-052…FR-057): per Station, per person and per Client team, content
 * in and moved forward, Returns by origin Station, time held, what still waits. A Return is
 * counted, never charged; only a judged Event charges (G-15). Round flags are for review.
 */
const props = defineProps<{ month: string }>()
const { data, error, status } = await useFetch<StationsReport>('/api/hub/v1/reports/stations', {
  query: { month: toRef(props, 'month') }
})

const origins = (by: Record<string, number>) => Object.entries(by)
  .sort(([a], [b]) => (a === 'tanpa_kategori' ? 1 : b === 'tanpa_kategori' ? -1 : a.localeCompare(b)))
  .map(([k, n]) => ({ k, n, label: k === 'tanpa_kategori' ? 'tanpa kategori' : `dari ${k}` }))

// ── people ──────────────────────────────────────────────────────────────────────────
const people = computed(() => data.value?.people ?? [])
const peoplePaged = usePaged(people)
const peopleColumns: TableColumn<StationPerson>[] = [
  { id: 'name', header: 'Orang', meta: { class: { td: 'whitespace-normal' } } },
  { accessorKey: 'station', header: 'Station', meta: { class: { th: 'hidden sm:table-cell', td: 'hidden sm:table-cell whitespace-normal' } } },
  { accessorKey: 'in', header: 'Masuk', meta: { class: { th: 'text-right', td: 'text-right tabular-nums' } } },
  { accessorKey: 'out', header: 'Keluar', meta: { class: { th: 'text-right', td: 'text-right tabular-nums' } } },
  { accessorKey: 'returns_against', header: 'Return', meta: { class: { th: 'hidden md:table-cell text-right', td: 'hidden md:table-cell text-right tabular-nums' } } },
  { id: 'held', header: 'Lama di Station (median / terlama)', meta: { class: { th: 'hidden lg:table-cell', td: 'hidden lg:table-cell whitespace-nowrap tabular-nums' } } },
  { accessorKey: 'waiting', header: 'Menunggu', meta: { class: { th: 'hidden md:table-cell text-right', td: 'hidden md:table-cell text-right tabular-nums' } } }
]

// ── teams ───────────────────────────────────────────────────────────────────────────
const teams = computed(() => data.value?.teams ?? [])
const teamsPaged = usePaged(teams)
const teamColumns: TableColumn<StationTeam>[] = [
  { id: 'client', header: 'Tim klien', meta: { class: { td: 'whitespace-normal' } } },
  { id: 'first_pass', header: 'First pass klien', meta: { class: { th: 'hidden sm:table-cell', td: 'hidden sm:table-cell whitespace-normal' } } },
  { id: 'qa_rounds', header: 'Putaran QA', meta: { class: { th: 'hidden sm:table-cell', td: 'hidden sm:table-cell whitespace-normal' } } },
  { accessorKey: 'returns', header: 'Return', meta: { class: { th: 'hidden md:table-cell text-right', td: 'hidden md:table-cell text-right tabular-nums' } } },
  { id: 'both', header: 'Ambang', meta: { class: { th: 'w-px', td: 'w-px' } } }
]
const firstPass = (t: StationTeam) => t.first_pass.rate === null
  ? 'Belum ada konten yang ditinjau klien'
  : `${formatPercent(t.first_pass.rate, 0)} dari ${t.first_pass.counted} konten`
const qaRounds = (t: StationTeam) => t.qa_rounds.average === null
  ? 'Belum ada konten sampai QA'
  : `${formatCount(t.qa_rounds.average, 1)} rata-rata dari ${t.qa_rounds.counted} konten`

// ── returns ─────────────────────────────────────────────────────────────────────────
// A Return with no Defect Category is shown under the Station that sent it back, charged to no one (G-35).
const originText = (r: StationReturn) => (r.origin
  ? stationName(r.origin)
  : `tanpa kategori${r.sent_back_by ? `, dikembalikan dari ${stationName(r.sent_back_by)}` : ''}`)
const returns = computed(() => data.value?.returns ?? [])
const returnsPaged = usePaged(returns)
const returnColumns: TableColumn<StationReturn>[] = [
  { accessorKey: 'key', header: 'Issue', meta: { class: { th: 'w-px', td: 'w-px whitespace-nowrap' } } },
  { id: 'move', header: 'Dari → ke', meta: { class: { td: 'whitespace-normal' } } },
  { id: 'origin', header: 'Asal kesalahan', meta: { class: { th: 'hidden md:table-cell', td: 'hidden md:table-cell whitespace-normal' } } },
  { accessorKey: 'reason', header: 'Return Reason', meta: { class: { th: 'hidden lg:table-cell', td: 'hidden lg:table-cell whitespace-normal' } } }
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
        Station dibaca dari status issue di Jira. Return dihitung terhadap Station asal kesalahannya (Defect Category); Return tanpa kategori tidak dihitung ke siapa pun. Laporan ini tidak membebankan apa pun ke orang: hanya Event yang dinilai yang memberi poin.
      </p>

      <UAlert
        v-if="data.unknown_statuses.length"
        color="neutral"
        variant="subtle"
        icon="i-lucide-circle-help"
        title="Status Jira yang belum punya Station"
        :description="data.unknown_statuses.join(', ')"
      />

      <UEmpty
        v-if="!data.stations.length && status !== 'pending'"
        icon="i-lucide-chart-column"
        :title="`Belum ada perpindahan konten di ${monthLabel(month)}`"
        description="Angka muncul setelah issue Jira bulan ini bergerak antar-Station."
      />
      <div
        v-else
        class="grid gap-4 md:grid-cols-2 2xl:grid-cols-3"
      >
        <UCard
          v-for="s in data.stations"
          :key="s.station"
          :ui="{ body: 'space-y-3' }"
        >
          <template #header>
            <h3 class="font-medium text-highlighted">
              {{ s.station }} · {{ s.label }}
            </h3>
            <p class="text-sm text-muted">
              {{ s.role }}
            </p>
          </template>
          <dl class="grid grid-cols-2 gap-x-4 gap-y-2 text-sm sm:grid-cols-3 md:grid-cols-2 xl:grid-cols-3">
            <div>
              <dt class="text-muted">
                Masuk
              </dt>
              <dd class="text-lg font-semibold text-highlighted tabular-nums">
                {{ s.in }}
              </dd>
            </div>
            <div>
              <dt class="text-muted">
                Keluar
              </dt>
              <dd class="text-lg font-semibold text-highlighted tabular-nums">
                {{ s.out }}
              </dd>
            </div>
            <div>
              <dt class="text-muted">
                Return
              </dt>
              <dd class="text-lg font-semibold text-highlighted tabular-nums">
                {{ s.returns.total }}
              </dd>
            </div>
            <div>
              <dt class="text-muted">
                Lama (median)
              </dt>
              <dd class="text-highlighted tabular-nums">
                {{ formatHours(s.held_hours.median) }}
              </dd>
            </div>
            <div>
              <dt class="text-muted">
                Terlama
              </dt>
              <dd class="text-highlighted tabular-nums">
                {{ formatHours(s.held_hours.longest) }}
              </dd>
            </div>
            <div>
              <dt class="text-muted">
                Menunggu
              </dt>
              <dd class="text-highlighted tabular-nums">
                {{ s.waiting.length }}
              </dd>
            </div>
          </dl>
          <div
            v-if="s.returns.total"
            class="flex flex-wrap gap-1"
          >
            <UBadge
              v-for="o in origins(s.returns.by_origin)"
              :key="o.k"
              color="neutral"
              variant="subtle"
              size="sm"
              :label="`${o.n} ${o.label}`"
            />
          </div>
          <div v-if="s.waiting.length">
            <p class="mb-1 text-xs font-medium uppercase tracking-wide text-muted">
              Masih menunggu
            </p>
            <ul class="space-y-1">
              <li
                v-for="w in s.waiting.slice(0, 5)"
                :key="w.key"
                class="flex flex-wrap items-baseline gap-x-2 text-sm"
              >
                <ULink
                  :to="`${JIRA_BROWSE}${w.key}`"
                  target="_blank"
                  class="font-medium text-primary hover:underline"
                >
                  {{ w.key }}
                </ULink>
                <span class="min-w-0 flex-1 text-muted wrap-break-word">{{ w.client }}</span>
                <span class="tabular-nums">{{ formatHours(w.hours) }}</span>
              </li>
            </ul>
            <p
              v-if="s.waiting.length > 5"
              class="mt-1 text-xs text-muted"
            >
              dan {{ s.waiting.length - 5 }} lainnya
            </p>
          </div>
        </UCard>
      </div>

      <UAlert
        v-if="data.round_flags.length"
        color="warning"
        variant="subtle"
        icon="i-lucide-repeat"
        title="Lebih dari 3 putaran"
      >
        <template #description>
          <p class="mb-1">
            Konten ini bolak-balik lebih dari 3 kali antara dua Station. Perlu ditinjau manajer; tidak membebankan siapa pun.
          </p>
          <ul class="space-y-1">
            <li
              v-for="f in data.round_flags"
              :key="f.key"
            >
              <ULink
                :to="`${JIRA_BROWSE}${f.key}`"
                target="_blank"
                class="font-medium underline"
              >
                {{ f.key }}
              </ULink>
              · {{ f.client }} · {{ f.stations.join(' ↔ ') }} · {{ f.rounds }} putaran
            </li>
          </ul>
        </template>
      </UAlert>

      <UCard
        v-if="teams.length"
        :ui="{ body: 'p-0 sm:p-0' }"
      >
        <template #header>
          <h3 class="font-medium text-highlighted">
            Tim klien
          </h3>
          <p class="mt-1 text-sm text-muted">
            Ambang Incentive Framework: first pass klien minimal 60%, putaran QA paling banyak 1,5.
          </p>
        </template>
        <UTable
          :data="teamsPaged.pageRows.value"
          :columns="teamColumns"
        >
          <template #client-cell="{ row }">
            <span class="font-medium text-highlighted">{{ row.original.client.name }}</span>
            <p class="mt-0.5 text-xs text-muted sm:hidden">
              First pass {{ firstPass(row.original) }} · QA {{ qaRounds(row.original) }}
            </p>
          </template>
          <template #first_pass-cell="{ row }">
            <span
              class="block"
              :class="row.original.first_pass.rate !== null && row.original.first_pass.rate < row.original.first_pass.target ? 'text-error' : ''"
            >{{ firstPass(row.original) }}</span>
            <span class="block text-xs text-muted">target ≥ {{ formatPercent(row.original.first_pass.target, 0) }}</span>
          </template>
          <template #qa_rounds-cell="{ row }">
            <span
              class="block"
              :class="row.original.qa_rounds.average !== null && row.original.qa_rounds.average > row.original.qa_rounds.target ? 'text-error' : ''"
            >{{ qaRounds(row.original) }}</span>
            <span class="block text-xs text-muted">target ≤ {{ formatCount(row.original.qa_rounds.target, 1) }}</span>
          </template>
          <template #both-cell="{ row }">
            <UBadge
              :color="row.original.both_met ? 'success' : 'neutral'"
              variant="subtle"
              :label="row.original.both_met ? 'Kedua ambang tercapai' : 'Belum tercapai'"
            />
          </template>
        </UTable>
        <div class="px-4 pb-3 sm:px-6">
          <ListPager
            v-model:page="teamsPaged.page.value"
            :total="teamsPaged.total.value"
            :page-size="teamsPaged.pageSize"
          />
        </div>
      </UCard>

      <UCard
        v-if="people.length"
        :ui="{ body: 'p-0 sm:p-0' }"
      >
        <template #header>
          <h3 class="font-medium text-highlighted">
            Per orang
          </h3>
          <p class="mt-1 text-sm text-muted">
            Field Associate dan Content Editor dari isian issue; Content Planner dan Quality Assurance dari tim klien.
          </p>
        </template>
        <UTable
          :data="peoplePaged.pageRows.value"
          :columns="peopleColumns"
        >
          <template #name-cell="{ row }">
            <NuxtLink
              v-if="row.original.person.id"
              :to="`/people/${row.original.person.id}`"
              class="font-medium text-highlighted hover:underline"
            >
              {{ row.original.person.name }}
            </NuxtLink>
            <span
              v-else
              class="text-muted"
            >{{ row.original.person.name }}<template v-if="row.original.person.account_id"> ({{ row.original.person.account_id }})</template></span>
            <p class="mt-0.5 text-xs text-muted sm:hidden">
              {{ stationName(row.original.station) }}
            </p>
          </template>
          <template #station-cell="{ row }">
            <span class="block min-w-20">{{ stationName(row.original.station) }}</span>
          </template>
          <template #held-cell="{ row }">
            {{ formatHours(row.original.held_hours.median) }} / {{ formatHours(row.original.held_hours.longest) }}
          </template>
        </UTable>
        <div class="px-4 pb-3 sm:px-6">
          <ListPager
            v-model:page="peoplePaged.page.value"
            :total="peoplePaged.total.value"
            :page-size="peoplePaged.pageSize"
          />
        </div>
      </UCard>

      <UCard
        v-if="returns.length"
        :ui="{ body: 'p-0 sm:p-0' }"
      >
        <template #header>
          <h3 class="font-medium text-highlighted">
            Return
          </h3>
          <p class="mt-1 text-sm text-muted">
            {{ returns.length }} konten dikembalikan ke Station sebelumnya bulan ini.
          </p>
        </template>
        <UTable
          :data="returnsPaged.pageRows.value"
          :columns="returnColumns"
        >
          <template #key-cell="{ row }">
            <ULink
              :to="`${JIRA_BROWSE}${row.original.key}`"
              target="_blank"
              class="text-sm font-medium text-primary hover:underline"
            >
              {{ row.original.key }}
            </ULink>
            <span class="block text-xs text-muted">{{ formatDate(row.original.at) }}</span>
          </template>
          <template #move-cell="{ row }">
            <span class="block min-w-36">{{ row.original.from_status }} → {{ row.original.to_status }}</span>
            <span
              v-if="row.original.client"
              class="mt-0.5 block text-xs text-muted"
            >{{ row.original.client }}</span>
            <span class="mt-0.5 block text-xs text-muted md:hidden">Asal: {{ originText(row.original) }}</span>
            <span
              v-if="row.original.reason"
              class="mt-0.5 block text-xs text-muted lg:hidden"
            >{{ row.original.reason }}</span>
          </template>
          <template #origin-cell="{ row }">
            <span class="block">{{ originText(row.original) }}</span>
            <span
              v-if="row.original.defect"
              class="block text-xs text-muted"
            >{{ row.original.defect }}</span>
          </template>
          <template #reason-cell="{ row }">
            <span class="block min-w-40">{{ row.original.reason ?? '—' }}</span>
          </template>
        </UTable>
        <div class="px-4 pb-3 sm:px-6">
          <ListPager
            v-model:page="returnsPaged.page.value"
            :total="returnsPaged.total.value"
            :page-size="returnsPaged.pageSize"
          />
        </div>
      </UCard>
    </template>
  </div>
</template>
