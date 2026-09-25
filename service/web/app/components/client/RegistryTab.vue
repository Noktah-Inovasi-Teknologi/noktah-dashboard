<script setup lang="ts">
import type { ClientForm } from '~/composables/useClientForm'
import type { ClientRecord, Person } from '~/types/hub'

/**
 * Registry (US4): the Client's details, team and social accounts. From this
 * feature on the Hub is the only place these are edited (FR-016); the Clients and
 * Hashmaps sheet tabs follow within 5 minutes. Every save carries `version`, so a
 * second editor gets "sudah diubah orang lain" instead of overwriting (FR-044).
 */
const props = defineProps<{ client: ClientRecord }>()
const emit = defineEmits<{ updated: [record: ClientRecord] }>()
const toast = useToast()
const { data: me } = await useMe()
const canEdit = computed(() => me.value?.can.edit_registry ?? false)
const { data: people } = await useFetch<Person[]>('/api/hub/v1/people', { query: { status: 'active' }, default: () => [] })

async function send(path: string, method: 'PATCH' | 'PUT' | 'POST', body: Record<string, unknown>, done = 'Tersimpan') {
  try {
    const record = await $fetch<ClientRecord>(`/api/hub/v1/clients/${props.client.id}${path}`,
      { method, body: { version: props.client.version, ...body } })
    emit('updated', record)
    toast.add({ color: 'success', title: done })
    return true
  } catch (err) {
    const e = hubError(err)
    toast.add({ color: e.code === 'conflict' ? 'warning' : 'error', title: e.code === 'conflict' ? 'Data sudah berubah' : 'Gagal', description: e.message })
    return false
  }
}

// ── details ──────────────────────────────────────────────────────────────────
const form = ref<ClientForm>(clientForm(props.client))
const resetDetails = () => (form.value = clientForm(props.client))
watch(() => props.client.version, resetDetails)
const dirty = computed(() => !sameForm(form.value, clientForm(props.client)))
const saving = ref(false)
async function saveDetails() {
  saving.value = true
  const before = clientForm(props.client) as unknown as Record<string, unknown>
  const changes: Record<string, unknown> = {}
  for (const [k, v] of Object.entries(form.value)) {
    if (!sameForm(v, before[k])) changes[k] = typeof v === 'string' ? (v.trim() || null) : (v ?? null)
  }
  await send('', 'PATCH', changes)
  saving.value = false
}

// ── team ─────────────────────────────────────────────────────────────────────
const TEAM = [
  { key: 'account_executive', label: 'Account Executive' },
  { key: 'content_planner', label: 'Content Planner' },
  { key: 'field_associate', label: 'Field Associate' },
  { key: 'content_editor', label: 'Content Editor' },
  { key: 'qc', label: 'QC' }
]
const NOBODY = '__none__'
const peopleItems = computed(() => [{ label: '— Kosong —', value: NOBODY }, ...people.value.map(p => ({ label: p.display_name, value: p.id }))])
async function setTeam(role: string, value: unknown) {
  await send(`/team/${role}`, 'PUT', { person_id: value === NOBODY ? null : value }, 'Tim diperbarui')
}

// ── accounts ─────────────────────────────────────────────────────────────────
const PLATFORM_ICON: Record<string, string> = { instagram: 'i-simple-icons-instagram', tiktok: 'i-simple-icons-tiktok' }
const adding = reactive({ platform: 'instagram', handle: '', relation: 'competitor' })
const addOpen = ref(false)
async function addAccount() {
  if (await send('/accounts', 'POST', { ...adding }, 'Akun ditautkan')) {
    adding.handle = ''
    addOpen.value = false
  }
}
async function toggleAccount(accountId: string, active: boolean) {
  await send(`/accounts/${accountId}`, 'PATCH', { is_active: active }, active ? 'Akun diaktifkan' : 'Akun dinonaktifkan')
}
const accounts = computed(() => [...props.client.accounts].sort((a, b) =>
  Number(b.is_active) - Number(a.is_active) || a.relation.localeCompare(b.relation) || a.handle.localeCompare(b.handle)))
</script>

