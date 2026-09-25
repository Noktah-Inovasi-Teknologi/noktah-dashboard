<script setup lang="ts">
import type { TableColumn } from '@nuxt/ui'
import type { ClientForm } from '~/composables/useClientForm'
import type { ClientRecord } from '~/types/hub'

interface ClientRow {
  id: string
  name: string
  status: 'active' | 'pending' | 'inactive'
  is_internal: boolean
  brand: string | null
  brand_name: string | null
  card_completeness: { profil: string, guideline: string }
  pending_approvals: number
  team_summary: { account_executive: string | null, field_associate: string | null, content_editor: string | null }
}

const STATUS = [
  { label: 'Aktif', value: 'active' },
  { label: 'Menunggu', value: 'pending' },
  { label: 'Tidak aktif', value: 'inactive' },
  { label: 'Semua status', value: 'all' }
]
const STATUS_LABEL: Record<string, string> = { active: 'Aktif', pending: 'Menunggu', inactive: 'Tidak aktif' }
const STATUS_COLOR: Record<string, 'success' | 'warning' | 'neutral'> = { active: 'success', pending: 'warning', inactive: 'neutral' }
const ALL = '__all__'
const NOBODY = '__none__'
const CARD_FILTER = [
  { label: 'Semua kartu', value: ALL },
  { label: 'Profil belum lengkap', value: 'profil' },
  { label: 'Menunggu persetujuan', value: 'approval' }
]

const status = ref('active')
const search = ref('')
const brand = ref(ALL)
const member = ref(ALL)
const cardFilter = ref(ALL)
const toast = useToast()
const router = useRouter()
const { data: me } = await useMe()
const { data: clients, error, status: loading } = await useFetch<ClientRow[]>('/api/hub/v1/clients', {
  query: { status },
  default: () => []
})

const TEAM = [
  { key: 'account_executive', label: 'Account Executive' },
  { key: 'field_associate', label: 'Field Associate' },
  { key: 'content_editor', label: 'Content Editor' }
] as const

const showBrand = computed(() => (me.value?.brands?.length ?? 0) > 1)
const brandItems = computed(() => (me.value?.brands ?? []).map(b => ({ label: brandName(b), value: b })))
const brandFilter = computed(() => [{ label: 'Semua brand', value: ALL }, ...brandItems.value])
const memberFilter = computed(() => {
  const names = new Set<string>()
  for (const c of clients.value) {
    for (const t of TEAM) {
      const name = c.team_summary[t.key]
      if (name) names.add(name)
    }
  }
  return [
    { label: 'Semua anggota tim', value: ALL },
    ...[...names].sort().map(n => ({ label: n, value: n })),
    { label: 'Tim belum lengkap', value: NOBODY }
  ]
})
const incomplete = (fraction: string) => {
  const [have, need] = fraction.split('/').map(Number)
  return (have ?? 0) < (need ?? 0)
}
const rows = computed(() => {
  const q = search.value.trim().toLowerCase()
  return clients.value.filter(c =>
    (!q || c.name.toLowerCase().includes(q))
    && (brand.value === ALL || c.brand === brand.value)
    && (member.value === ALL || (member.value === NOBODY
      ? TEAM.some(t => !c.team_summary[t.key])
      : TEAM.some(t => c.team_summary[t.key] === member.value)))
    && (cardFilter.value === ALL
      || (cardFilter.value === 'profil' && incomplete(c.card_completeness.profil))
      || (cardFilter.value === 'approval' && c.pending_approvals > 0)))
})
const { page, pageRows, pageSize, total } = usePaged(rows)
const filtered = computed(() => status.value !== 'active' || search.value.trim() !== ''
  || brand.value !== ALL || member.value !== ALL || cardFilter.value !== ALL)
function clearFilters() {
  status.value = 'active'
  search.value = ''
  brand.value = ALL
  member.value = ALL
  cardFilter.value = ALL
}

const columns = computed<TableColumn<ClientRow>[]>(() => [
  { accessorKey: 'name', header: 'Klien', meta: { class: { td: 'whitespace-normal' } } },
  ...(showBrand.value ? [{ accessorKey: 'brand_name', header: 'Noktah Brand', meta: { class: { th: 'hidden md:table-cell', td: 'hidden md:table-cell' } } } as TableColumn<ClientRow>] : []),
  { accessorKey: 'status', header: 'Status', meta: { class: { th: 'w-px', td: 'w-px' } } },
  // Below xl, completeness sits under the name (see the name cell) instead of taking a
  // column: at 1024px it would push the Content Editor column off-screen, and on phones
  // off the table (UI sweep screenshots). Two short lines rather than one long one.
  { id: 'completeness', header: 'Kartu', meta: { class: { th: 'hidden xl:table-cell', td: 'hidden xl:table-cell whitespace-nowrap' } } },
  // The team, one column per role; below lg they would push the table off-screen. The
  // name inside each cell carries a minimum width (table layout ignores a cell's own),
  // which keeps a name to two lines at most (the sweep measured four at 46px).
  ...TEAM.map(t => ({ id: t.key, header: t.label, meta: { class: { th: 'hidden lg:table-cell', td: 'hidden lg:table-cell whitespace-normal' } } }))
])

