<script setup lang="ts">
// First screen wired end to end: Access login -> Worker -> tunnel -> Python API
// -> database. The signed-in email comes from the Worker alone, so it still shows
// when the office PC is off; the client list needs the PC.
const { data: me } = await useFetch('/api/me')
const { data: clients, error, status } = await useFetch('/api/clients', { lazy: true })

const columns = [
  { accessorKey: 'name', header: 'Klien' },
  { accessorKey: 'is_active', header: 'Status' }
]
</script>

<template>
  <UContainer class="py-10 space-y-6">
    <UPageCard
      title="Dashboard internal Noktah"
      description="Sedang dibangun."
      icon="i-lucide-construction"
    >
      <p
        v-if="me?.email"
        class="text-sm"
      >
        Masuk sebagai <strong>{{ me.email }}</strong>
      </p>
    </UPageCard>

    <UAlert
      v-if="error"
      color="error"
      icon="i-lucide-server-off"
      :title="error.statusMessage || 'Gagal memuat data'"
    />

    <UTable
      v-else
      :data="clients ?? []"
      :columns="columns"
      :loading="status === 'pending'"
    >
      <template #is_active-cell="{ row }">
        <UBadge
          :color="row.original.is_active ? 'success' : 'neutral'"
          variant="subtle"
        >
          {{ row.original.is_active ? 'Aktif' : 'Tidak aktif' }}
        </UBadge>
      </template>
    </UTable>
  </UContainer>
</template>
