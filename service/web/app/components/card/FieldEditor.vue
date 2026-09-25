<script setup lang="ts">
import type { CardDefinition, FieldSpec, Shape, Source, SubfieldSpec } from '~/types/hub'

/**
 * Edit (or correct) one Card value. The form is generated from the field's shape in
 * the card definition, so a new field needs no new component. Values are saved
 * word for word; nothing here translates or rewrites.
 */
const props = defineProps<{
  spec: FieldSpec
  value: unknown
  validUntil: string | null
  mode: 'edit' | 'correct' | 'proposal'
  definition?: CardDefinition | null
  saving?: boolean
}>()
const open = defineModel<boolean>('open', { default: false })
const emit = defineEmits<{ save: [payload: { value: unknown, valid_until: string | null, source: Source }] }>()

type Obj = Record<string, unknown>
const emptyFor = (shape: Shape): unknown => (shape === 'list' ? [] : shape === 'rating' || shape === 'choice' ? null : '')

function initial(): unknown {
  const v = props.value
  if (props.spec.shape === 'object') {
    const obj = (v && typeof v === 'object' ? { ...(v as Obj) } : {}) as Obj
    for (const s of props.spec.subfields ?? []) if (!(s.key in obj)) obj[s.key] = emptyFor(s.shape)
    return obj
  }
  if (props.spec.shape === 'lines') return Array.isArray(v) ? (v as Obj[]).map(l => ({ ...l })) : []
  return v ?? emptyFor(props.spec.shape)
}

const draft = ref<unknown>(initial())
const validUntil = ref<string>(props.validUntil ?? '')
const source = reactive<Source>({ who: '', where: '', when: new Date().toISOString().slice(0, 10) })
watch(open, (o) => {
  if (o) {
    draft.value = initial()
    validUntil.value = props.validUntil ?? ''
  }
})

const obj = computed(() => draft.value as Obj)
const lineList = computed(() => draft.value as Obj[])
function addLine() {
  const line: Obj = {}
  for (const s of props.spec.subfields ?? []) line[s.key] = ''
  lineList.value.push(line)
}
function removeLine(i: number) {
  lineList.value.splice(i, 1)
}
const choiceItems = (list?: string) => (props.definition?.choices[list ?? ''] ?? []).map(c => ({ label: c.label, value: c.key }))

function clean(shape: Shape, v: unknown): unknown {
  if (shape === 'text') return typeof v === 'string' ? v : ''
  if (shape === 'list') return Array.isArray(v) ? v.map(String).filter(x => x.trim() !== '') : []
  if (shape === 'rating') return typeof v === 'number' ? v : null
  return v
}
function payloadValue(): unknown {
  const spec = props.spec
  if (spec.shape === 'object') {
    const out: Obj = {}
    for (const s of spec.subfields ?? []) {
      const c = clean(s.shape, obj.value[s.key])
      if (!isEmptyValue(c)) out[s.key] = c
    }
    return out
  }
  if (spec.shape === 'lines') return lineList.value.filter(l => !isEmptyValue(l))
  return clean(spec.shape, draft.value)
}
function save() {
  emit('save', {
    value: payloadValue(),
    valid_until: validUntil.value || null,
    source: { who: source.who || null, where: source.where || null, when: source.when || null }
  })
}
const subs = computed<SubfieldSpec[]>(() => props.spec.subfields ?? [])

// 'proposal': "Ubah lalu terima" on an Intake Proposal. Source and validity come from
// the Intake itself, so those inputs are hidden.
const TITLE = { edit: 'Ubah', correct: 'Koreksi', proposal: 'Ubah lalu terima' }
const DESCRIPTION = {
  edit: 'Nilai lama tetap tersimpan di riwayat.',
  correct: 'Nilai lama tetap tersimpan di riwayat, ditandai dikoreksi.',
  proposal: 'Ketik ulang atau perbaiki nilainya, persis seperti kata klien.'
}
const SAVE_LABEL = { edit: 'Simpan', correct: 'Simpan koreksi', proposal: 'Terima' }
</script>

