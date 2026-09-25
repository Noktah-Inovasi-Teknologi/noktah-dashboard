<script setup lang="ts">
import type { Card, CardDefinition, ClientRecord, FieldSpec, Source } from '~/types/hub'

/**
 * One part of the Client Card (Profil or Guideline) as a grid of field cards,
 * with edit / correct / history, approvals (Guideline), and "copy from another
 * client" (Guideline, G-3). A PM/AE Guideline edit is stored as pending and
 * waits for the Brand Manager (G-9).
 */
const props = defineProps<{
  part: 'profil' | 'guideline'
  client: ClientRecord
  card: Card
  definition: CardDefinition
}>()
const emit = defineEmits<{ changed: [] }>()
const toast = useToast()
const { data: me } = await useMe()

const canEdit = computed(() => (props.part === 'profil' ? me.value?.can.edit_profil : me.value?.can.edit_guideline) ?? false)
const canApprove = computed(() => props.part === 'guideline' && (me.value?.can.approve ?? false))
const fields = computed(() => props.definition.parts[props.part].fields)
const missing = computed(() => new Set(props.part === 'profil'
  ? props.card.completeness.profil.missing
  : props.card.completeness.guideline.missing_required))
const pendingFor = (key: string) => props.card.pending.find(p => p.part === props.part && p.field_key === key)

const editing = ref<FieldSpec | null>(null)
const editMode = ref<'edit' | 'correct'>('edit')
const editorOpen = ref(false)
const historyOf = ref<FieldSpec | null>(null)
const historyOpen = ref(false)
const saving = ref(false)

function openEditor(spec: FieldSpec, mode: 'edit' | 'correct') {
  editing.value = spec
  editMode.value = mode
  editorOpen.value = true
}
function openHistory(spec: FieldSpec) {
  historyOf.value = spec
  historyOpen.value = true
}

function explain(err: unknown) {
  const e = hubError(err)
  toast.add({ color: e.code === 'conflict' ? 'warning' : 'error', title: e.code === 'conflict' ? 'Kartu sudah berubah' : 'Gagal menyimpan', description: e.message })
  if (e.code === 'conflict') emit('changed')
}

async function save(payload: { value: unknown, valid_until: string | null, source: Source }) {
  if (!editing.value) return
  saving.value = true
  const url = `/api/hub/v1/clients/${props.client.id}/card/${props.part}/${editing.value.key}${editMode.value === 'correct' ? '/correct' : ''}`
  try {
    const res = await $fetch<{ state: string }>(url, {
      method: editMode.value === 'correct' ? 'POST' : 'PUT',
      body: { card_version: props.card.card_version, ...payload }
    })
    editorOpen.value = false
    toast.add(res.state === 'pending'
      ? { color: 'warning', title: 'Menunggu persetujuan', description: 'Brand Manager sudah diberi tahu lewat Slack.' }
      : { color: 'success', title: 'Tersimpan' })
    emit('changed')
  } catch (err) {
    explain(err)
  } finally {
    saving.value = false
  }
}

// ── approvals ────────────────────────────────────────────────────────────────
const rejecting = ref<string | null>(null)
const rejectReason = ref('')
async function approve(id: string) {
  try {
    await $fetch(`/api/hub/v1/approvals/${id}/approve`, { method: 'POST' })
    toast.add({ color: 'success', title: 'Disetujui' })
    emit('changed')
  } catch (err) {
    explain(err)
  }
}
async function confirmReject() {
  if (!rejecting.value) return
  try {
    await $fetch(`/api/hub/v1/approvals/${rejecting.value}/reject`, { method: 'POST', body: { reason: rejectReason.value } })
    rejecting.value = null
    rejectReason.value = ''
    toast.add({ color: 'success', title: 'Ditolak' })
    emit('changed')
  } catch (err) {
    explain(err)
  }
}

