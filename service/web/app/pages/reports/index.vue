<script setup lang="ts">
import type { TabsItem } from '@nuxt/ui'

/**
 * Laporan (spec 009 US5–US7): Delivery, Stations and Performa for Eskala, by month, from the
 * Hub's copy of Jira (refreshed every 15 minutes) and the Harvest. Needs "Lihat laporan";
 * points and sanctions are on /reports/incentive behind "Lihat poin insentif" (FR-004).
 */
const route = useRoute()
const router = useRouter()
const { data: me, error: meError } = await useMe()
useSeoMeta({ title: 'Laporan · Noktah Hub' })

const TABS = ['delivery', 'stations', 'performa'] as const
const tab = computed({
  get: () => (TABS as readonly string[]).includes(String(route.query.tab)) ? String(route.query.tab) : 'delivery',
  set: (value: string) => router.replace({ query: { ...route.query, tab: value } })
})
const items: TabsItem[] = [
  { label: 'Delivery', value: 'delivery', icon: 'i-lucide-calendar-check' },
  { label: 'Stations', value: 'stations', icon: 'i-lucide-route' },
  { label: 'Performa', value: 'performa', icon: 'i-lucide-chart-line' }
]
// Last month by default: the month a manager reviews.
const month = computed({
  get: () => monthFromQuery(route.query.month, monthKey(-1)),
  set: (value: string) => router.replace({ query: { ...route.query, month: value } })
})
</script>

<template>
  <UDashboardPanel id="reports">
    <template #header>
      <UDashboardNavbar title="Laporan">
        <template #leading>
          <UDashboardSidebarCollapse />
        </template>
        <template #right>
          <UButton
            v-if="me?.can.view_incentive"
            :to="`/reports/incentive?month=${month}`"
            color="neutral"
            variant="outline"
            icon="i-lucide-scale"
            label="Insentif"
          />
        </template>
      </UDashboardNavbar>
    </template>

    <template #body>
      <UAlert
        v-if="meError"
        color="error"
        icon="i-lucide-server-off"
        :title="hubError(meError).message"
      />
      <UAlert
        v-else-if="!me?.can.view_reports"
        color="neutral"
        variant="subtle"
        icon="i-lucide-lock"
        title="Laporan ini butuh izin Lihat laporan."
        :description="me?.can.view_incentive ? 'Poin dan sanksi Incentive Framework ada di halaman Insentif.' : undefined"
      />
      <div
        v-else
        class="space-y-4"
      >
        <div class="flex flex-col gap-2 sm:flex-row sm:items-center">
          <MonthSelect
            v-model="month"
            :to="monthKey(0)"
          />
        </div>
        <UTabs
          v-model="tab"
          :items="items"
          :content="false"
          variant="link"
          class="w-full overflow-x-auto items-start"
          :ui="{ list: 'w-max min-w-full', trigger: 'shrink-0' }"
        />
        <ReportDeliveryTab
          v-if="tab === 'delivery'"
          :month="month"
        />
        <ReportStationsTab
          v-else-if="tab === 'stations'"
          :month="month"
        />
        <ReportPerformanceTab
          v-else
          :month="month"
        />
      </div>
    </template>
  </UDashboardPanel>
</template>
