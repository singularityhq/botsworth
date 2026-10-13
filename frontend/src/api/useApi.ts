import { useCallback, useEffect, useRef, useState } from 'react'
import { clearEtags } from './client'
import { ApiError, NetError, isAbort } from './errors'

/**
 * FE-4 / FE-7: one fetch hook with an in-memory cache.
 *
 * `ResponseCache` is the cache and request-sharing logic with no React in
 * it; `useApi` binds one component to it. Keys are request URLs (FE-4).
 */

const CACHE_LIMIT = 200

/** The status poll is not a data route: it never changes the cache (FE-7, D38 H3). */
const STATUS_KEY = '/api/ingest/status'

interface Inflight<T> {
  promise: Promise<T>
  controller: AbortController
  refs: number
}

export class ResponseCache {
  private readonly entries = new Map<string, unknown>()
  private readonly inflight = new Map<string, Inflight<unknown>>()
  private storeVersion: number | null | undefined
  /** Incremented each time the cache is cleared because the store changed. */
  epoch = 0

  private readonly limit: number

  constructor(limit = CACHE_LIMIT) {
    this.limit = limit
  }

  get size(): number {
    return this.entries.size
  }

  get<T>(key: string): T | undefined {
    if (!this.entries.has(key)) return undefined
    const v = this.entries.get(key)
    this.entries.delete(key)
    this.entries.set(key, v)
    return v as T
  }

  has(key: string): boolean {
    return this.entries.has(key)
  }

  clear(): void {
    this.entries.clear()
    this.storeVersion = undefined
    clearEtags()
  }

  /**
   * Start (or join) the request for `key`. Equal keys share one request.
   * `release` drops this caller's claim; the request aborts when no claim is
   * left, and an aborted request never reaches the cache.
   */
  load<T>(key: string, fetcher: (signal: AbortSignal) => Promise<T>): { promise: Promise<T>; release: () => void } {
    let entry = this.inflight.get(key) as Inflight<T> | undefined
    if (!entry) {
      const controller = new AbortController()
      const created: Inflight<T> = {
        controller,
        refs: 0,
        promise: fetcher(controller.signal).then((data) => {
          if (this.inflight.get(key) === created) this.accept(key, data)
          return data
        }),
      }
      const settle = () => {
        if (this.inflight.get(key) === created) this.inflight.delete(key)
      }
      created.promise.then(settle, settle)
      this.inflight.set(key, created)
      entry = created
    }
    const claimed = entry
    claimed.refs += 1
    let released = false
    return {
      promise: claimed.promise,
      release: () => {
        if (released) return
        released = true
        claimed.refs -= 1
        if (claimed.refs === 0) {
          claimed.controller.abort()
          if (this.inflight.get(key) === claimed) this.inflight.delete(key)
        }
      },
    }
  }

  /** Store a response; clear everything else when the store version moved (FE-7). */
  private accept(key: string, data: unknown): void {
    if (key !== STATUS_KEY) {
      const fresh = (data as { freshness?: { last_ingest_ms?: number | null } } | null)?.freshness
      // FALLBACK-OK: interfaces.md API-7 (every data body has freshness; a body without it carries no version to compare)
      if (fresh && fresh.last_ingest_ms !== undefined) {
        if (this.storeVersion !== undefined && this.storeVersion !== fresh.last_ingest_ms) {
          this.entries.clear()
          clearEtags()
          this.epoch += 1
        }
        this.storeVersion = fresh.last_ingest_ms
      }
    }
    this.entries.delete(key)
    this.entries.set(key, data)
    if (this.entries.size > this.limit) this.entries.delete(this.entries.keys().next().value as string)
  }
}

/** The cache the hooks share. */
export const responseCache = new ResponseCache()

export interface ApiState<T> {
  /** undefined only on a first load with nothing to show (SYS-54). */
  data: T | undefined
  /** true while `data` belongs to an earlier key (EXP-78). */
  stale: boolean
  error: ApiError | NetError | null
  retry: () => void
}

interface Settled<T> {
  key: string
  data: T | undefined
  error: ApiError | NetError | null
}

/**
 * Fetch `fetcher` for `key` and keep the result.
 * - A null key does not fetch and returns no data.
 * - A key change or unmount aborts the request; an aborted request sets no state.
 * - While a new key loads, the previous data comes back with `stale: true`.
 * - A cached key shows its cached data at once (`stale: false`) and is revalidated.
 */
export function useApi<T>(key: string | null, fetcher: (signal: AbortSignal) => Promise<T>): ApiState<T> {
  const [settled, setSettled] = useState<Settled<T> | null>(null)
  const [attempt, setAttempt] = useState(0)
  const fetcherRef = useRef(fetcher)
  fetcherRef.current = fetcher
  const lastData = useRef<T | undefined>(undefined)

  useEffect(() => {
    if (key === null) return
    let live = true
    const { promise, release } = responseCache.load<T>(key, (signal) => fetcherRef.current(signal))
    promise.then(
      (data) => {
        if (!live) return
        setSettled({ key, data, error: null })
      },
      (e: unknown) => {
        if (!live || isAbort(e)) return
        if (e instanceof ApiError || e instanceof NetError) {
          setSettled({ key, data: undefined, error: e })
          return
        }
        // Anything else is a bug in a fetcher: surface it through the nearest error boundary.
        setSettled(() => {
          throw e
        })
      },
    )
    return () => {
      live = false
      release()
    }
  }, [key, attempt])

  const retry = useCallback(() => {
    setSettled(null)
    setAttempt((n) => n + 1)
  }, [])

  if (key === null) {
    lastData.current = undefined
    return { data: undefined, stale: false, error: null, retry }
  }
  if (settled && settled.key === key) {
    if (settled.error) return { data: undefined, stale: false, error: settled.error, retry }
    lastData.current = settled.data
    return { data: settled.data, stale: false, error: null, retry }
  }
  const cached = responseCache.get<T>(key)
  if (cached !== undefined) {
    lastData.current = cached
    return { data: cached, stale: false, error: null, retry }
  }
  return { data: lastData.current, stale: lastData.current !== undefined, error: null, retry }
}

/**
 * `value` after it has stopped changing for `ms` (FE-5: search requests use
 * the text debounced by 150 ms; FE-10: the palette uses 100 ms). The URL
 * still updates on every keystroke; only the request key uses this value.
 */
export function useDebounced<T>(value: T, ms: number): T {
  const [settled, setSettled] = useState(value)
  useEffect(() => {
    const id = setTimeout(() => setSettled(value), ms)
    return () => clearTimeout(id)
  }, [value, ms])
  return settled
}
