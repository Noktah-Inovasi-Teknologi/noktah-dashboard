<script setup lang="ts">
import type { ClientRequest } from '~/types/hub'

/** Riwayat Permintaan: what the Client asked for, word for word (G-26). */
const props = defineProps<{ clientId: string }>()
const toast = useToast()
const { data: me } = await useMe()
const filter = ref<string>('all')
const { data: items, error, refresh } = await useFetch<ClientRequest[]>(() => `/api/hub/v1/clients/${props.clientId}/requests`, { default: () => [] })

type Status = ClientRequest['status']
const STATUS: Record<Status, { label: string, color: 'info' | 'warning' | 'success' | 'neutral' }> = {
  baru: { label: 'Baru', color: 'info' },
  diproses: { label: 'Diproses', color: 'warning' },
  selesai: { label: 'Selesai', color: 'success' },
  ditolak: { label: 'Ditolak', color: 'neutral' }
}
const CHANNEL: Record<ClientRequest['channel'], string> = { whatsapp_group: 'WhatsApp grup', meeting: 'Rapat', email: 'Email', lainnya: 'Lainnya' }
const STATUS_ITEMS = (Object.keys(STATUS) as Status[]).map(value => ({ label: STATUS[value].label, value }))
const CHANNEL_ITEMS = (Object.keys(CHANNEL) as ClientRequest['channel'][]).map(value => ({ label: CHANNEL[value], value }))
const FILTERS = [{ label: 'Semua status', value: 'all' }, ...STATUS_ITEMS]
const CHANNEL_FILTERS = [{ label: 'Semua kanal', value: 'all' }, ...CHANNEL_ITEMS]
const search = ref('')
const channel = ref<string>('all')
const shown = computed(() => {
  const q = search.value.trim().toLowerCase()
  return items.value.filter(r => (filter.value === 'all' || r.status === filter.value)
    && (channel.value === 'all' || r.channel === channel.value)
    && (!q || r.text.toLowerCase().includes(q) || (r.requested_by ?? '').toLowerCase().includes(q)))
})
const { page, pageRows, pageSize, total } = usePaged(shown, 10)

const adding = ref(false)
const form = reactive({ requested_on: new Date().toISOString().slice(0, 10), text: '', requested_by: '', is_pic: true, channel: 'whatsapp_group' as ClientRequest['channel'], link: '' })
async function add() {
  try {
    await $fetch(`/api/hub/v1/clients/${props.clientId}/requests`, { method: 'POST', body: { ...form, link: form.link || null } })
    adding.value = false
    form.text = ''
    toast.add({ color: 'success', title: 'Permintaan dicatat' })
    await refresh()
  } catch (err) {
    toast.add({ color: 'error', title: 'Gagal', description: hubError(err).message })
  }
}

const changing = ref<ClientRequest | null>(null)
const nextStatus = ref<Status>('diproses')
const reason = ref('')
const link = ref('')
function openChange(r: ClientRequest) {
  changing.value = r
  nextStatus.value = r.status
  reason.value = ''
  link.value = r.link ?? ''
}
const changeDirty = computed(() => !!changing.value && (nextStatus.value !== changing.value.status
  || !sameForm(link.value, changing.value.link)
  || (nextStatus.value === 'ditolak' && !sameForm(reason.value, changing.value.reject_reason))))
async function saveChange() {
  if (!changing.value) return
  try {
    await $fetch(`/api/hub/v1/requests/${changing.value.id}`, {
      method: 'PATCH',
      body: { version: changing.value.version, status: nextStatus.value, reject_reason: reason.value || null, link: link.value || null }
    })
    changing.value = null
    await refresh()
  } catch (err) {
    const e = hubError(err)
    toast.add({ color: e.code === 'conflict' ? 'warning' : 'error', title: 'Gagal', description: e.message })
    if (e.code === 'conflict') await refresh()
  }
}
</script>

