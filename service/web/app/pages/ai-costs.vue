<script setup lang="ts">
import type { TableColumn } from '@nuxt/ui'
import type { AiCostCase, AiCosts } from '~/types/hub'

/**
 * Biaya AI: what every AI call costs, per case (keperluan) and month, across the Hub, roach and
 * songbird (GET /v1/ai/costs). Owner and Brand Managers only; the models each case
 * uses come from shared/noktah_ai/models.yaml.
 */
const { data, error, status } = await useFetch<AiCosts>('/api/hub/v1/ai/costs')
useSeoMeta({ title: 'Biaya AI · Noktah Hub' })

const usd = (v: number) => new Intl.NumberFormat('id-ID', {
  style: 'currency', currency: 'USD', minimumFractionDigits: v > 0 && v < 1 ? 4 : 2, maximumFractionDigits: v > 0 && v < 1 ? 4 : 2
}).format(v)
const monthName = (key: string) => new Intl.DateTimeFormat('id-ID', { month: 'short', year: 'numeric', timeZone: 'UTC' })
  .format(new Date(`${key}-01T00:00:00Z`))

const months = computed(() => data.value?.months ?? [])
const thisMonth = computed(() => months.value.at(-1) ?? '')
const lastMonth = computed(() => months.value.at(-2) ?? '')
const total = (m: string) => data.value?.totals[m] ?? 0
const anySpend = computed(() => Object.values(data.value?.totals ?? {}).some(v => v > 0))
const capShare = computed(() => {
  const cap = data.value?.hub_cap
  return cap && cap.cap_usd ? Math.min(100, Math.round((cap.spent_usd / cap.cap_usd) * 100)) : 0
})

function bars(c: AiCostCase) {
  const max = Math.max(...months.value.map(m => c.months[m]?.cost_usd ?? 0))
  return months.value.map((m) => {
    const v = c.months[m]?.cost_usd ?? 0
    return { m, v, calls: c.months[m]?.calls ?? 0, pct: max ? Math.max(v ? 4 : 0, Math.round((v / max) * 100)) : 0 }
  })
}

// History: one row per month, newest first; one column per case, then the total.
type Row = { month: string } & Record<string, number | string>
const rows = computed<Row[]>(() => [...months.value].reverse().map(m => ({
  month: m,
  ...Object.fromEntries((data.value?.cases ?? []).map(c => [c.key, c.months[m]?.cost_usd ?? 0])),
  total: total(m)
})))
const columns = computed<TableColumn<Row>[]>(() => [
  { accessorKey: 'month', header: 'Bulan', cell: ({ row }) => monthName(row.original.month) },
  ...(data.value?.cases ?? []).map(c => ({
    accessorKey: c.key, header: c.label, cell: ({ row }: { row: { original: Row } }) => usd(Number(row.original[c.key] ?? 0)),
    meta: { class: { th: 'text-right', td: 'text-right tabular-nums' } }
  })),
  { accessorKey: 'total', header: 'Total', cell: ({ row }) => usd(Number(row.original.total)),
    meta: { class: { th: 'text-right', td: 'text-right tabular-nums font-semibold text-highlighted' } } }
])
</script>

