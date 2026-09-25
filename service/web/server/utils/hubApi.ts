import type { H3Event } from 'h3'
import { fixtureResponse, fixtureState } from '../fixtures'

type Method = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'

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
 * API errors pass through with their envelope (`data`: {error, message, …}) so
 * pages can branch on the code: no_access → /no-access, conflict → reload,
 * ai_cap_reached → Intake paused. When the PC or tunnel is down (Cloudflare
 * answers 502/530, or the fetch fails), the answer is a 503 in Bahasa.
 */
export async function hubApi<T>(
  event: H3Event,
  path: string,
  opts: { method?: Method, body?: unknown, query?: Record<string, unknown> } = {}
): Promise<T> {
  // Local UI sweep / dev only (never in a production build): sample data.
  const fixture = fixtureState(event)
  if (fixture) return fixtureResponse(path, opts.method ?? 'GET', fixture, opts.body, opts.query) as T

  const config = useRuntimeConfig(event)
  const userJwt = getHeader(event, 'cf-access-jwt-assertion')
  if (!userJwt) {
    throw createError({ statusCode: 401, statusMessage: 'Belum login melalui Cloudflare Access', data: { error: 'unauthenticated' } })
  }
  try {
    return (await $fetch(path, {
      baseURL: config.apiBaseUrl,
      method: opts.method ?? 'GET',
      body: opts.body as Record<string, unknown> | undefined,
      query: opts.query,
      headers: {
        'CF-Access-Client-Id': config.accessClientId,
        'CF-Access-Client-Secret': config.accessClientSecret,
        'X-Hub-User-Jwt': userJwt
      }
    })) as T
  } catch (err: unknown) {
    const e = err as { statusCode?: number, data?: { error?: string, message?: string } }
    const status = e.statusCode
    if (status && status < 500 && e.data?.error) {
      throw createError({ statusCode: status, statusMessage: e.data.message ?? 'Permintaan ditolak', data: e.data })
    }
    throw createError({
      statusCode: 503,
      statusMessage: 'Server kantor tidak bisa dihubungi. Pastikan PC kantor menyala, lalu coba lagi.',
      data: { error: 'unavailable' }
    })
  }
}
