<script setup lang="ts">
import type { CardDefinition, CurrentValue, FieldSpec, PendingValue } from '~/types/hub'

/** One Profil fact or Guideline section: value, validity, source, and actions. */
const props = defineProps<{
  spec: FieldSpec
  current?: CurrentValue
  pending?: PendingValue
  missingRequired?: boolean
  canEdit: boolean
  canApprove?: boolean
  definition?: CardDefinition | null
}>()
defineEmits<{ edit: [], correct: [], history: [], approve: [id: string], reject: [id: string] }>()

const sourceLine = computed(() => {
  const s = props.current?.source
  if (!s) return ''
  return [s.who, s.where, s.when ? formatDate(s.when) : null].filter(Boolean).join(' · ')
})
</script>

<template>
  <UCard
    :ui="{ body: 'space-y-3' }"
    :class="missingRequired ? 'ring-2 ring-warning/50' : ''"
  >
    <div class="flex flex-wrap items-start gap-2">
      <div class="min-w-0 flex-1">
        <h3 class="font-medium text-highlighted">
          {{ spec.label }}
        </h3>
        <p
          v-if="spec.help"
          class="text-xs text-muted mt-0.5"
        >
          {{ spec.help }}
        </p>
      </div>
      <UBadge
        v-if="missingRequired"
        label="Wajib diisi"
        color="warning"
        variant="subtle"
      />
      <UBadge
        v-if="current?.expired"
        label="Kedaluwarsa"
        color="error"
        variant="subtle"
      />
    </div>

    <CardValueView
      :shape="spec.shape"
      :value="current?.value"
      :subfields="spec.subfields"
      :choices="spec.choices"
      :definition="definition"
    />

    <UAlert
      v-if="pending"
      color="warning"
      variant="subtle"
      icon="i-lucide-hourglass"
      title="Menunggu persetujuan"
      :description="`Usulan dari ${pending.set_by}, ${formatDate(pending.set_at)}. Kartu tetap memakai nilai yang sudah disetujui.`"
    >
      <template
        v-if="canApprove"
        #actions
      >
        <UButton
          size="xs"
          label="Setujui"
          icon="i-lucide-check"
          @click="$emit('approve', pending.id)"
        />
        <UButton
          size="xs"
          label="Tolak"
          color="neutral"
          variant="outline"
          @click="$emit('reject', pending.id)"
        />
      </template>
    </UAlert>

    <div class="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted">
      <span v-if="current?.valid_until">Berlaku sampai {{ formatDate(current.valid_until) }}</span>
      <span v-if="sourceLine">Sumber: {{ sourceLine }}</span>
      <span v-if="current">Diisi {{ current.set_by }}, {{ formatDate(current.set_at) }}</span>
      <NuxtLink
        v-if="current?.from_intake"
        :to="`?intake=${current.from_intake}`"
        class="underline"
      >
        Bukti
      </NuxtLink>
    </div>

    <div class="flex flex-wrap gap-2">
      <UButton
        v-if="canEdit"
        size="xs"
        :label="current ? 'Ubah' : 'Isi'"
        icon="i-lucide-pencil"
        variant="soft"
        @click="$emit('edit')"
      />
      <UButton
        v-if="canEdit && current"
        size="xs"
        label="Koreksi"
        icon="i-lucide-eraser"
        color="neutral"
        variant="ghost"
        @click="$emit('correct')"
      />
      <UButton
        size="xs"
        label="Riwayat"
        icon="i-lucide-history"
        color="neutral"
        variant="ghost"
        @click="$emit('history')"
      />
    </div>
  </UCard>
</template>
