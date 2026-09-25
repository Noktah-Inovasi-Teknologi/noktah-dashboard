/**
 * A signed-in Person without a Hub role sees only /no-access (G-11, SC-009).
 * Roles are the Hub's own; the API answers 403 `no_access` and this sends them away.
 */
export default defineNuxtRouteMiddleware(async (to) => {
  if (to.path === '/no-access') return
  const { error } = await useMe()
  if (error.value && hubError(error.value).code === 'no_access') {
    return navigateTo('/no-access')
  }
})
