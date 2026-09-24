// https://nuxt.com/docs/api/configuration/nuxt-config
export default defineNuxtConfig({
  modules: [
    '@nuxt/eslint',
    '@nuxt/ui'
  ],

  devtools: {
    enabled: true
  },

  css: ['~/assets/css/main.css'],

  // Server-only settings, read per request with useRuntimeConfig(event).
  // On Cloudflare they come from Worker variables/secrets named NUXT_<KEY>
  // (e.g. NUXT_API_BASE_URL); locally from service/web/.env.
  runtimeConfig: {
    apiBaseUrl: '',
    // Cloudflare Access service token for hub-api.noktah.co. Worker secrets
    // NUXT_ACCESS_CLIENT_ID / NUXT_ACCESS_CLIENT_SECRET (wrangler secret put).
    accessClientId: '',
    accessClientSecret: ''
  },

  compatibilityDate: '2026-06-30',

  // Cloudflare Workers with static assets (see wrangler.jsonc).
  nitro: {
    preset: 'cloudflare_module'
  },

  eslint: {
    config: {
      stylistic: {
        commaDangle: 'never',
        braceStyle: '1tbs'
      }
    }
  }
})
