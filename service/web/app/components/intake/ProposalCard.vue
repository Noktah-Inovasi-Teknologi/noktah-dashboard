<script setup lang="ts">
import type { CardDefinition, Intake, Proposal, ProposalFlag } from '~/types/hub'

/**
 * One Intake Proposal: current → proposed, the exact excerpt, its flags, the ticks
 * those flags require, and Terima / Ubah lalu terima / Tolak. Nothing reaches the
 * card until one of these is pressed (FR-035). The API enforces the same rules;
 * the buttons only mirror them so a Manager isn't surprised by a refusal.
 */
const props = defineProps<{
  proposal: Proposal
  kind: Intake['kind']
  definition: CardDefinition
  cardVersion: number
  canApprove: boolean
}>()
const emit = defineEmits<{ decided: [result: { proposal: Proposal, card?: { card_version: number, state: string } }] }>()
const toast = useToast()

const FLAG: Record<ProposalFlag, { label: string, color: 'warning' | 'error' | 'neutral' }> = {
  from_image: { label: 'dari gambar — cek manual', color: 'warning' },
  not_pic: { label: 'belum dikonfirmasi PIC', color: 'warning' },
  unverified: { label: 'kutipan tidak ditemukan', color: 'error' },
  price_incomplete: { label: 'harga tanpa satuan', color: 'warning' },
  other_client: { label: 'menyebut klien lain', color: 'warning' },
  contradicts: { label: 'bertentangan', color: 'warning' },
  not_bahasa: { label: 'bukan Bahasa', color: 'neutral' }
}
const flagLabel = (f: ProposalFlag) => (f === 'from_image' && props.kind === 'pdf_scanned' ? 'cek manual' : FLAG[f].label)

const p = computed(() => props.proposal)
const spec = computed(() => p.value.target === 'request' || !p.value.field_key
  ? undefined
  : fieldSpec(props.definition, p.value.target, p.value.field_key))
const title = computed(() => p.value.target === 'request'
  ? 'Permintaan baru'
  : `${props.definition.parts[p.value.target].label} · ${spec.value?.label ?? p.value.field_key}`)
const decided = computed(() => p.value.outcome !== 'pending')
const OUTCOME: Record<string, { label: string, color: 'success' | 'neutral' | 'info' }> = {
  accepted: { label: 'Diterima', color: 'success' },
  edited: { label: 'Diubah lalu diterima', color: 'success' },
  rejected: { label: 'Ditolak', color: 'neutral' },
  tidak_masuk_kartu: { label: 'Tidak masuk kartu', color: 'info' }
}

const ticks = reactive({ image_checked: false, pic_confirmed: false })
const needsImageTick = computed(() => p.value.flags.includes('from_image'))
const needsPicTick = computed(() => p.value.flags.includes('not_pic'))
const ticksDone = computed(() => (!needsImageTick.value || ticks.image_checked) && (!needsPicTick.value || ticks.pic_confirmed))
const mustEdit = computed(() => p.value.flags.includes('unverified'))
const busy = ref(false)

async function decide(outcome: 'accept' | 'edit' | 'reject', finalValue?: unknown) {
  busy.value = true
  const sentTicks: Record<string, boolean> = {}
  if (needsImageTick.value) sentTicks.image_checked = ticks.image_checked
  if (needsPicTick.value) sentTicks.pic_confirmed = ticks.pic_confirmed
  try {
    const res = await $fetch<{ proposal: Proposal, card?: { card_version: number, state: string } }>(
      `/api/hub/v1/proposals/${p.value.id}/decide`,
      { method: 'POST', body: { outcome, final_value: finalValue ?? null, ticks: sentTicks, card_version: props.cardVersion } })
    editorOpen.value = false
    requestOpen.value = false
    if (res.card?.state === 'pending') {
      toast.add({ color: 'warning', title: 'Menunggu persetujuan', description: 'Brand Manager sudah diberi tahu lewat Slack.' })
    }
    emit('decided', res)
  } catch (err) {
    const e = hubError(err)
    toast.add({ color: e.code === 'conflict' ? 'warning' : 'error', title: 'Gagal', description: e.message })
  } finally {
    busy.value = false
  }
}

// "Ubah lalu terima": a fact uses the card's own editor; a Request is one sentence.
const editorOpen = ref(false)
const requestOpen = ref(false)
const requestText = ref('')
function openEdit() {
  if (p.value.target === 'request') {
    requestText.value = String(p.value.proposed_value ?? '')
    requestOpen.value = true
  } else {
    editorOpen.value = true
  }
}
</script>

