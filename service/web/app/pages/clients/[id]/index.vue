<script setup lang="ts">
import type { TabsItem } from '@nuxt/ui'
import type { Card, ClientRecord } from '~/types/hub'

/** One Client: its Client Card (four parts) and its Registry record. */
const route = useRoute()
const router = useRouter()
const id = computed(() => String(route.params.id))
const { data: me } = await useMe()
const { data: definition } = await useCardDefinition()
const { data: client, error: clientError } = await useFetch<ClientRecord>(() => `/api/hub/v1/clients/${id.value}`)
const { data: card, error: cardError, refresh: refreshCard } = await useFetch<Card>(() => `/api/hub/v1/clients/${id.value}/card`)

const TABS = ['ringkasan', 'profil', 'guideline', 'permintaan', 'registry', 'riwayat'] as const
const tab = computed({
  get: () => (TABS as readonly string[]).includes(String(route.query.tab)) ? String(route.query.tab) : 'ringkasan',
  set: (value: string) => router.replace({ query: { ...route.query, tab: value } })
})
const items = computed<TabsItem[]>(() => [
  { label: 'Ringkasan', value: 'ringkasan', icon: 'i-lucide-sparkles' },
  { label: 'Profil', value: 'profil', icon: 'i-lucide-id-card', badge: card.value?.completeness.profil.missing.length ? '!' : undefined },
  { label: 'Guideline', value: 'guideline', icon: 'i-lucide-palette', badge: card.value?.pending.length || undefined },
  { label: 'Permintaan', value: 'permintaan', icon: 'i-lucide-inbox', badge: card.value?.requests_open || undefined },
  { label: 'Registry', value: 'registry', icon: 'i-lucide-settings-2' },
  { label: 'Riwayat', value: 'riwayat', icon: 'i-lucide-history' }
])

const STATUS_LABEL: Record<string, string> = { active: 'Aktif', pending: 'Menunggu', inactive: 'Tidak aktif' }
const STATUS_COLOR: Record<string, 'success' | 'warning' | 'neutral'> = { active: 'success', pending: 'warning', inactive: 'neutral' }
const error = computed(() => clientError.value ?? cardError.value)
useSeoMeta({ title: () => (client.value ? `${client.value.name} · Noktah Hub` : 'Noktah Hub') })
</script>

<template>
  <UDashboardPanel id="client">
    <template #header>
      <UDashboardNavbar :title="client?.name ?? 'Klien'">
        <template #leading>
          <UDashboardSidebarCollapse />
        </template>
        <template #right>
          <UButton
            v-if="client && me?.can.run_intake"
            :to="`/clients/${id}/intake`"
            icon="i-lucide-clipboard-paste"
            label="Intake"
          />
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
        v-else-if="client && card && definition"
        class="space-y-4"
      >
        <div class="flex flex-wrap items-center gap-2">
          <UBadge
            :color="STATUS_COLOR[client.status]"
            variant="subtle"
            :label="STATUS_LABEL[client.status]"
          />
          <UBadge
            v-if="client.brand_name"
            color="neutral"
            variant="outline"
            :label="client.brand_name"
          />
          <UBadge
            v-if="client.is_internal"
            color="neutral"
            variant="subtle"
            label="Internal"
          />
          <span class="text-sm text-muted">
            Profil {{ card.completeness.profil.filled }}/{{ card.completeness.profil.required }} · Guideline {{ card.completeness.guideline.filled }}/{{ card.completeness.guideline.total }}
          </span>
        </div>

        <UTabs
          v-model="tab"
          :items="items"
          :content="false"
          variant="link"
          class="w-full overflow-x-auto items-start"
          :ui="{ list: 'w-max min-w-full', trigger: 'shrink-0' }"
        />

        <ClientSummaryTab
          v-if="tab === 'ringkasan'"
          :card="card"
          :definition="definition"
        />
        <ClientCardPartTab
          v-else-if="tab === 'profil'"
          part="profil"
          :client="client"
          :card="card"
          :definition="definition"
          @changed="refreshCard"
        />
        <ClientCardPartTab
          v-else-if="tab === 'guideline'"
          part="guideline"
          :client="client"
          :card="card"
          :definition="definition"
          @changed="refreshCard"
        />
        <ClientRequestsTab
          v-else-if="tab === 'permintaan'"
          :client-id="id"
        />
        <ClientRegistryTab
          v-else-if="tab === 'registry'"
          :client="client"
          @updated="record => (client = record)"
        />
        <ClientHistoryTab
          v-else
          :key="client.version"
          :client-id="id"
        />
      </div>
    </template>
  </UDashboardPanel>
</template>
