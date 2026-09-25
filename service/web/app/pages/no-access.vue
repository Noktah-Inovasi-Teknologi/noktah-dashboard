<script setup lang="ts">
definePageMeta({ layout: false })
const { data: who } = await useFetch<{ email: string | null }>('/api/me')
</script>

<template>
  <UContainer class="py-24 max-w-xl">
    <UPageCard
      icon="i-lucide-lock"
      title="Anda belum punya akses ke Noktah Hub"
      :description="`Minta Brand Manager Anda untuk menambahkan email ${who?.email ?? 'Anda'} beserta peran Anda.`"
    />
  </UContainer>
</template>
