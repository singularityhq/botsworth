import { useCallback, useEffect, useMemo, useState } from 'react'
import { ApiError, NetError, isAbort } from '../../../api/errors'
import { responseCache } from '../../../api/useApi'

export interface FetchRequest<T> {
  /** The request URL: the cache key (FE-4). */
  url: string
  fetch: (signal: AbortSignal) => Promise<T>
}

export interface FetchMap<T> {
  /** Bodies by URL, for every requested URL that has one (cached or fetched). */
  data: ReadonlyMap<string, T>
  errors: ReadonlyMap<string, ApiError | NetError>
  /** Drop a failed URL's error and ask for it again. */
  retry: (url: string) => void
}

/**
 * Several requests at once through the shared response cache (FE-4): list pages after the first (API-12) and the full rows of an
 * expanded group. `useApi` holds one key per hook; this holds a changing set. A URL that is no longer asked for is released, so an
 * unused request aborts. A cached URL is answered at once.
 */
export function useFetchMap<T>(requests: readonly FetchRequest<T>[]): FetchMap<T> {
  const [held, setState] = useState<State<T>>(() => empty(responseCache.epoch))
  // A store change clears the cache (FE-7); what this hook holds from before it is dropped too.
  const state = held.epoch === responseCache.epoch ? held : empty<T>(responseCache.epoch)
  const [attempts, setAttempts] = useState<Record<string, number>>({})
  const key = requests.map((r) => `${r.url}#${attempts[r.url] ?? 0}`).join('\n')

  // The latest fetchers, read when an effect starts a request; the effect itself depends on the URL set only.
  const latest = useMemo(() => new Map<string, (signal: AbortSignal) => Promise<T>>(), [])
  for (const r of requests) latest.set(r.url, r.fetch)

  useEffect(() => {
    const live = new Set<string>()
    const releases: (() => void)[] = []
    for (const r of requests) {
      if (responseCache.has(r.url)) continue
      const { promise, release } = responseCache.load<T>(r.url, (signal) => latest.get(r.url)!(signal))
      releases.push(release)
      live.add(r.url)
      promise.then(
        (body) => {
          if (!live.has(r.url)) return
          setState((s) => {
            const cur = s.epoch === responseCache.epoch ? s : empty<T>(responseCache.epoch)
            return { epoch: cur.epoch, data: new Map(cur.data).set(r.url, body), errors: withoutKey(cur.errors, r.url) }
          })
        },
        (e: unknown) => {
          if (!live.has(r.url) || isAbort(e)) return
          if (e instanceof ApiError || e instanceof NetError) {
            setState((s) => {
              const cur = s.epoch === responseCache.epoch ? s : empty<T>(responseCache.epoch)
              return { epoch: cur.epoch, data: cur.data, errors: new Map(cur.errors).set(r.url, e) }
            })
            return
          }
          // Anything else is a bug in a fetcher: surface it through the nearest error boundary.
          setState(() => {
            throw e
          })
        },
      )
    }
    return () => {
      live.clear()
      for (const release of releases) release()
    }
    // `key` is the set of URLs and retry counts; `requests` is rebuilt on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, responseCache.epoch])

  const retry = useCallback((url: string) => {
    setState((s) => ({ ...s, errors: withoutKey(s.errors, url) }))
    setAttempts((a) => ({ ...a, [url]: (a[url] ?? 0) + 1 }))
  }, [])

  // Stable between renders while nothing arrived, so consumers that depend on the maps do not recompute on every render.
  const { data, errors } = useMemo(() => {
    const data = new Map<string, T>()
    for (const r of requests) {
      const body: T | undefined = state.data.get(r.url) ?? responseCache.get<T>(r.url)
      if (body !== undefined) data.set(r.url, body)
    }
    const errors = new Map<string, ApiError | NetError>()
    for (const r of requests) {
      const e = state.errors.get(r.url)
      if (e && !data.has(r.url)) errors.set(r.url, e)
    }
    return { data, errors }
    // `key` names the requested URLs; `state` changes when a body or an error arrives.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, state])
  return { data, errors, retry }
}

interface State<T> {
  epoch: number
  data: Map<string, T>
  errors: Map<string, ApiError | NetError>
}
const empty = <T,>(epoch: number): State<T> => ({ epoch, data: new Map(), errors: new Map() })

function withoutKey<V>(m: Map<string, V>, k: string): Map<string, V> {
  if (!m.has(k)) return m
  const next = new Map(m)
  next.delete(k)
  return next
}
