/** The API error envelope as the proxy passes it through: {error, message, …}. */
export interface HubErrorBody {
  error?: string
  message?: string
  [key: string]: unknown
}

interface FetchErrorLike {
  statusCode?: number
  statusMessage?: string
  data?: { data?: HubErrorBody, statusMessage?: string } & HubErrorBody
}

/** Normalise a useFetch / $fetch error into {status, code, message}. */
export function hubError(err: unknown): { status: number, code: string, message: string } {
  const e = (err ?? {}) as FetchErrorLike
  const body = (e.data?.data ?? e.data ?? {}) as HubErrorBody
  return {
    status: e.statusCode ?? 0,
    code: body.error ?? 'unknown',
    message: body.message ?? e.data?.statusMessage ?? e.statusMessage ?? 'Terjadi kesalahan.'
  }
}