<template>
  <UDashboardPanel id="ai-costs">
    <template #header>
      <UDashboardNavbar title="Biaya AI">
        <template #leading>
          <UDashboardSidebarCollapse />
        </template>
      </UDashboardNavbar>
    </template>

    <template #body>
      <UAlert
        v-if="error"
        color="error"
        icon="i-lucide-server-off"
        :title="hubError(error).message"
      />
      <div
        v-else-if="data"
        class="space-y-6"
      >
        <p class="max-w-3xl text-sm text-muted">
          Biaya setiap panggilan AI per keperluan dan per bulan, dari Hub, roach, dan songbird, dalam dolar AS sesuai tagihan OpenRouter. Model tiap keperluan diurutkan dari yang paling hemat untuk hasilnya. Bila model yang dipakai gagal 3 kali berturut-turut, model berikutnya yang dipakai.
        </p>

        <div class="grid gap-4 sm:grid-cols-3">
          <UCard>
            <p class="text-sm text-muted">
              Bulan ini ({{ monthName(thisMonth) }})
            </p>
            <p class="mt-1 text-2xl font-semibold text-highlighted tabular-nums">
              {{ usd(total(thisMonth)) }}
            </p>
          </UCard>
          <UCard>
            <p class="text-sm text-muted">
              Bulan lalu ({{ monthName(lastMonth) }})
            </p>
            <p class="mt-1 text-2xl font-semibold text-highlighted tabular-nums">
              {{ usd(total(lastMonth)) }}
            </p>
          </UCard>
          <UCard>
            <p class="text-sm text-muted">
              Batas AI Hub bulan ini
            </p>
            <p class="mt-1 text-2xl font-semibold text-highlighted tabular-nums">
              {{ usd(data.hub_cap.spent_usd) }} <span class="text-base font-normal text-muted">dari {{ usd(data.hub_cap.cap_usd) }}</span>
            </p>
            <UProgress
              :model-value="capShare"
              class="mt-3"
              :color="capShare >= 80 ? 'warning' : 'primary'"
            />
            <p class="mt-2 text-xs text-muted">
              Berlaku untuk Ringkasan dan Intake saja; bila batas tercapai, keduanya dijeda sampai bulan depan.
            </p>
          </UCard>
        </div>

        <UEmpty
          v-if="!anySpend"
          icon="i-lucide-receipt"
          title="Belum ada biaya AI"
          description="Biaya muncul di sini setelah Hub, roach, atau songbird memanggil AI."
        />

        <div class="grid gap-4 lg:grid-cols-2 2xl:grid-cols-3">
          <UCard
            v-for="c in data.cases"
            :key="c.key"
            :ui="{ body: 'space-y-4' }"
          >
            <template #header>
              <div class="flex flex-wrap items-center gap-2">
                <h3 class="font-medium text-highlighted">
                  {{ c.label }}
                </h3>
                <UBadge
                  :label="c.used_by"
                  color="neutral"
                  variant="subtle"
                />
              </div>
              <p class="mt-1 text-sm text-muted">
                {{ c.description }}
              </p>
            </template>

            <div class="flex flex-wrap items-baseline gap-x-3 gap-y-1">
              <span class="text-xl font-semibold text-highlighted tabular-nums">{{ usd(c.this_month) }}</span>
              <span class="text-sm text-muted tabular-nums">bulan ini · {{ c.months[thisMonth]?.calls ?? 0 }} panggilan</span>
              <span class="text-sm text-muted tabular-nums">bulan lalu {{ usd(c.last_month) }}</span>
            </div>

            <div>
              <div
                class="flex h-16 items-end gap-1"
                role="img"
                :aria-label="`Biaya ${c.label} 12 bulan terakhir`"
              >
                <div
                  v-for="b in bars(c)"
                  :key="b.m"
                  class="flex-1 rounded-t-sm"
                  :class="b.v ? 'bg-primary' : 'bg-elevated'"
                  :style="{ height: `${b.v ? b.pct : 6}%` }"
                  :title="`${monthName(b.m)}: ${usd(b.v)} · ${b.calls} panggilan`"
                />
              </div>
              <div class="mt-1 flex justify-between text-xs text-muted">
                <span>{{ monthName(months[0] ?? '') }}</span>
                <span>12 bulan: {{ usd(c.total) }}</span>
                <span>{{ monthName(thisMonth) }}</span>
              </div>
            </div>

            <div>
              <p class="mb-1 text-xs font-medium uppercase tracking-wide text-muted">
                Urutan model
              </p>
              <ol class="space-y-1 text-sm">
                <li
                  v-for="(m, i) in c.models"
                  :key="m"
                  class="flex flex-wrap items-center gap-2"
                >
                  <span class="w-4 text-muted tabular-nums">{{ i + 1 }}</span>
                  <span class="font-mono text-xs break-all">{{ m }}</span>
                  <UBadge
                    v-if="c.current === m"
                    label="Dipakai sekarang"
                    color="primary"
                    variant="subtle"
                    size="sm"
                  />
                </li>
              </ol>
              <p
                v-if="!c.current"
                class="mt-2 text-xs text-muted"
              >
                Model yang sedang dipakai hanya diketahui {{ c.used_by }} sendiri; biasanya yang pertama.
              </p>
            </div>
          </UCard>
        </div>

        <UCard :ui="{ body: 'p-0 sm:p-0' }">
          <template #header>
            <h3 class="font-medium text-highlighted">
              Riwayat per bulan
            </h3>
          </template>
          <UTable
            :data="rows"
            :columns="columns"
            :loading="status === 'pending'"
            class="w-full"
          />
        </UCard>
      </div>
    </template>
  </UDashboardPanel>
</template>
