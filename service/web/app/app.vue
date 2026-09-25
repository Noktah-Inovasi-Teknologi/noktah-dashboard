<script setup lang="ts">
// Nuxt UI's own strings (empty tables, dialogs, pickers) in Bahasa.
import { id } from '@nuxt/ui/locale'

useHead({
  meta: [{ name: 'viewport', content: 'width=device-width, initial-scale=1' }],
  link: [{ rel: 'icon', href: '/favicon.ico' }],
  htmlAttrs: { lang: 'id' }
})

useSeoMeta({
  title: 'Noktah Hub',
  robots: 'noindex, nofollow'
})
</script>

<template>
  <UApp :locale="id">
    <NuxtLayout>
      <NuxtPage />
    </NuxtLayout>
  </UApp>
</template>
