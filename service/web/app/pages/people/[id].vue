<script setup lang="ts">
import type { Person, RegistryChange } from '~/types/hub'

/**
 * One Person: details, emails (all sign in as this Person, G-16), roles (granted by
 * the rules in G-9), current teams, and history. "Tandai keluar" ends roles and
 * team assignments and stops sign-in; the Person and their history stay (G-32).
 */
interface PersonDetail extends Person {
  version: number
  left_at: string | null
  team: { client_id: string, client: string, team_role: string }[]
  history: RegistryChange[]
}

const route = useRoute()
const toast = useToast()
const id = computed(() => String(route.params.id))
const { data: me } = await useMe()
const { data: person, error } = await useFetch<PersonDetail>(() => `/api/hub/v1/people/${id.value}`)
useSeoMeta({ title: () => (person.value ? `${person.value.display_name} · Noktah Hub` : 'Orang · Noktah Hub') })

const canManage = computed(() => (me.value?.can.manage_people ?? false) && person.value?.status === 'active')
const brandName = (key: string | null) => (key ? key.charAt(0).toUpperCase() + key.slice(1) : '')
const TEAM_LABEL: Record<string, string> = {
  account_executive: 'Account Executive', content_planner: 'Content Planner', field_associate: 'Field Associate',
  content_editor: 'Content Editor', qc: 'QC'
}

async function call(path: string, method: 'POST' | 'PATCH' | 'DELETE', body?: Record<string, unknown>, done = 'Tersimpan') {
  try {
    person.value = await $fetch<PersonDetail>(`/api/hub/v1/people/${id.value}${path}`, { method, body })
    toast.add({ color: 'success', title: done })
    return true
  } catch (err) {
    const e = hubError(err)
    toast.add({ color: e.code === 'conflict' ? 'warning' : 'error', title: 'Gagal', description: e.message })
    return false
  }
}

// ── details ──────────────────────────────────────────────────────────────────
const form = reactive({ display_name: '', jira_account_id: '', slack_user_id: '' })
const reset = () => Object.assign(form, {
  display_name: person.value?.display_name ?? '', jira_account_id: person.value?.jira_account_id ?? '',
  slack_user_id: person.value?.slack_user_id ?? ''
})
watch(person, reset, { immediate: true })
const dirty = computed(() => !!person.value && (form.display_name !== person.value.display_name
  || form.jira_account_id !== (person.value.jira_account_id ?? '') || form.slack_user_id !== (person.value.slack_user_id ?? '')))
const saveDetails = () => call('', 'PATCH', { version: person.value!.version, ...form })

// ── emails ───────────────────────────────────────────────────────────────────
const newEmail = ref('')
async function addEmail() {
  if (await call('/emails', 'POST', { email: newEmail.value }, 'Email ditambahkan')) newEmail.value = ''
}

// ── roles ────────────────────────────────────────────────────────────────────
const grantable = computed(() => Object.keys(ROLE_LABELS)
  .filter(r => me.value?.can.appoint_bm || !['owner', 'brand_manager'].includes(r))
  .map(r => ({ label: ROLE_LABELS[r]!, value: r })))
const brandItems = computed(() => (me.value?.brands ?? []).map(b => ({ label: brandName(b), value: b })))
const grant = reactive({ role: 'account_executive', brand: '' })
watch(brandItems, (items) => {
  if (!grant.brand && items[0]) grant.brand = items[0].value
}, { immediate: true })
async function grantRole() {
  await call('/roles', 'POST', { role: grant.role, brand: grant.role === 'owner' ? null : grant.brand }, 'Peran diberikan')
}

// ── leaving ──────────────────────────────────────────────────────────────────
const leaveOpen = ref(false)
async function markLeft() {
  if (await call('', 'PATCH', { version: person.value!.version, status: 'left' }, 'Ditandai keluar')) leaveOpen.value = false
}

