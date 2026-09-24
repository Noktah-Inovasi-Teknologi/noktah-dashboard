<script setup lang="ts">
// Placeholder until the dashboard's first screens exist. It proves the chain
// works: Cloudflare Access login -> Worker -> the signed-in email shown here.
const { data: me } = await useFetch('/api/me')
</script>

<template>
  <UContainer class="py-16">
    <UPageCard
      title="Dashboard internal Noktah"
      description="Sedang dibangun. Halaman ini hanya bisa dibuka setelah login."
      icon="i-lucide-construction"
    >
      <p v-if="me?.email" class="text-sm">
        Masuk sebagai <strong>{{ me.email }}</strong>
      </p>
      <p v-else class="text-sm text-muted">
        Belum ada data login (normal saat dijalankan di komputer sendiri).
      </p>
    </UPageCard>
  </UContainer>
</template>