<template>
  <UCard :ui="{ body: 'space-y-3' }">
    <div class="flex flex-wrap items-center gap-2">
      <h3 class="font-medium text-highlighted min-w-0 wrap-break-word">
        {{ title }}
      </h3>
      <UBadge
        v-if="decided"
        :label="OUTCOME[p.outcome]?.label ?? p.outcome"
        :color="OUTCOME[p.outcome]?.color ?? 'neutral'"
        variant="subtle"
      />
      <span
        v-if="decided && p.decided_by"
        class="text-xs text-muted"
      >oleh {{ p.decided_by }}</span>
    </div>

    <div
      v-if="p.flags.length"
      class="flex flex-wrap gap-1"
    >
      <UBadge
        v-for="f in p.flags"
        :key="f"
        :label="flagLabel(f)"
        :color="FLAG[f].color"
        variant="subtle"
        icon="i-lucide-flag"
      />
    </div>

    <p
      v-if="p.target === 'request'"
      class="text-sm whitespace-pre-line wrap-break-word"
    >
      “{{ p.proposed_value }}”
    </p>
    <div
      v-else-if="spec"
      class="grid gap-4 md:grid-cols-2"
    >
      <div class="min-w-0">
        <p class="text-xs text-muted mb-1">
          Sekarang
        </p>
        <CardValueView
          :shape="spec.shape"
          :value="p.current_value"
          :subfields="spec.subfields"
          :choices="spec.choices"
          :definition="definition"
        />
      </div>
      <div class="min-w-0">
        <p class="text-xs text-muted mb-1">
          {{ p.outcome === 'edited' ? 'Disimpan' : 'Usulan' }}
        </p>
        <CardValueView
          :shape="spec.shape"
          :value="p.outcome === 'edited' ? p.final_value : p.proposed_value"
          :subfields="spec.subfields"
          :choices="spec.choices"
          :definition="definition"
        />
      </div>
    </div>

    <figure class="rounded-md bg-elevated/60 p-3 text-sm space-y-1">
      <blockquote class="italic wrap-break-word whitespace-pre-line">
        “{{ p.excerpt }}”
      </blockquote>
      <figcaption class="text-xs text-muted">
        {{ p.speaker ?? 'Pembicara tidak diketahui' }}<template v-if="p.spoke_at">
          · {{ formatDate(p.spoke_at) }}
        </template><template v-if="p.valid_until">
          · berlaku sampai {{ formatDate(p.valid_until) }}
        </template>
      </figcaption>
    </figure>

    <template v-if="!decided">
      <div
        v-if="needsImageTick || needsPicTick"
        class="flex flex-col gap-2"
      >
        <UCheckbox
          v-if="needsImageTick"
          v-model="ticks.image_checked"
          label="Sudah dicek manual"
          description="Nilai di atas sudah saya cocokkan dengan gambarnya."
        />
        <UCheckbox
          v-if="needsPicTick"
          v-model="ticks.pic_confirmed"
          label="PIC sudah konfirmasi"
          description="PIC klien sudah menyetujui fakta ini secara tertulis."
        />
      </div>
      <p
        v-if="mustEdit"
        class="text-xs text-error"
      >
        Kutipan ini tidak ada di teks yang dikirim. Ketik ulang nilainya lewat “Ubah lalu terima”, atau tolak.
      </p>
      <p
        v-if="p.target === 'guideline' && !canApprove"
        class="text-xs text-muted"
      >
        Perubahan Guideline menunggu persetujuan Brand Manager setelah diterima.
      </p>
      <div class="flex flex-wrap gap-2">
        <UButton
          icon="i-lucide-check"
          label="Terima"
          :disabled="busy || mustEdit || !ticksDone"
          @click="decide('accept')"
        />
        <UButton
          variant="soft"
          icon="i-lucide-pencil"
          label="Ubah lalu terima"
          :disabled="busy || !ticksDone"
          @click="openEdit"
        />
        <UButton
          color="neutral"
          variant="outline"
          label="Tolak"
          :disabled="busy"
          @click="decide('reject')"
        />
      </div>
    </template>

    <CardFieldEditor
      v-if="spec"
      v-model:open="editorOpen"
      :spec="spec"
      :value="p.proposed_value"
      :valid-until="p.valid_until"
      mode="proposal"
      :definition="definition"
      :saving="busy"
      @save="payload => decide('edit', payload.value)"
    />
    <UModal
      v-model:open="requestOpen"
      title="Ubah lalu terima: permintaan"
      description="Tulis persis seperti kata klien."
    >
      <template #body>
        <UTextarea
          v-model="requestText"
          autoresize
          class="w-full"
          aria-label="Isi permintaan"
        />
      </template>
      <template #footer>
        <div class="flex justify-end gap-2 w-full">
          <UButton
            color="neutral"
            variant="ghost"
            label="Batal"
            @click="requestOpen = false"
          />
          <UButton
            label="Terima"
            :loading="busy"
            :disabled="!requestText.trim() || sameForm(requestText, p.proposed_value)"
            @click="decide('edit', requestText.trim())"
          />
        </div>
      </template>
    </UModal>
  </UCard>
</template>
