<script setup lang="ts">
import type { TableColumn } from '@nuxt/ui'
import type { PersonForm } from '~/composables/usePersonForm'
import type { Person } from '~/types/hub'

/** Orang (US5): everyone the Hub knows, their roles and emails. Roles are internal (G-11). */
const STATUS = [{ label: 'Aktif', value: 'active' }, { label: 'Keluar', value: 'left' }, { label: 'Semua status', value: 'all' }]
const ALL = '__all__'
const NO_ROLE = '__none__'
const status = ref('active')
const search = ref('')
const role = ref(ALL)
const brand = ref(ALL)
const toast = useToast()
const router = useRouter()
const { data: me } = await useMe()
const { data: people, error, status: loading } = await useFetch<Person[]>('/api/hub/v1/people', { query: { status }, default: () => [] })
useSeoMeta({ title: 'Orang · Noktah Hub' })

const showBrand = computed(() => (me.value?.brands.length ?? 0) > 1)
const ROLE_FILTER = [{ label: 'Semua peran', value: ALL }, ...Object.entries(ROLE_LABELS).map(([value, label]) => ({ label, value })), { label: 'Belum ada peran', value: NO_ROLE }]
const brandFilter = computed(() => [{ label: 'Semua Noktah Brand', value: ALL }, ...(me.value?.brands ?? []).map(b => ({ label: brandName(b), value: b }))])
const filtered = computed(() => (role.value !== ALL || brand.value !== ALL || search.value.trim() !== '' || status.value !== 'active'))
function clearFilters() {
  status.value = 'active'
  search.value = ''
  role.value = ALL
  brand.value = ALL
}

const rows = computed(() => {
  const q = search.value.trim().toLowerCase()
  return people.value.filter(p =>
    (!q || p.display_name.toLowerCase().includes(q) || p.emails.some(e => e.includes(q)))
    && (role.value === ALL || (role.value === NO_ROLE ? !p.roles.length : p.roles.some(r => r.role === role.value)))
    && (brand.value === ALL || p.roles.some(r => r.noktah_brand === brand.value)))
})
const { page, pageRows, pageSize, total } = usePaged(rows)

const columns: TableColumn<Person>[] = [
  { accessorKey: 'display_name', header: 'Nama', meta: { class: { td: 'whitespace-normal' } } },
  { id: 'roles', header: 'Peran', meta: { class: { td: 'whitespace-normal' } } },
  { id: 'emails', header: 'Email', meta: { class: { th: 'hidden md:table-cell', td: 'hidden md:table-cell whitespace-normal' } } }
]

// ── add ──────────────────────────────────────────────────────────────────────
const addOpen = ref(false)
const draft = ref<PersonForm>(personForm())
const roleItems = useRoleItems(me, () => [])
const adding = ref(false)
watch(addOpen, (open) => {
  if (open) draft.value = personForm()
})
async function add() {
  adding.value = true
  try {
    const p = await $fetch<Person>('/api/hub/v1/people', { method: 'POST', body: personBody(draft.value) })
    addOpen.value = false
    await router.push(`/people/${p.id}`)
  } catch (err) {
    toast.add({ color: 'error', title: 'Gagal', description: hubError(err).message })
  } finally {
    adding.value = false
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
    </template>

    <template #body>
      <div class="grid grid-cols-2 gap-2 sm:flex sm:flex-wrap sm:items-center">
        <UInput
          v-model="search"
          icon="i-lucide-search"
          placeholder="Cari nama atau email…"
          class="col-span-2 w-full sm:w-64"
          aria-label="Cari orang"
        />
        <USelect
          v-model="status"
          :items="STATUS"
          class="w-full sm:w-36"
          aria-label="Filter status"
        />
        <USelect
          v-model="role"
          :items="ROLE_FILTER"
          class="w-full sm:w-44"
          aria-label="Filter peran"
        />
        <USelect
          v-if="showBrand"
          v-model="brand"
          :items="brandFilter"
          class="w-full sm:w-44"
          aria-label="Filter Noktah Brand"
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
          :empty="people.length ? 'Tidak ada orang yang cocok dengan filter.' : 'Belum ada orang.'"
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
                :label="r.noktah_brand && showBrand ? `${ROLE_LABELS[r.role] ?? r.role} · ${brandName(r.noktah_brand)}` : (ROLE_LABELS[r.role] ?? r.role)"
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
        <ListPager
          v-model:page="page"
          :total="total"
          :page-size="pageSize"
        />
      </template>

      <UModal
        v-model:open="addOpen"
        title="Tambah orang"
        description="Semua isian bisa diubah lagi nanti di halaman orang ini."
      >
        <template #body>
          <PersonFields
            v-model="draft"
            :role-items="roleItems"
          />
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
              :disabled="!draft.display_name.trim()"
              @click="add"
            />
          </div>
        </template>
      </UModal>
    </template>
  </UDashboardPanel>
</template>