<template>
  <div class="grid gap-4 xl:grid-cols-2 items-start">
    <UCard :ui="{ body: 'space-y-4' }">
      <template #header>
        <h3 class="font-medium text-highlighted">
          Data klien
        </h3>
      </template>
      <ClientFields
        v-model="form"
        :disabled="!canEdit"
      />
      <div
        v-if="canEdit"
        class="flex justify-end gap-2"
      >
        <UButton
          color="neutral"
          variant="ghost"
          label="Batal"
          :disabled="!dirty || saving"
          @click="resetDetails"
        />
        <UButton
          label="Simpan"
          :loading="saving"
          :disabled="!dirty || !form.name.trim()"
          @click="saveDetails"
        />
      </div>
    </UCard>

    <div class="space-y-4 min-w-0">
      <UCard :ui="{ body: 'space-y-3' }">
        <template #header>
          <h3 class="font-medium text-highlighted">
            Tim
          </h3>
        </template>
        <UFormField
          v-for="r in TEAM"
          :key="r.key"
          :label="r.label"
          class="sm:grid sm:grid-cols-[10rem_minmax(0,1fr)] sm:items-center sm:gap-3"
        >
          <USelectMenu
            :model-value="client.team[r.key]?.person_id ?? NOBODY"
            :items="peopleItems"
            value-key="value"
            :disabled="!canEdit"
            class="w-full"
            @update:model-value="v => setTeam(r.key, v)"
          />
        </UFormField>
      </UCard>

      <UCard :ui="{ body: 'space-y-3' }">
        <template #header>
          <div class="flex items-center gap-2">
            <h3 class="font-medium text-highlighted">
              Akun sosial
            </h3>
            <UButton
              v-if="canEdit"
              class="ms-auto"
              size="xs"
              icon="i-lucide-plus"
              label="Tautkan akun"
              @click="addOpen = true"
            />
          </div>
        </template>
        <UEmpty
          v-if="!accounts.length"
          icon="i-lucide-at-sign"
          title="Belum ada akun"
        />
        <ul
          v-else
          class="divide-y divide-default"
        >
          <li
            v-for="a in accounts"
            :key="a.account_id"
            class="flex flex-wrap items-center gap-2 py-2"
            :class="a.is_active ? '' : 'opacity-70'"
          >
            <UIcon
              :name="PLATFORM_ICON[a.platform] ?? 'i-lucide-at-sign'"
              class="size-4 shrink-0"
            />
            <span class="min-w-0 break-all text-sm">@{{ a.handle }}</span>
            <UBadge
              :label="a.relation === 'own' ? 'Milik klien' : 'Pesaing'"
              :color="a.relation === 'own' ? 'primary' : 'neutral'"
              variant="subtle"
            />
            <UBadge
              v-if="!a.is_active"
              label="Nonaktif"
              color="neutral"
              variant="outline"
            />
            <UButton
              v-if="canEdit"
              class="ms-auto"
              size="xs"
              color="neutral"
              variant="ghost"
              :label="a.is_active ? 'Nonaktifkan' : 'Aktifkan'"
              @click="toggleAccount(a.account_id, !a.is_active)"
            />
          </li>
        </ul>
      </UCard>
    </div>

    <UModal
      v-model:open="addOpen"
      title="Tautkan akun sosial"
      description="Satu akun boleh jadi pesaing untuk beberapa klien, tapi hanya milik satu klien."
    >
      <template #body>
        <div class="grid gap-3 sm:grid-cols-2">
          <UFormField label="Platform">
            <USelect
              v-model="adding.platform"
              :items="[{ label: 'Instagram', value: 'instagram' }, { label: 'TikTok', value: 'tiktok' }]"
              class="w-full"
            />
          </UFormField>
          <UFormField label="Hubungan">
            <USelect
              v-model="adding.relation"
              :items="[{ label: 'Pesaing', value: 'competitor' }, { label: 'Milik klien', value: 'own' }]"
              class="w-full"
            />
          </UFormField>
          <UFormField
            label="Handle atau tautan profil"
            class="sm:col-span-2"
          >
            <UInput
              v-model="adding.handle"
              placeholder="@namaakun atau https://www.instagram.com/namaakun/"
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
            @click="addOpen = false"
          />
          <UButton
            label="Tautkan"
            :disabled="!adding.handle.trim()"
            @click="addAccount"
          />
        </div>
      </template>
    </UModal>
  </div>
</template>