// ── copy from another client (Guideline, same Noktah Brand) ─────────────────
const copyOf = ref<FieldSpec | null>(null)
const copyOpen = ref(false)
const copyFrom = ref<string>()
const { data: siblings } = useFetch<{ id: string, name: string, brand: string | null }[]>('/api/hub/v1/clients', {
  query: { status: 'all' }, default: () => [], immediate: props.part === 'guideline', key: `siblings-${props.client.id}`
})
const siblingItems = computed(() => siblings.value
  .filter(c => c.id !== props.client.id && c.brand === props.client.brand)
  .map(c => ({ label: c.name, value: c.id })))
async function doCopy() {
  if (!copyOf.value || !copyFrom.value) return
  try {
    const res = await $fetch<{ state: string }>(`/api/hub/v1/clients/${props.client.id}/card/guideline/${copyOf.value.key}/copy-from`, {
      method: 'POST', body: { card_version: props.card.card_version, from_client_id: copyFrom.value }
    })
    copyOpen.value = false
    toast.add(res.state === 'pending' ? { color: 'warning', title: 'Disalin, menunggu persetujuan' } : { color: 'success', title: 'Disalin' })
    emit('changed')
  } catch (err) {
    explain(err)
  }
}
</script>

<template>
  <div class="space-y-4">
    <div
      v-if="part === 'guideline'"
      class="text-sm text-muted"
    >
      Brand klien sebagaimana konten harus menampilkannya. Setiap perubahan butuh persetujuan Brand Manager.
      Terisi {{ card.completeness.guideline.filled }} dari {{ card.completeness.guideline.total }} bagian.
    </div>
    <div class="grid gap-4 xl:grid-cols-2">
      <div
        v-for="spec in fields"
        :key="spec.key"
        class="min-w-0"
      >
        <CardFieldCard
          :spec="spec"
          :current="card[part][spec.key]"
          :pending="pendingFor(spec.key)"
          :missing-required="missing.has(spec.key)"
          :can-edit="canEdit"
          :can-approve="canApprove"
          :definition="definition"
          @edit="openEditor(spec, 'edit')"
          @correct="openEditor(spec, 'correct')"
          @history="openHistory(spec)"
          @approve="approve"
          @reject="id => (rejecting = id)"
        />
        <UButton
          v-if="part === 'guideline' && canEdit"
          size="xs"
          color="neutral"
          variant="link"
          icon="i-lucide-copy"
          label="Salin dari klien lain"
          class="mt-1"
          @click="copyOf = spec; copyFrom = undefined; copyOpen = true"
        />
      </div>
    </div>

    <CardFieldEditor
      v-if="editing"
      v-model:open="editorOpen"
      :spec="editing"
      :value="card[part][editing.key]?.value"
      :valid-until="card[part][editing.key]?.valid_until ?? null"
      :mode="editMode"
      :definition="definition"
      :saving="saving"
      @save="save"
    />
    <CardHistorySlideover
      v-if="historyOf"
      v-model:open="historyOpen"
      :client-id="client.id"
      :part="part"
      :spec="historyOf"
      :definition="definition"
    />

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
            v-model="rejectReason"
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
            :disabled="!rejectReason.trim()"
            @click="confirmReject"
          />
        </div>
      </template>
    </UModal>

    <UModal
      v-model:open="copyOpen"
      :title="`Salin ${copyOf?.label ?? ''} dari klien lain`"
      description="Hanya klien di Noktah Brand yang sama."
    >
      <template #body>
        <USelectMenu
          v-model="copyFrom"
          :items="siblingItems"
          value-key="value"
          placeholder="Pilih klien…"
          class="w-full"
        />
      </template>
      <template #footer>
        <div class="flex justify-end gap-2 w-full">
          <UButton
            color="neutral"
            variant="ghost"
            label="Batal"
            @click="copyOpen = false"
          />
          <UButton
            label="Salin"
            :disabled="!copyFrom"
            @click="doCopy"
          />
        </div>
      </template>
    </UModal>
  </div>
</template>
