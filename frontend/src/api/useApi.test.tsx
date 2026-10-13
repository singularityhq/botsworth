// @vitest-environment jsdom
import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { clearEtags, getJson } from './client'
import { ApiError, NetError } from './errors'
import { ResponseCache, responseCache, useApi, useDebounced } from './useApi'

interface Deferred<T> {
  promise: Promise<T>
  resolve: (v: T) => void
  reject: (e: unknown) => void
}
function deferred<T>(): Deferred<T> {
  let resolve!: (v: T) => void
  let reject!: (e: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

const body = (ingest: number | null, tag = 'x') => ({ freshness: { last_ingest_ms: ingest }, tag })

/** A fetcher that stays pending until the test settles it; honours the signal like fetch does. */
function controlled<T>() {
  const d = deferred<T>()
  let signal: AbortSignal | undefined
  const fetcher = vi.fn((s: AbortSignal) => {
    signal = s
    s.addEventListener('abort', () => d.reject(new DOMException('aborted', 'AbortError')))
    return d.promise
  })
  return { ...d, fetcher, signal: () => signal }
}

beforeEach(() => {
  responseCache.clear()
})
afterEach(() => vi.unstubAllGlobals())

describe('ResponseCache', () => {
  it('shares one request between equal keys', async () => {
    const cache = new ResponseCache()
    const c = controlled<ReturnType<typeof body>>()
    const a = cache.load('k', c.fetcher)
    const b = cache.load('k', c.fetcher)
    expect(c.fetcher).toHaveBeenCalledTimes(1)
    c.resolve(body(1))
    expect(await a.promise).toBe(await b.promise)
  })

  it('aborts the request only when every caller has released it', () => {
    const cache = new ResponseCache()
    const c = controlled<unknown>()
    const a = cache.load('k', c.fetcher)
    const b = cache.load('k', c.fetcher)
    a.release()
    expect(c.signal()?.aborted).toBe(false)
    b.release()
    expect(c.signal()?.aborted).toBe(true)
  })

  it('starts a new request for a key after the old one was aborted', () => {
    const cache = new ResponseCache()
    const c1 = controlled<unknown>()
    cache.load('k', c1.fetcher).release()
    const c2 = controlled<unknown>()
    cache.load('k', c2.fetcher)
    expect(c2.fetcher).toHaveBeenCalledTimes(1)
    expect(c2.signal()?.aborted).toBe(false)
  })

  it('never stores the response of an aborted request', async () => {
    const cache = new ResponseCache()
    const d = deferred<ReturnType<typeof body>>()
    const l = cache.load('k', () => d.promise) // a fetcher that ignores the signal
    l.release()
    d.resolve(body(1))
    await l.promise
    expect(cache.has('k')).toBe(false)
  })

  it('stores a response and serves it from get()', async () => {
    const cache = new ResponseCache()
    await cache.load('k', async () => body(1)).promise
    expect(cache.get('k')).toEqual(body(1))
    expect(cache.get('missing')).toBeUndefined()
  })

  it('keeps at most `limit` entries and drops the least recently used first (FE-4: 200)', async () => {
    const cache = new ResponseCache(3)
    for (const k of ['a', 'b', 'c']) await cache.load(k, async () => body(1, k)).promise
    cache.get('a') // a is now the most recent
    await cache.load('d', async () => body(1, 'd')).promise
    expect(cache.has('b')).toBe(false)
    expect(['a', 'c', 'd'].every((k) => cache.has(k))).toBe(true)
    expect(cache.size).toBe(3)
  })

  it('uses a limit of 200 by default', async () => {
    const cache = new ResponseCache()
    for (let i = 0; i < 201; i++) await cache.load(`k${i}`, async () => body(1)).promise
    expect(cache.size).toBe(200)
    expect(cache.has('k0')).toBe(false)
    expect(cache.has('k200')).toBe(true)
  })

  describe('store changes (FE-7)', () => {
    it('keeps every entry while last_ingest_ms stays the same', async () => {
      const cache = new ResponseCache()
      await cache.load('a', async () => body(5)).promise
      await cache.load('b', async () => body(5)).promise
      expect(cache.has('a') && cache.has('b')).toBe(true)
      expect(cache.epoch).toBe(0)
    })

    it('clears every other entry when a response shows a new last_ingest_ms', async () => {
      const cache = new ResponseCache()
      await cache.load('a', async () => body(5)).promise
      await cache.load('b', async () => body(5)).promise
      await cache.load('c', async () => body(9)).promise
      expect(cache.has('a')).toBe(false)
      expect(cache.has('b')).toBe(false)
      expect(cache.get('c')).toEqual(body(9))
      expect(cache.epoch).toBe(1)
    })

    it('also treats a move from null (no store) to a number as a change', async () => {
      const cache = new ResponseCache()
      await cache.load('a', async () => body(null)).promise
      await cache.load('b', async () => body(9)).promise
      expect(cache.has('a')).toBe(false)
    })

    it('ignores the status poll: it neither clears the cache nor sets the version (D38 H3)', async () => {
      const cache = new ResponseCache()
      await cache.load('a', async () => body(5)).promise
      await cache.load('/api/ingest/status', async () => body(99)).promise
      expect(cache.has('a')).toBe(true)
      await cache.load('b', async () => body(5)).promise
      expect(cache.has('a')).toBe(true)
    })

    it('forgets stored ETags too, so the next request is unconditional', async () => {
      const fetchMock = vi.fn()
      vi.stubGlobal('fetch', fetchMock)
      clearEtags()
      const res = (n: number, etag: string) =>
        new Response(JSON.stringify(body(n)), { headers: { 'content-type': 'application/json', etag } })
      fetchMock.mockResolvedValueOnce(res(5, 'e5'))
      const cache = new ResponseCache()
      await cache.load('/api/a', (s) => getJson('/api/a', s)).promise
      fetchMock.mockResolvedValueOnce(res(9, 'e9'))
      await cache.load('/api/b', (s) => getJson('/api/b', s)).promise // store changed
      fetchMock.mockResolvedValueOnce(res(9, 'e9b'))
      await cache.load('/api/a', (s) => getJson('/api/a', s)).promise
      expect(fetchMock.mock.calls[2][1].headers['if-none-match']).toBeUndefined()
    })

    it('does not compare bodies that carry no freshness', async () => {
      const cache = new ResponseCache()
      await cache.load('a', async () => body(5)).promise
      await cache.load('b', async () => ({ tag: 'no freshness' })).promise
      expect(cache.has('a')).toBe(true)
    })
  })
})

describe('useApi (FE-4)', () => {
  it('returns no data on a first load, then the data', async () => {
    const c = controlled<ReturnType<typeof body>>()
    const { result } = renderHook(() => useApi('k', c.fetcher))
    expect(result.current).toMatchObject({ data: undefined, stale: false, error: null })
    await act(async () => c.resolve(body(1, 'one')))
    expect(result.current).toMatchObject({ data: body(1, 'one'), stale: false, error: null })
  })

  it('does not fetch for a null key', () => {
    const fetcher = vi.fn()
    const { result } = renderHook(() => useApi(null, fetcher))
    expect(fetcher).not.toHaveBeenCalled()
    expect(result.current).toMatchObject({ data: undefined, stale: false, error: null })
  })

  it('returns the previous data with stale: true while a new key loads', async () => {
    const a = controlled<ReturnType<typeof body>>()
    const b = controlled<ReturnType<typeof body>>()
    const { result, rerender } = renderHook(({ k }) => useApi(k, k === 'a' ? a.fetcher : b.fetcher), {
      initialProps: { k: 'a' },
    })
    await act(async () => a.resolve(body(1, 'A')))
    rerender({ k: 'b' })
    expect(result.current).toMatchObject({ data: body(1, 'A'), stale: true })
    await act(async () => b.resolve(body(1, 'B')))
    expect(result.current).toMatchObject({ data: body(1, 'B'), stale: false })
  })

  it('aborts the request of the old key, and a late response for it never renders', async () => {
    const a = controlled<ReturnType<typeof body>>()
    const b = controlled<ReturnType<typeof body>>()
    const { result, rerender } = renderHook(({ k }) => useApi(k, k === 'a' ? a.fetcher : b.fetcher), {
      initialProps: { k: 'a' },
    })
    rerender({ k: 'b' })
    expect(a.signal()?.aborted).toBe(true)
    await act(async () => a.resolve(body(1, 'A late'))) // after abort the rejection won, so this is a no-op
    await act(async () => b.resolve(body(1, 'B')))
    expect(result.current.data).toEqual(body(1, 'B'))
    expect(result.current.error).toBeNull()
  })

  it('does not show a response from a fetcher that ignores abort', async () => {
    const d = deferred<ReturnType<typeof body>>()
    const e = deferred<ReturnType<typeof body>>()
    const { result, rerender } = renderHook(({ k }) => useApi(k, () => (k === 'a' ? d.promise : e.promise)), {
      initialProps: { k: 'a' },
    })
    rerender({ k: 'b' })
    await act(async () => d.resolve(body(1, 'A late')))
    expect(result.current.data).toBeUndefined()
    await act(async () => e.resolve(body(1, 'B')))
    expect(result.current.data).toEqual(body(1, 'B'))
  })

  it('shows no error for an aborted request', async () => {
    const a = controlled<unknown>()
    const { result, unmount } = renderHook(() => useApi('a', a.fetcher))
    unmount()
    expect(a.signal()?.aborted).toBe(true)
    expect(result.current.error).toBeNull()
  })

  it('shows a cached key at once, not stale, then revalidates it', async () => {
    const a1 = controlled<ReturnType<typeof body>>()
    const b = controlled<ReturnType<typeof body>>()
    const a2 = controlled<ReturnType<typeof body>>()
    const fetchers: Record<string, typeof a1.fetcher> = { a: a1.fetcher, b: b.fetcher }
    const { result, rerender } = renderHook(({ k }) => useApi(k, fetchers[k]), { initialProps: { k: 'a' } })
    await act(async () => a1.resolve(body(1, 'A')))
    rerender({ k: 'b' })
    await act(async () => b.resolve(body(1, 'B')))
    fetchers.a = a2.fetcher
    rerender({ k: 'a' })
    expect(result.current).toMatchObject({ data: body(1, 'A'), stale: false })
    expect(a2.fetcher).toHaveBeenCalledTimes(1)
    await act(async () => a2.resolve(body(1, 'A2')))
    expect(result.current.data).toEqual(body(1, 'A2'))
  })

  it('returns an ApiError with no data, and retry runs the fetcher again', async () => {
    const fetcher = vi.fn()
    fetcher.mockRejectedValueOnce(new ApiError(500, 'internal', 'boom'))
    fetcher.mockResolvedValueOnce(body(1, 'ok'))
    const { result } = renderHook(() => useApi('k', fetcher))
    await waitFor(() => expect(result.current.error).toBeInstanceOf(ApiError))
    expect(result.current).toMatchObject({ data: undefined, stale: false })
    act(() => result.current.retry())
    await waitFor(() => expect(result.current.data).toEqual(body(1, 'ok')))
    expect(result.current.error).toBeNull()
    expect(fetcher).toHaveBeenCalledTimes(2)
  })

  it('returns a NetError as error', async () => {
    const { result } = renderHook(() => useApi('k', () => Promise.reject(new NetError('down'))))
    await waitFor(() => expect(result.current.error).toBeInstanceOf(NetError))
  })

  it('drops the data when the key becomes null', async () => {
    const a = controlled<ReturnType<typeof body>>()
    const { result, rerender } = renderHook(({ k }: { k: string | null }) => useApi(k, a.fetcher), { initialProps: { k: 'a' as string | null } })
    await act(async () => a.resolve(body(1)))
    rerender({ k: null })
    expect(result.current.data).toBeUndefined()
  })

  it('shares one request between two hooks with the same key', async () => {
    const c = controlled<ReturnType<typeof body>>()
    const one = renderHook(() => useApi('k', c.fetcher))
    const two = renderHook(() => useApi('k', c.fetcher))
    expect(c.fetcher).toHaveBeenCalledTimes(1)
    await act(async () => c.resolve(body(1, 'shared')))
    expect(one.result.current.data).toEqual(body(1, 'shared'))
    expect(two.result.current.data).toEqual(body(1, 'shared'))
  })
})

describe('useDebounced (FE-5, FE-10)', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  it('returns the first value at once and a changed value only after the delay', () => {
    const { result, rerender } = renderHook(({ v }) => useDebounced(v, 150), { initialProps: { v: 'a' } })
    expect(result.current).toBe('a')
    rerender({ v: 'ab' })
    act(() => vi.advanceTimersByTime(149))
    expect(result.current).toBe('a')
    act(() => vi.advanceTimersByTime(1))
    expect(result.current).toBe('ab')
  })

  it('restarts the delay on every change, so only the last value of a burst comes through', () => {
    const { result, rerender } = renderHook(({ v }) => useDebounced(v, 150), { initialProps: { v: '' } })
    for (const v of ['r', 'rb', 'rba', 'rbac']) {
      rerender({ v })
      act(() => vi.advanceTimersByTime(100))
    }
    expect(result.current).toBe('')
    act(() => vi.advanceTimersByTime(50))
    expect(result.current).toBe('rbac')
  })
})