const HISTORY_FIELD: Record<string, string> = {
  created: 'Dibuat', display_name: 'Nama', jira_account_id: 'Jira account ID', slack_user_id: 'Slack user ID',
  status: 'Status', email: 'Email', linked_to_workers: 'Ditautkan ke WORKERS'
}
function historyLine(h: RegistryChange): string {
  const label = h.entity === 'person_role' ? (ROLE_LABELS[h.field] ?? h.field) : (HISTORY_FIELD[h.field] ?? h.field)
  const show = (v: unknown): string => {
    if (v === null || v === undefined || v === '') return '—'
    if (typeof v === 'object') {
      const o = v as Record<string, unknown>
      if ('role' in o) return `${ROLE_LABELS[String(o.role)] ?? o.role}${o.noktah_brand ? ` · ${brandName(String(o.noktah_brand))}` : ''}`
      if ('name' in o) return String(o.name)
      return Object.values(o).filter(Boolean).join(' · ')
    }
    return String(v)
  }
  if (h.entity === 'person_role') return h.new_value ? `Peran diberikan: ${show(h.new_value)}` : `Peran berakhir: ${label}`
  if (h.field === 'created') return 'Orang ditambahkan'
  if (h.old_value === null || h.old_value === undefined) return `${label}: ${show(h.new_value)}`
  if (h.new_value === null || h.new_value === undefined) return `${label} dihapus: ${show(h.old_value)}`
  return `${label}: ${show(h.old_value)} → ${show(h.new_value)}`
}
</script>