// ── add ──────────────────────────────────────────────────────────────────────
// The new Client opens on its Registry tab, where the team and social accounts are set.
const addOpen = ref(false)
const draft = ref<ClientForm>(clientForm())
const draftBrand = ref('')
const adding = ref(false)
watch(addOpen, (open) => {
  if (!open) return
  draft.value = clientForm()
  draftBrand.value = me.value?.brands.length === 1 ? me.value.brands[0]! : ''
})
async function add() {
  adding.value = true
  const f = draft.value
  try {
    const rec = await $fetch<ClientRecord>('/api/hub/v1/clients', {
      method: 'POST',
      body: {
        name: f.name, brand: draftBrand.value, status: f.status,
        quota_post: f.quota_post, quota_story: f.quota_story, quota_short_video: f.quota_short_video,
        drive_folder_id: f.drive_folder_id.trim() || null,
        content_plan_folder_id: f.content_plan_folder_id.trim() || null,
        jira_component_id: f.jira_component_id.trim() || null
      }
    })
    addOpen.value = false
    toast.add({ color: 'success', title: `${rec.name} ditambahkan`, description: 'Lengkapi tim dan akun sosialnya di sini.' })
    await router.push(`/clients/${rec.id}?tab=registry`)
  } catch (err) {
    toast.add({ color: 'error', title: 'Gagal', description: hubError(err).message })
  } finally {
    adding.value = false
  }
}
</script>

<template>
  <UDashboardPanel id="clients">
    <template #header>
      <UDashboardNavbar title="Klien">
        <template #leading>
          <UDashboardSidebarCollapse />
        </template>
        <template #right>
          <UButton
            v-if="me?.can.edit_registry"
            icon="i-lucide-plus"
            label="Tambah klien"
            @click="addOpen = true"
          />
        </template>
      </UDashboardNavbar>
    </template>

    <template #body>
      <div class="grid grid-cols-2 gap-2 sm:flex sm:flex-wrap sm:items-center">
        <UInput
          v-model="search"
          icon="i-lucide-search"
          placeholder="Cari klien…"
          class="col-span-2 w-full sm:w-64"
          aria-label="Cari klien"
        />
        <USelect
          v-model="status"
          :items="STATUS"
          class="w-full sm:w-36"
          aria-label="Filter status"
        />
        <USelect
          v-if="showBrand"
          v-model="brand"
          :items="brandFilter"
          class="w-full sm:w-36"
          aria-label="Filter Noktah Brand"
        />
        <USelect
          v-model="member"
          :items="memberFilter"
          class="w-full sm:w-52"
          aria-label="Filter anggota tim"
        />
        <USelect
          v-model="cardFilter"
          :items="CARD_FILTER"
          class="w-full sm:w-52"
          aria-label="Filter kartu"
        />
        <UButton
          v-if="filtered"
          color="neutral"
          variant="ghost"
          icon="i-lucide-x"
          label="Hapus filter"
          @click="clearFilters"
        />
      </div>
      <UAlert
        v-if="error"
        color="error"
        icon="i-lucide-server-off"
        :title="hubError(error).message"
      />
      <template v-else>
        <UTable
          :data="pageRows"
          :columns="columns"
          :loading="loading === 'pending'"
          :empty="clients.length ? 'Tidak ada klien yang cocok dengan filter.' : 'Belum ada klien.'"
        >
          <template #name-cell="{ row }">
            <NuxtLink
              :to="`/clients/${row.original.id}`"
              class="font-medium text-highlighted hover:underline"
            >
              {{ row.original.name }}
            </NuxtLink>
            <UBadge
              v-if="row.original.is_internal"
              label="Internal"
              variant="subtle"
              color="neutral"
              size="sm"
              class="ms-2"
            />
            <UBadge
              v-if="row.original.pending_approvals"
              :label="`${row.original.pending_approvals} menunggu persetujuan`"
              variant="subtle"
              color="warning"
              size="sm"
              class="ms-2"
            />
            <p class="xl:hidden mt-1 text-xs text-muted">
              Profil {{ row.original.card_completeness.profil }} · Guideline {{ row.original.card_completeness.guideline }}
            </p>
          </template>
          <template #status-cell="{ row }">
            <UBadge
              :color="STATUS_COLOR[row.original.status]"
              variant="subtle"
            >
              {{ STATUS_LABEL[row.original.status] }}
            </UBadge>
          </template>
          <template #completeness-cell="{ row }">
            <span class="block text-sm">Profil {{ row.original.card_completeness.profil }}</span>
            <span class="block text-sm text-muted">Guideline {{ row.original.card_completeness.guideline }}</span>
          </template>
          <template
            v-for="t in TEAM"
            :key="t.key"
            #[`${t.key}-cell`]="{ row }"
          >
            <span
              v-if="row.original.team_summary[t.key]"
              class="block min-w-28 text-sm"
            >{{ row.original.team_summary[t.key] }}</span>
            <span
              v-else
              class="text-sm text-muted"
            >—</span>
          </template>
        </UTable>
        <ListPager
          v-model:page="page"
          :total="total"
          :page-size="pageSize"
        />
      </template>

      <UModal
        v-model:open="addOpen"
        title="Tambah klien"
        description="Tim dan akun sosialnya diisi setelah ini, di tab Registry."
        :ui="{ content: 'sm:max-w-xl' }"
      >
        <template #body>
          <div class="space-y-4">
            <UFormField
              v-if="showBrand"
              label="Noktah Brand"
              required
            >
              <USelect
                v-model="draftBrand"
                :items="brandItems"
                placeholder="Pilih Noktah Brand…"
                class="w-full"
              />
            </UFormField>
            <ClientFields v-model="draft" />
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
              :loading="adding"
              :disabled="!draft.name.trim() || !draftBrand"
              @click="add"
            />
          </div>
        </template>
      </UModal>
    </template>
  </UDashboardPanel>
</template>