<template>
  <div class="space-y-4">
    <div class="flex flex-wrap items-center gap-2">
      <UInput
        v-model="search"
        icon="i-lucide-search"
        placeholder="Cari isi atau nama…"
        class="w-full sm:w-64"
        aria-label="Cari permintaan"
      />
      <USelect
        v-model="filter"
        :items="FILTERS"
        class="w-40"
        aria-label="Filter status"
      />
      <USelect
        v-model="channel"
        :items="CHANNEL_FILTERS"
        class="w-40"
        aria-label="Filter kanal"
      />
      <UButton
        v-if="me?.can.edit_profil"
        class="ms-auto"
        icon="i-lucide-plus"
        label="Tambah permintaan"
        @click="adding = true"
      />
    </div>
    <UAlert
      v-if="error"
      color="error"
      :title="hubError(error).message"
    />
    <UEmpty
      v-else-if="!shown.length"
      icon="i-lucide-inbox"
      :title="items.length ? 'Tidak ada permintaan yang cocok dengan filter' : 'Belum ada permintaan'"
    />
    <ul
      v-else
      class="space-y-3"
    >
      <li
        v-for="r in pageRows"
        :key="r.id"
      >
        <UCard :ui="{ body: 'space-y-2' }">
          <div class="flex flex-wrap items-center gap-2 text-xs text-muted">
            <UBadge
              :label="STATUS[r.status].label"
              :color="STATUS[r.status].color"
              variant="subtle"
            />
            <span>{{ formatDate(r.requested_on) }} · {{ r.requested_by ?? '—' }}{{ r.is_pic ? ' (PIC)' : '' }} · {{ CHANNEL[r.channel] }}</span>
          </div>
          <p class="text-sm whitespace-pre-line break-words">
            “{{ r.text }}”
          </p>
          <p
            v-if="r.reject_reason"
            class="text-xs text-muted"
          >
            Alasan ditolak: {{ r.reject_reason }}
          </p>
          <div class="flex flex-wrap items-center gap-2">
            <UButton
              v-if="r.link"
              :to="r.link"
              target="_blank"
              size="xs"
              color="neutral"
              variant="link"
              icon="i-lucide-external-link"
              label="Tautan"
            />
            <UButton
              v-if="me?.can.edit_profil"
              size="xs"
              variant="soft"
              label="Ubah status"
              @click="openChange(r)"
            />
          </div>
        </UCard>
      </li>
    </ul>
    <ListPager
      v-if="shown.length"
      v-model:page="page"
      :total="total"
      :page-size="pageSize"
    />

    <UModal
      v-model:open="adding"
      title="Tambah permintaan"
      description="Tulis persis seperti kata klien."
    >
      <template #body>
        <div class="space-y-3">
          <UFormField
            label="Isi permintaan"
            required
          >
            <UTextarea
              v-model="form.text"
              autoresize
              class="w-full"
            />
          </UFormField>
          <div class="grid gap-3 sm:grid-cols-2">
            <UFormField label="Tanggal">
              <UInput
                v-model="form.requested_on"
                type="date"
                class="w-full"
              />
            </UFormField>
            <UFormField label="Kanal">
              <USelect
                v-model="form.channel"
                :items="CHANNEL_ITEMS"
                class="w-full"
              />
            </UFormField>
            <UFormField label="Dari">
              <UInput
                v-model="form.requested_by"
                class="w-full"
              />
            </UFormField>
            <UFormField label="Tautan (opsional)">
              <UInput
                v-model="form.link"
                class="w-full"
              />
            </UFormField>
          </div>
          <UCheckbox
            v-model="form.is_pic"
            label="Disampaikan oleh PIC"
          />
        </div>
      </template>
      <template #footer>
        <div class="flex justify-end gap-2 w-full">
          <UButton
            color="neutral"
            variant="ghost"
            label="Batal"
            @click="adding = false"
          />
          <UButton
            label="Simpan"
            :disabled="!form.text.trim()"
            @click="add"
          />
        </div>
      </template>
    </UModal>

    <UModal
      :open="!!changing"
      title="Ubah status permintaan"
      @update:open="o => { if (!o) changing = null }"
    >
      <template #body>
        <div class="space-y-3">
          <USelect
            v-model="nextStatus"
            :items="STATUS_ITEMS"
            class="w-full"
          />
          <UFormField
            v-if="nextStatus === 'ditolak'"
            label="Alasan"
            required
          >
            <UTextarea
              v-model="reason"
              autoresize
              class="w-full"
            />
          </UFormField>
          <UFormField label="Tautan (mis. tiket Jira)">
            <UInput
              v-model="link"
              class="w-full"
            />
          </UFormField>
        </div>
      </template>
      <template #footer>
        <div class="flex justify-end gap-2 w-full">
          <UButton
            color="neutral"
            variant="ghost"
            label="Batal"
            @click="changing = null"
          />
          <UButton
            label="Simpan"
            :disabled="!changeDirty || (nextStatus === 'ditolak' && !reason.trim())"
            @click="saveChange"
          />
        </div>
      </template>
    </UModal>
  </div>
</template>
