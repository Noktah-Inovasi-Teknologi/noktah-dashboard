<script setup lang="ts">
import type { TableColumn } from '@nuxt/ui'
import type { Person } from '~/types/hub'

/** Orang (US5): everyone the Hub knows, their roles and emails. Roles are internal (G-11). */
const STATUS = [{ label: 'Aktif', value: 'active' }, { label: 'Keluar', value: 'left' }, { label: 'Semua', value: 'all' }]
const status = ref('active')
const search = ref('')
const toast = useToast()
const router = useRouter()
const { data: me } = await useMe()
const { data: people, error, status: loading } = await useFetch<Person[]>('/api/hub/v1/people', { query: { status }, default: () => [] })
useSeoMeta({ title: 'Orang · Noktah Hub' })

const rows = computed(() => {
  const q = search.value.trim().toLowerCase()
  return q ? people.value.filter(p => p.display_name.toLowerCase().includes(q) || p.emails.some(e => e.includes(q))) : people.value
})
const brandName = (key: string | null) => (key ? key.charAt(0).toUpperCase() + key.slice(1) : '')
const columns: TableColumn<Person>[] = [
  { accessorKey: 'display_name', header: 'Nama', meta: { class: { td: 'whitespace-normal' } } },
  { id: 'roles', header: 'Peran', meta: { class: { td: 'whitespace-normal' } } },
  { id: 'emails', header: 'Email', meta: { class: { th: 'hidden md:table-cell', td: 'hidden md:table-cell whitespace-normal' } } }
]

// ── add ──────────────────────────────────────────────────────────────────────
const addOpen = ref(false)
const draft = reactive({ display_name: '', emails: [] as string[], jira_account_id: '' })
async function add() {
  try {
    const p = await $fetch<Person>('/api/hub/v1/people', { method: 'POST', body: { ...draft, jira_account_id: draft.jira_account_id || null } })
    addOpen.value = false
    Object.assign(draft, { display_name: '', emails: [], jira_account_id: '' })
    await router.push(`/people/${p.id}`)
  } catch (err) {
    toast.add({ color: 'error', title: 'Gagal', description: hubError(err).message })
  }
}
</script>

<template>
  <UDashboardPanel id="people">
    <template #header>
      <UDashboardNavbar title="Orang">
        <template #leading>
          <UDashboardSidebarCollapse />
        </template>
        <template #right>
          <UButton
            v-if="me?.can.manage_people"
            icon="i-lucide-user-plus"
            label="Tambah orang"
            @click="addOpen = true"
          />
        </template>
      </UDashboardNavbar>
      <UDashboardToolbar>
        <template #left>
          <UInput
            v-model="search"
            icon="i-lucide-search"
            placeholder="Cari nama atau email…"
            class="w-full sm:w-64"
            aria-label="Cari orang"
          />
        </template>
        <template #right>
          <USelect
            v-model="status"
            :items="STATUS"
            class="w-32"
            aria-label="Filter status"
          />
        </template>
      </UDashboardToolbar>
    </template>

    <template #body>
      <UAlert
        v-if="error"
        color="error"
        icon="i-lucide-server-off"
        :title="hubError(error).message"
      />
      <UTable
        v-else
        :data="rows"
        :columns="columns"
        :loading="loading === 'pending'"
        empty="Belum ada orang."
      >
        <template #display_name-cell="{ row }">
          <NuxtLink
            :to="`/people/${row.original.id}`"
            class="font-medium text-highlighted hover:underline"
          >
            {{ row.original.display_name }}
          </NuxtLink>
          <UBadge
            v-if="row.original.status === 'left'"
            label="Keluar"
            color="neutral"
            variant="outline"
            size="sm"
            class="ms-2"
          />
          <p class="md:hidden mt-1 text-xs text-muted break-all">
            {{ row.original.emails.join(', ') || 'Belum ada email' }}
          </p>
        </template>
        <template #roles-cell="{ row }">
          <div
            v-if="row.original.roles.length"
            class="flex flex-wrap gap-1"
          >
            <UBadge
              v-for="r in row.original.roles"
              :key="r.id"
              :label="r.noktah_brand ? `${ROLE_LABELS[r.role] ?? r.role} · ${brandName(r.noktah_brand)}` : (ROLE_LABELS[r.role] ?? r.role)"
              color="neutral"
              variant="subtle"
            />
          </div>
          <span
            v-else
            class="text-sm text-muted"
          >Belum ada peran</span>
        </template>
        <template #emails-cell="{ row }">
          <span class="text-sm break-all">{{ row.original.emails.join(', ') || '—' }}</span>
        </template>
      </UTable>

      <UModal
        v-model:open="addOpen"
        title="Tambah orang"
        description="Semua email orang ini dianggap satu orang yang sama saat masuk."
      >
        <template #body>
          <div class="space-y-3">
            <UFormField
              label="Nama"
              required
            >
              <UInput
                v-model="draft.display_name"
                class="w-full"
              />
            </UFormField>
            <UFormField
              label="Email"
              help="Tekan Enter setelah tiap email. Boleh email kantor dan pribadi."
            >
              <UInputTags
                v-model="draft.emails"
                class="w-full"
              />
            </UFormField>
            <UFormField label="Jira account ID (opsional)">
              <UInput
                v-model="draft.jira_account_id"
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
              label="Simpan"
              :disabled="!draft.display_name.trim()"
              @click="add"
            />
          </div>
        </template>
      </UModal>
    </template>
  </UDashboardPanel>
</template>
