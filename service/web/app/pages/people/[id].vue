<script setup lang="ts">
import type { PersonForm } from '~/composables/usePersonForm'
import type { Person, RegistryChange } from '~/types/hub'

/**
 * One Person: their data (name, IDs, emails that all sign in as this Person (G-16),
 * Units, roles and permissions; migration 012), current client teams, and history.
 * "Tandai keluar" ends roles, team assignments and permissions and stops sign-in;
 * the Person and their history stay (G-32).
 */
interface PersonDetail extends Person {
  version: number
  left_at: string | null
  team: { client_id: string, client: string, team_role: string, brand: string | null }[]
  history: RegistryChange[]
}

const route = useRoute()
const toast = useToast()
const id = computed(() => String(route.params.id))
const { data: me } = await useMe()
const { data: person, error } = await useFetch<PersonDetail>(() => `/api/hub/v1/people/${id.value}`)
useSeoMeta({ title: () => (person.value ? `${person.value.display_name} · Noktah Hub` : 'Orang · Noktah Hub') })

const canManage = computed(() => (me.value?.can.manage_people ?? false) && person.value?.status === 'active')
const { roleName, unitName } = useCatalog()

async function call(path: string, method: 'POST' | 'PATCH' | 'PUT', body?: Record<string, unknown>, done = 'Tersimpan') {
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

// ── profile: details, emails, Units, roles and permissions, one Save ──────────
const form = ref<PersonForm>(personForm(person.value))
const reset = () => (form.value = personForm(person.value))
watch(person, reset)
const choices = reactive(usePersonChoices(me, () => person.value, () => form.value.units))
const dirty = computed(() => !!person.value && !sameForm(personFormKey(form.value), personFormKey(personForm(person.value))))
const saving = ref(false)
async function save() {
  saving.value = true
  await call('', 'PUT', { version: person.value!.version, ...personBody(form.value) })
  saving.value = false
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
  const label = h.entity === 'person_role' ? roleName(h.field) : (HISTORY_FIELD[h.field] ?? h.field)
  const show = (v: unknown): string => {
    if (v === null || v === undefined || v === '') return '—'
    if (typeof v === 'object') {
      const o = v as Record<string, unknown>
      if ('role' in o) return `${roleName(String(o.role))}${o.noktah_brand ? ` · ${unitName(String(o.noktah_brand))}` : ''}`
      if ('name' in o) return String(o.name)
      return Object.values(o).filter(Boolean).join(' · ')
    }
    return String(v)
  }
  if (h.entity === 'person_role') {
    if (h.new_value && typeof h.new_value === 'object') return `Peran diberikan: ${show(h.new_value)}`
    return `Peran berakhir: ${h.old_value && typeof h.old_value === 'object' ? show(h.old_value) : label}`
  }
  if (h.entity === 'person_unit') return h.new_value ? `Unit ditambahkan: ${unitName(h.field)}` : `Unit dilepas: ${unitName(h.field)}`
  if (h.entity === 'person_permission') {
    const name = PERMISSIONS.find(p => p.key === h.field)?.label ?? h.field
    return h.new_value ? `Izin diberikan: ${name}` : `Izin dicabut: ${name}`
  }
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
          <UCard :ui="{ body: 'space-y-4' }">
            <template #header>
              <h3 class="font-medium text-highlighted">
                Data
              </h3>
            </template>
            <PersonFields
              v-model="form"
              :choices="choices"
              :team="person.team"
              :disabled="!canManage"
            />
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
                :disabled="!dirty || saving"
                @click="reset"
              />
              <UButton
                label="Simpan"
                :loading="saving"
                :disabled="!dirty || !form.display_name.trim()"
                @click="save"
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
                <span class="text-muted"> · {{ roleName(t.team_role) }}</span>
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
          description="Semua peran, izin, dan tim kliennya berakhir, dan dia tidak bisa masuk lagi. Riwayat tetap tersimpan."
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