<template>
  <UDashboardPanel id="person">
    <template #header>
      <UDashboardNavbar :title="person?.display_name ?? 'Orang'">
        <template #leading>
          <UDashboardSidebarCollapse />
        </template>
        <template #right>
          <UButton
            to="/people"
            color="neutral"
            variant="ghost"
            icon="i-lucide-arrow-left"
            label="Orang"
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
        v-else-if="person"
        class="space-y-4"
      >
        <UAlert
          v-if="person.status === 'left'"
          color="neutral"
          variant="subtle"
          icon="i-lucide-log-out"
          :title="`Sudah keluar${person.left_at ? ` sejak ${formatDate(person.left_at)}` : ''}`"
          description="Tidak bisa masuk ke Hub dan tidak bisa ditugaskan. Riwayatnya tetap tersimpan."
        />

        <div class="grid gap-4 xl:grid-cols-2 items-start">
          <UCard :ui="{ body: 'space-y-3' }">
            <template #header>
              <h3 class="font-medium text-highlighted">
                Data
              </h3>
            </template>
            <UFormField label="Nama">
              <UInput
                v-model="form.display_name"
                :disabled="!canManage"
                class="w-full"
              />
            </UFormField>
            <div class="grid gap-3 sm:grid-cols-2">
              <UFormField label="Jira account ID">
                <UInput
                  v-model="form.jira_account_id"
                  :disabled="!canManage"
                  class="w-full"
                  :ui="{ base: 'font-mono text-xs' }"
                />
              </UFormField>
              <UFormField label="Slack user ID">
                <UInput
                  v-model="form.slack_user_id"
                  :disabled="!canManage"
                  class="w-full"
                  :ui="{ base: 'font-mono text-xs' }"
                />
              </UFormField>
            </div>
            <div
              v-if="canManage"
              class="flex flex-wrap justify-end gap-2"
            >
              <UButton
                color="error"
                variant="ghost"
                icon="i-lucide-log-out"
                label="Tandai keluar"
                class="me-auto"
                :disabled="person.id === me?.person.id"
                @click="leaveOpen = true"
              />
              <UButton
                color="neutral"
                variant="ghost"
                label="Batal"
                :disabled="!dirty"
                @click="reset"
              />
              <UButton
                label="Simpan"
                :disabled="!dirty || !form.display_name.trim()"
                @click="saveDetails"
              />
            </div>
          </UCard>

          <UCard :ui="{ body: 'space-y-3' }">
            <template #header>
              <h3 class="font-medium text-highlighted">
                Email
              </h3>
            </template>
            <p class="text-xs text-muted">
              Semua email ini masuk sebagai orang yang sama.
            </p>
            <ul
              v-if="person.emails.length"
              class="divide-y divide-default"
            >
              <li
                v-for="e in person.emails"
                :key="e"
                class="flex items-center gap-2 py-2"
              >
                <span class="min-w-0 break-all text-sm">{{ e }}</span>
                <UButton
                  v-if="canManage"
                  class="ms-auto"
                  size="xs"
                  color="neutral"
                  variant="ghost"
                  icon="i-lucide-x"
                  :aria-label="`Lepas ${e}`"
                  @click="call(`/emails/${encodeURIComponent(e)}`, 'DELETE', undefined, 'Email dilepas')"
                />
              </li>
            </ul>
            <p
              v-else
              class="text-sm text-muted"
            >
              Belum ada email: orang ini belum bisa masuk ke Hub.
            </p>
            <div
              v-if="canManage"
              class="flex gap-2"
            >
              <UInput
                v-model="newEmail"
                type="email"
                placeholder="nama@contoh.com"
                class="min-w-0 flex-1"
                aria-label="Email baru"
                @keydown.enter="addEmail"
              />
              <UButton
                label="Tambah"
                :disabled="!newEmail.includes('@')"
                @click="addEmail"
              />
            </div>
          </UCard>

          <UCard :ui="{ body: 'space-y-3' }">
            <template #header>
              <h3 class="font-medium text-highlighted">
                Peran
              </h3>
            </template>
            <ul
              v-if="person.roles.length"
              class="divide-y divide-default"
            >
              <li
                v-for="r in person.roles"
                :key="r.id"
                class="flex flex-wrap items-center gap-2 py-2"
              >
                <span class="text-sm font-medium">{{ ROLE_LABELS[r.role] ?? r.role }}</span>
                <UBadge
                  v-if="r.noktah_brand"
                  :label="brandName(r.noktah_brand)"
                  color="neutral"
                  variant="outline"
                />
                <UButton
                  v-if="me?.can.manage_people && (me?.can.appoint_bm || !['owner', 'brand_manager'].includes(r.role))"
                  class="ms-auto"
                  size="xs"
                  color="neutral"
                  variant="ghost"
                  label="Akhiri"
                  @click="call(`/roles/${r.id}/end`, 'POST', undefined, 'Peran berakhir')"
                />
              </li>
            </ul>
            <p
              v-else
              class="text-sm text-muted"
            >
              Belum ada peran. Tanpa peran Manager, orang ini tidak bisa masuk ke Hub.
            </p>
            <div
              v-if="canManage"
              class="grid gap-2 sm:grid-cols-[minmax(0,1fr)_minmax(0,10rem)_auto]"
            >
              <USelect
                v-model="grant.role"
                :items="grantable"
                aria-label="Peran"
                class="w-full"
              />
              <USelect
                v-model="grant.brand"
                :items="brandItems"
                :disabled="grant.role === 'owner'"
                aria-label="Noktah Brand"
                class="w-full"
              />
              <UButton
                label="Beri peran"
                @click="grantRole"
              />
            </div>
          </UCard>

          <UCard :ui="{ body: 'space-y-2' }">
            <template #header>
              <h3 class="font-medium text-highlighted">
                Tim klien
              </h3>
            </template>
            <ul
              v-if="person.team.length"
              class="space-y-1"
            >
              <li
                v-for="t in person.team"
                :key="`${t.client_id}-${t.team_role}`"
                class="text-sm"
              >
                <NuxtLink
                  :to="`/clients/${t.client_id}?tab=registry`"
                  class="font-medium text-highlighted hover:underline"
                >{{ t.client }}</NuxtLink>
                <span class="text-muted"> · {{ TEAM_LABEL[t.team_role] ?? t.team_role }}</span>
              </li>
            </ul>
            <p
              v-else
              class="text-sm text-muted"
            >
              Tidak ada tim klien.
            </p>
          </UCard>
        </div>

        <UCard>
          <template #header>
            <h3 class="font-medium text-highlighted">
              Riwayat
            </h3>
          </template>
          <ol
            v-if="person.history.length"
            class="space-y-2"
          >
            <li
              v-for="h in person.history"
              :key="h.id"
            >
              <p class="text-sm break-words">
                {{ historyLine(h) }}
              </p>
              <p class="text-xs text-muted">
                {{ h.person }} · {{ formatDate(h.at) }}
              </p>
            </li>
          </ol>
          <p
            v-else
            class="text-sm text-muted"
          >
            Belum ada perubahan.
          </p>
        </UCard>

        <UModal
          v-model:open="leaveOpen"
          :title="`Tandai ${person.display_name} keluar?`"
          description="Semua peran dan tim kliennya berakhir, dan dia tidak bisa masuk lagi. Riwayat tetap tersimpan."
        >
          <template #footer>
            <div class="flex justify-end gap-2 w-full">
              <UButton
                color="neutral"
                variant="ghost"
                label="Batal"
                @click="leaveOpen = false"
              />
              <UButton
                color="error"
                label="Tandai keluar"
                @click="markLeft"
              />
            </div>
          </template>
        </UModal>
      </div>
    </template>
  </UDashboardPanel>
</template>
