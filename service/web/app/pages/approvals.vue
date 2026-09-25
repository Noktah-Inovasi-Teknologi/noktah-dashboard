<script setup lang="ts">
import type { CardDefinition } from '~/types/hub'

/** Persetujuan: Guideline changes waiting for the Brand Manager (or the Owner, G-12). */
interface Approval {
  id: string
  client: { id: string, name: string }
  part: 'profil' | 'guideline'
  field_key: string
  current_value: unknown
  proposed_value: unknown
  set_by: string
  set_at: string
}

const toast = useToast()
const { data: definition } = await useCardDefinition()
const { data: items, error, refresh } = await useFetch<Approval[]>('/api/hub/v1/approvals', { default: () => [] })
const spec = (a: Approval) => fieldSpec(definition.value as CardDefinition | null, a.part, a.field_key)

const rejecting = ref<string | null>(null)
const reason = ref('')
async function decide(id: string, action: 'approve' | 'reject') {
  try {
    await $fetch(`/api/hub/v1/approvals/${id}/${action}`, { method: 'POST', body: action === 'reject' ? { reason: reason.value } : {} })
    toast.add({ color: 'success', title: action === 'approve' ? 'Disetujui' : 'Ditolak' })
    rejecting.value = null
    reason.value = ''
    await refresh()
    await refreshNuxtData('approvals-count')
  } catch (err) {
    toast.add({ color: 'error', title: 'Gagal', description: hubError(err).message })
  }
}
</script>

<template>
  <UDashboardPanel id="approvals">
    <template #header>
      <UDashboardNavbar title="Persetujuan">
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
      <UEmpty
        v-else-if="!items.length"
        icon="i-lucide-badge-check"
        title="Tidak ada yang menunggu"
        description="Semua perubahan Guideline sudah diputuskan."
      />
      <ul
        v-else
        class="space-y-4"
      >
        <li
          v-for="a in items"
          :key="a.id"
        >
          <UCard :ui="{ body: 'space-y-3' }">
            <div class="flex flex-wrap items-center gap-2">
              <NuxtLink
                :to="`/clients/${a.client.id}?tab=guideline`"
                class="font-medium text-highlighted hover:underline"
              >
                {{ a.client.name }}
              </NuxtLink>
              <span class="text-muted">·</span>
              <span class="font-medium">{{ spec(a)?.label ?? a.field_key }}</span>
              <span class="text-xs text-muted ms-auto">Diusulkan {{ a.set_by }}, {{ formatDate(a.set_at) }}</span>
            </div>
            <div
              v-if="spec(a)"
              class="grid gap-4 md:grid-cols-2"
            >
              <div class="min-w-0">
                <p class="text-xs text-muted mb-1">
                  Sekarang
                </p>
                <CardValueView
                  :shape="spec(a)!.shape"
                  :value="a.current_value"
                  :subfields="spec(a)!.subfields"
                  :definition="definition"
                />
              </div>
              <div class="min-w-0">
                <p class="text-xs text-muted mb-1">
                  Usulan
                </p>
                <CardValueView
                  :shape="spec(a)!.shape"
                  :value="a.proposed_value"
                  :subfields="spec(a)!.subfields"
                  :definition="definition"
                />
              </div>
            </div>
            <div class="flex gap-2">
              <UButton
                icon="i-lucide-check"
                label="Setujui"
                @click="decide(a.id, 'approve')"
              />
              <UButton
                color="neutral"
                variant="outline"
                label="Tolak"
                @click="rejecting = a.id"
              />
            </div>
          </UCard>
        </li>
      </ul>

      <UModal
        :open="!!rejecting"
        title="Tolak perubahan"
        description="Alasan akan tercatat di riwayat."
        @update:open="o => { if (!o) rejecting = null }"
      >
        <template #body>
          <UFormField
            label="Alasan"
            required
          >
            <UTextarea
              v-model="reason"
              autoresize
              class="w-full"
            />
          </UFormField>
        </template>
        <template #footer>
          <div class="flex justify-end gap-2 w-full">
            <UButton
              color="neutral"
              variant="ghost"
              label="Batal"
              @click="rejecting = null"
            />
            <UButton
              color="error"
              label="Tolak"
              :disabled="!reason.trim()"
              @click="rejecting && decide(rejecting, 'reject')"
            />
          </div>
        </template>
      </UModal>
    </template>
  </UDashboardPanel>
</template>
