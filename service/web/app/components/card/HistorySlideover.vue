<script setup lang="ts">
import type { CardDefinition, FieldSpec, HistoryItem } from '~/types/hub'

/** Every value a field has had, newest first. Nothing is ever deleted (G-32). */
const props = defineProps<{
  clientId: string
  part: 'profil' | 'guideline'
  spec: FieldSpec
  definition?: CardDefinition | null
}>()
const open = defineModel<boolean>('open', { default: false })

const { data: items, error, status, refresh } = useFetch<HistoryItem[]>(
  () => `/api/hub/v1/clients/${props.clientId}/card/${props.part}/${props.spec.key}/history`,
  { immediate: false, default: () => [] }
)
watch(open, (o) => {
  if (o) refresh()
})

const STATE: Record<HistoryItem['state'], { label: string, color: 'success' | 'warning' | 'neutral' | 'error' }> = {
  current: { label: 'Berlaku', color: 'success' },
  pending: { label: 'Menunggu persetujuan', color: 'warning' },
  superseded: { label: 'Diganti', color: 'neutral' },
  corrected: { label: 'Dikoreksi', color: 'error' },
  rejected: { label: 'Ditolak', color: 'neutral' }
}
</script>

<template>
  <USlideover
    v-model:open="open"
    :title="`Riwayat: ${spec.label}`"
    :ui="{ content: 'max-w-xl' }"
  >
    <template #body>
      <UAlert
        v-if="error"
        color="error"
        :title="hubError(error).message"
      />
      <p
        v-else-if="status === 'pending'"
        class="text-sm text-muted"
      >
        Memuat…
      </p>
      <p
        v-else-if="!items.length"
        class="text-sm text-muted"
      >
        Belum ada riwayat.
      </p>
      <ol
        v-else
        class="space-y-3"
      >
        <li
          v-for="item in items"
          :key="item.id"
          class="rounded-md border border-default p-3 space-y-2"
        >
          <div class="flex flex-wrap items-center gap-2 text-xs text-muted">
            <UBadge
              :label="STATE[item.state].label"
              :color="STATE[item.state].color"
              variant="subtle"
            />
            <span>{{ item.set_by }}, {{ formatDate(item.set_at) }}</span>
            <span v-if="item.valid_until">· berlaku s.d. {{ formatDate(item.valid_until) }}</span>
          </div>
          <CardValueView
            :shape="spec.shape"
            :value="item.value"
            :subfields="spec.subfields"
            :choices="spec.choices"
            :definition="definition"
          />
          <p
            v-if="item.source?.who || item.source?.where"
            class="text-xs text-muted"
          >
            Sumber: {{ [item.source.who, item.source.where, item.source.when].filter(Boolean).join(' · ') }}
          </p>
          <p
            v-if="item.reject_reason"
            class="text-xs text-muted"
          >
            Alasan ditolak: {{ item.reject_reason }}
          </p>
        </li>
      </ol>
    </template>
  </USlideover>
</template>
