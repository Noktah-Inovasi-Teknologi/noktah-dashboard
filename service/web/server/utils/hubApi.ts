import type { H3Event } from 'h3'

/**
 * Call the Python API on the office PC (hub-api.noktah.co, through the tunnel).
 *
 * Sends two credentials, both required by the API:
 * - the Worker's Cloudflare Access service token, so Access lets the call through
 *   to the API at all;
 * - the signed-in manager's own Access token (from this request's
 *   Cf-Access-Jwt-Assertion), forwarded as X-Hub-User-Jwt so the API can verify
 *   WHO is acting. The API never trusts a plain email header.
 *
 * When the PC or tunnel is down, Cloudflare answers 502/530; that becomes a 503
 * with a message managers can act on, instead of a stack trace.
 */
export async function hubApi<T>(
  event: H3Event,
  path: string,
  opts: { method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE', body?: Record<string, unknown> } = {}
): Promise<T> {
  const config = useRuntimeConfig(event)
  const userJwt = getHeader(event, 'cf-access-jwt-assertion')
  if (!userJwt) {
    throw createError({ statusCode: 401, statusMessage: 'Belum login melalui Cloudflare Access' })
  }
  try {
    return (await $fetch(path, {
      baseURL: config.apiBaseUrl,
      method: opts.method ?? 'GET',
      body: opts.body,
      headers: {
        'CF-Access-Client-Id': config.accessClientId,
        'CF-Access-Client-Secret': config.accessClientSecret,
        'X-Hub-User-Jwt': userJwt
      }
    })) as T
  } catch (err: unknown) {
    const status = (err as { statusCode?: number }).statusCode
    if (status === 401 || status === 403 || status === 404 || status === 422) {
      throw createError({ statusCode: status, statusMessage: (err as { statusMessage?: string }).statusMessage })
    }
    throw createError({
      statusCode: 503,
      statusMessage: 'Server kantor tidak bisa dihubungi. Pastikan PC kantor menyala, lalu coba lagi.'
    })
  }
}
