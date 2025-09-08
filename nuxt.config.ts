import tailwindcss from "@tailwindcss/vite";

export default defineNuxtConfig({
  compatibilityDate: "2025-07-15",
  css: ["~/assets/css/main.css"],
  content: {
    database: {
      type: "postgres",
      url: `postgresql://${process.env.POSTGRES_USER}:${process.env.POSTGRES_PASSWORD}@postgres:5432/${process.env.POSTGRES_DB}`,
    },
    experimental: {
      sqliteConnector: "native",
    },
  },
  devtools: { enabled: true },
  kinde: {
    debug: true,
    authDomain: process.env.NUXT_KINDE_AUTH_DOMAIN,
    clientId: process.env.NUXT_KINDE_CLIENT_ID,
    clientSecret: process.env.NUXT_KINDE_CLIENT_SECRET,
    redirectURL: process.env.NUXT_KINDE_REDIRECT_URL,
    logoutRedirectURL: process.env.NUXT_KINDE_LOGOUT_REDIRECT_URL,
    postLoginRedirectURL: process.env.NUXT_KINDE_POST_LOGIN_REDIRECT_URL,
  },
  modules: [
    "@nuxt/image",
    "@nuxt/scripts",
    "@nuxt/test-utils",
    "@nuxt/test-utils/module",
    "@nuxt/ui",
    "@nuxt/content",
    "@nuxtjs/kinde",
  ],
  routeRules: {
    "/**": {
      appMiddleware: ["auth-logged-in"],
      kinde: {
        redirectUrl: "/api/login",
        external: true,
      },
    },
    "/workflows": {
      appMiddleware: ["auth-logged-in"],
      kinde: {
        permissions: {
          "noktah-dashboard:admin": true,
        },
        redirectUrl: "/api/login",
        external: true,
      },
    },
  },
  vite: {
    plugins: [tailwindcss()],
  },
});
