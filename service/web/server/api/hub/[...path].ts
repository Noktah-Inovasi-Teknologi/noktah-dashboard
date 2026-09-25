/**
 * The one door from the browser to hub-api: /api/hub/v1/... → hub-api /v1/...
 * (method, query and JSON body forwarded; credentials attached in hubApi()).
 *
 * Only /v1 is forwarded. /internal is for Prefect on the office PC's Docker
 * network and is refused by the API for anything that came through Cloudflare;
 * the proxy doesn't even try.
 */
export default defineEventHandler(async (event) => {
  const path = getRouterParam(event, 'path') ?? ''
  if (!path.startsWith('v1/')) {
    throw createError({ statusCode: 404, statusMessage: 'Tidak ditemukan' })
  }
  const method = event.method as 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'
  const body = method === 'GET' ? undefined : await readBody(event).catch(() => undefined)
  return hubApi(event, `/${path}`, { method, body, query: getQuery(event) })
})
