<script setup lang="ts">
import type { NavigationMenuItem } from '@nuxt/ui'

const { data: me } = await useMe()
const { data: approvals } = await useFetch<unknown[]>('/api/hub/v1/approvals', {
  key: 'approvals-count',
  immediate: !!me.value?.can.approve,
  default: () => []
})

// Items a role can't use are hidden, not disabled (ui-screens.md).
const items = computed<NavigationMenuItem[]>(() => {
  const can = me.value?.can
  const list: NavigationMenuItem[] = [{ label: 'Klien', icon: 'i-lucide-building-2', to: '/' }]
  if (can?.approve) {
    list.push({ label: 'Persetujuan', icon: 'i-lucide-badge-check', to: '/approvals', badge: approvals.value?.length || undefined })
  }
  list.push({ label: 'Orang', icon: 'i-lucide-users', to: '/people' })
  if (can?.run_intake) list.push({ label: 'Catatan lama', icon: 'i-lucide-archive', to: '/notes' })
  return list
})

const roleLine = computed(() => {
  const r = me.value?.roles?.[0]
  if (!r) return ''
  const brand = r.noktah_brand ? ` · ${r.noktah_brand === 'eskala' ? 'Eskala' : 'Venyu'}` : ''
  return `${ROLE_LABELS[r.role] ?? r.role}${brand}`
})
</script>

<template>
  <UDashboardGroup unit="rem">
    <UDashboardSidebar
      collapsible
      resizable
      :ui="{ footer: 'border-t border-default' }"
    >
      <template #header="{ collapsed }">
        <NuxtLink
          to="/"
          class="flex items-center gap-2 font-semibold truncate"
        >
          <UIcon
            name="i-lucide-orbit"
            class="size-5 text-primary shrink-0"
          />
          <span v-if="!collapsed">Noktah Hub</span>
        </NuxtLink>
      </template>

      <template #default="{ collapsed }">
        <UNavigationMenu
          :collapsed="collapsed"
          :items="items"
          orientation="vertical"
          tooltip
        />
      </template>

      <template #footer="{ collapsed }">
        <div
          v-if="me"
          class="flex items-center gap-2 min-w-0"
        >
          <UAvatar
            :alt="me.person.display_name"
            size="sm"
          />
          <div
            v-if="!collapsed"
            class="min-w-0"
          >
            <p class="text-sm font-medium truncate">
              {{ me.person.display_name }}
            </p>
            <p class="text-xs text-muted truncate">
              {{ roleLine }}
            </p>
          </div>
        </div>
        <UColorModeButton
          v-if="!collapsed"
          class="ms-auto"
        />
      </template>
    </UDashboardSidebar>

    <slot />
  </UDashboardGroup>
</template>
