/**
 * Error types and the FE-8 mapping from a failed request to a UI state.
 * The client turns every failure into ApiError, NetError or AbortError, so
 * classifyError can reject anything else as a bug.
 */

/** A response with a non-2xx status, or a 2xx body that is not JSON (API-4). */
export class ApiError extends Error {
  readonly status: number
  /** API-4 code, or 'bad_response' when the body carries none. */
  readonly code: string
  constructor(status: number, code: string, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
  }
}

/** fetch itself rejected: the server is unreachable (EXP-83). */
export class NetError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'NetError'
  }
}

export type ErrorClass = 'no_store' | 'schema_mismatch' | 'failed' | 'unreachable' | 'aborted'

export function isAbort(e: unknown): boolean {
  return typeof e === 'object' && e !== null && (e as { name?: unknown }).name === 'AbortError'
}

/** FE-8 table, rows "no store", "schema mismatch", "request failed", "unreachable", "aborted". */
export function classifyError(e: unknown): ErrorClass {
  if (isAbort(e)) return 'aborted'
  if (e instanceof NetError) return 'unreachable'
  if (e instanceof ApiError) {
    if (e.status === 503 && e.code === 'no_store') return 'no_store'
    if (e.status === 503 && e.code === 'schema_mismatch') return 'schema_mismatch'
    return 'failed'
  }
  throw new Error(`classifyError: not an ApiError, NetError or AbortError: ${String(e)}`)
}
