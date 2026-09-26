<script setup lang="ts">
import type { TabsItem } from '@nuxt/ui'
import type { Automation } from '~/types/hub'

/**
 * Otomasi (spec 009 US1–US3): one card per automation (FR-040), then the Jira tab (Content
 * Plans, Greenlight, "Buat issue Jira") and the Harvest tab (accounts from the Registry).
 * Eskala only; needs "Kelola otomasi".
 */
const route = useRoute()
const router = useRouter()
const { data: me, error: meError } = await useMe()
const { data, error } = await useFetch<{ automations: Automation[] }>('/api/hub/v1/automations', {
  default: () => ({ automations: [] })
})
useSeoMeta({ title: 'Otomasi · Noktah Hub' })

const TABS = ['jira', 'harvest'] as const
const tab = computed({
  get: () => (TABS as readonly string[]).includes(String(route.query.tab)) ? String(route.query.tab) : 'jira',
  set: (value: string) => router.replace({ query: { ...route.query, tab: value } })
})
const items: TabsItem[] = [
  { label: 'Jira', value: 'jira', icon: 'i-simple-icons-jira' },
  { label: 'Harvest', value: 'harvest', icon: 'i-lucide-sprout' }
]
// The Content Plans' month: next month by default (plans are made ahead), kept in ?month=.
const month = computed({
  get: () => monthFromQuery(route.query.month, monthKey(1)),
  set: (value: string) => router.replace({ query: { ...route.query, month: value } })
})
</script>

<template>
  <UDashboardPanel id="automations">
    <template #header>
      <UDashboardNavbar title="Otomasi">
        <template #leading>
          <UDashboardSidebarCollapse />
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
        v-else-if="!me?.can.manage_automation"
        color="neutral"
        variant="subtle"
        icon="i-lucide-lock"
        title="Halaman ini butuh izin Kelola otomasi."
      />
      <UAlert
        v-else-if="error"
        color="error"
        icon="i-lucide-server-off"
        :title="hubError(error).message"
      />
      <div
        v-else
        class="space-y-6"
      >
        <p class="max-w-3xl text-sm text-muted">
          Otomasi Eskala: issue Jira dibuat dari Content Plan yang sudah di-Greenlight, dan Harvest mengumpulkan post akun sosial klien dan pesaingnya sesuai Registry.
        </p>
        <div class="grid gap-4 lg:grid-cols-2">
          <AutomationRunCard
            v-for="a in data.automations"
            :key="a.key"
            :automation="a"
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
        <AutomationJiraTab
          v-if="tab === 'jira'"
          v-model:month="month"
        />
        <AutomationHarvestTab v-else />
      </div>
    </template>
  </UDashboardPanel>
</template>