<template>
  <USlideover
    v-model:open="open"
    :title="`${TITLE[mode]}: ${spec.label}`"
    :description="DESCRIPTION[mode]"
    :ui="{ content: 'max-w-2xl' }"
  >
    <template #body>
      <div class="space-y-4">
        <p
          v-if="spec.help"
          class="text-sm text-muted"
        >
          {{ spec.help }}
        </p>

        <!-- scalar fields -->
        <UFormField
          v-if="spec.shape === 'text'"
          :label="spec.label"
        >
          <UTextarea
            :model-value="draft as string"
            autoresize
            :rows="3"
            class="w-full"
            @update:model-value="v => (draft = v)"
          />
        </UFormField>
        <UFormField
          v-else-if="spec.shape === 'list'"
          :label="spec.label"
          help="Tekan Enter setelah tiap isian."
        >
          <UInputTags
            :model-value="draft as string[]"
            class="w-full"
            @update:model-value="v => (draft = v)"
          />
        </UFormField>

        <!-- object -->
        <template v-else-if="spec.shape === 'object'">
          <UFormField
            v-for="sub in subs"
            :key="sub.key"
            :label="sub.label"
            :help="sub.shape === 'list' ? 'Tekan Enter setelah tiap isian.' : undefined"
          >
            <UTextarea
              v-if="sub.shape === 'text'"
              :model-value="obj[sub.key] as string"
              autoresize
              :rows="1"
              class="w-full"
              @update:model-value="v => (obj[sub.key] = v)"
            />
            <UInputTags
              v-else-if="sub.shape === 'list'"
              :model-value="obj[sub.key] as string[]"
              class="w-full"
              @update:model-value="v => (obj[sub.key] = v)"
            />
            <div
              v-else-if="sub.shape === 'rating'"
              class="flex items-center gap-3"
            >
              <USlider
                :model-value="(obj[sub.key] as number | null) ?? 3"
                :min="1"
                :max="5"
                class="flex-1"
                :aria-label="sub.label"
                @update:model-value="v => (obj[sub.key] = v as number)"
              />
              <span class="w-12 text-sm tabular-nums">{{ obj[sub.key] ?? '—' }}/5</span>
              <UButton
                v-if="obj[sub.key] != null"
                size="xs"
                color="neutral"
                variant="ghost"
                label="Kosongkan"
                @click="obj[sub.key] = null"
              />
            </div>
            <USelect
              v-else-if="sub.shape === 'choice'"
              :model-value="obj[sub.key] as string"
              :items="choiceItems(sub.choices)"
              placeholder="Pilih…"
              class="w-full"
              @update:model-value="v => (obj[sub.key] = v)"
            />
          </UFormField>
        </template>

        <!-- lines -->
        <template v-else-if="spec.shape === 'lines'">
          <div
            v-for="(line, i) in lineList"
            :key="i"
            class="rounded-md border border-default p-3 space-y-2"
          >
            <div class="grid gap-2 sm:grid-cols-2">
              <UFormField
                v-for="sub in subs"
                :key="sub.key"
                :label="sub.label"
              >
                <UInput
                  :model-value="line[sub.key] as string"
                  class="w-full"
                  @update:model-value="v => (line[sub.key] = v)"
                />
              </UFormField>
            </div>
            <UButton
              size="xs"
              color="error"
              variant="ghost"
              icon="i-lucide-trash-2"
              label="Hapus baris"
              @click="removeLine(i)"
            />
          </div>
          <UButton
            size="sm"
            variant="soft"
            icon="i-lucide-plus"
            label="Tambah baris"
            @click="addLine"
          />
        </template>

        <template v-if="mode !== 'proposal'">
          <USeparator />

          <UFormField
            label="Berlaku sampai"
            help="Kosongkan bila tidak ada batas waktu."
          >
            <UInput
              v-model="validUntil"
              type="date"
              class="w-full sm:w-56"
            />
          </UFormField>
          <div class="grid gap-3 sm:grid-cols-3">
            <UFormField label="Sumber: siapa">
              <UInput
                :model-value="source.who as string"
                placeholder="mis. Bu Rina (PIC)"
                class="w-full"
                @update:model-value="v => (source.who = v)"
              />
            </UFormField>
            <UFormField label="Di mana">
              <UInput
                :model-value="source.where as string"
                placeholder="mis. WhatsApp grup"
                class="w-full"
                @update:model-value="v => (source.where = v)"
              />
            </UFormField>
            <UFormField label="Kapan">
              <UInput
                :model-value="source.when as string"
                type="date"
                class="w-full"
                @update:model-value="v => (source.when = v)"
              />
            </UFormField>
          </div>
        </template>
      </div>
    </template>
    <template #footer>
      <div class="flex justify-end gap-2 w-full">
        <UButton
          color="neutral"
          variant="ghost"
          label="Batal"
          @click="open = false"
        />
        <UButton
          :loading="saving"
          :label="SAVE_LABEL[mode]"
          @click="save"
        />
      </div>
    </template>
  </USlideover>
</template>
