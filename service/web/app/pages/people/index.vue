<script setup lang="ts">
import type { TableColumn } from '@nuxt/ui'
import type { PersonForm } from '~/composables/usePersonForm'
import type { Person } from '~/types/hub'

/** Orang (US5): everyone the Hub knows, with their Units, roles and emails (G-11, migration 012). */
const STATUS = [{ label: 'Aktif', value: 'active' }, { label: 'Keluar', value: 'left' }, { label: 'Semua status', value: 'all' }]
const ALL = '__all__'
const NONE = '__none__'
const status = ref('active')
const search = ref('')
const role = ref(ALL)
const unit = ref(ALL)
const toast = useToast()
const router = useRouter()
const { data: me } = await useMe()
const { data: people, error, status: loading } = await useFetch<Person[]>('/api/hub/v1/people', { query: { status }, default: () => [] })
useSeoMeta({ title: 'Orang · Noktah Hub' })

const { roleName, unitName } = useCatalog()
// Units whose people this manager sees: all of them from the Noktah group, else their own.
const seenUnits = computed(() => {
  const all = (me.value?.catalog ?? []).map(u => u.key)
  return me.value?.can.appoint_bm || me.value?.units.includes('noktah') ? all : all.filter(u => me.value?.units.includes(u))
})
const showUnit = computed(() => seenUnits.value.length > 1)
const roleFilter = computed(() => {
  const keys = new Map<string, string>()
  for (const u of me.value?.catalog ?? []) {
    if (seenUnits.value.includes(u.key)) for (const r of u.roles) keys.set(r.key, r.name)
  }
  return [{ label: 'Semua peran', value: ALL }, ...[...keys].map(([value, label]) => ({ label, value })),
    { label: 'Belum ada peran', value: NONE }]
})
const unitFilter = computed(() => [{ label: 'Semua unit', value: ALL },
  ...seenUnits.value.map(u => ({ label: unitName(u), value: u })), { label: 'Belum ada unit', value: NONE }])
const filtered = computed(() => (role.value !== ALL || unit.value !== ALL || search.value.trim() !== '' || status.value !== 'active'))
function clearFilters() {
  status.value = 'active'
  search.value = ''
  role.value = ALL
  unit.value = ALL
}

const rows = computed(() => {
  const q = search.value.trim().toLowerCase()
  return people.value.filter(p =>
    (!q || p.display_name.toLowerCase().includes(q) || p.emails.some(e => e.includes(q)))
    && (role.value === ALL || (role.value === NONE ? !p.roles.length : p.roles.some(r => r.role === role.value)))
    && (unit.value === ALL || (unit.value === NONE ? !p.units.length : p.units.includes(unit.value))))
})
const { page, pageRows, pageSize, total } = usePaged(rows)

// Unit and Peran are separate columns; the Unit one only when more than one Unit is in view.
const columns = computed<TableColumn<Person>[]>(() => [
  { accessorKey: 'display_name', header: 'Nama', meta: { class: { td: 'whitespace-normal' } } },
  ...(showUnit.value
    ? [{ id: 'units', header: 'Unit', meta: { class: { th: 'hidden sm:table-cell', td: 'hidden sm:table-cell whitespace-normal' } } }]
    : []),
  { id: 'roles', header: 'Peran', meta: { class: { th: 'hidden sm:table-cell', td: 'hidden sm:table-cell whitespace-normal' } } },
  { id: 'emails', header: 'Email', meta: { class: { th: 'hidden md:table-cell', td: 'hidden md:table-cell whitespace-normal' } } }
])
// The same role in two Units (Quality Assurance in Eskala and Venyu) is listed once.
const roleNames = (p: Person) => [...new Set(p.roles.map(r => roleName(r.role)))]
const unitNames = (p: Person) => p.units.map(u => unitName(u))

// ── add ──────────────────────────────────────────────────────────────────────
const addOpen = ref(false)
const draft = ref<PersonForm>(personForm())
const choices = reactive(usePersonChoices(me, () => null, () => draft.value.units))
const adding = ref(false)
watch(addOpen, (open) => {
  // A manager with one Unit adds people to it; others pick.
  const mine = me.value?.manageable_units ?? []
  if (open) draft.value = { ...personForm(), units: mine.length === 1 ? [...mine] : [] }
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
          v-if="showUnit"
          v-model="unit"
          :items="unitFilter"
          class="w-full sm:w-40"
          aria-label="Filter unit"
        />
        <USelect
          v-model="role"
          :items="roleFilter"
          class="w-full sm:w-48"
          aria-label="Filter peran"
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
            <p
              v-if="showUnit"
              class="sm:hidden mt-1 text-xs text-muted"
            >
              {{ unitNames(row.original).join(', ') || 'Belum ada unit' }}
            </p>
            <p class="sm:hidden mt-1 text-xs text-muted">
              {{ roleNames(row.original).join(', ') || 'Belum ada peran' }}
            </p>
          </template>
          <template #units-cell="{ row }">
            <span
              v-if="row.original.units.length"
              class="text-sm"
            >{{ unitNames(row.original).join(', ') }}</span>
            <span
              v-else
              class="text-sm text-muted"
            >Belum ada unit</span>
          </template>
          <template #roles-cell="{ row }">
            <div
              v-if="row.original.roles.length"
              class="flex flex-wrap gap-1"
            >
              <UBadge
                v-for="name in roleNames(row.original)"
                :key="name"
                :label="name"
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
            :choices="choices"
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
