// @vitest-environment jsdom
import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fixtures } from '../../api/__fixtures__'
import type { SourceResponse } from '../../api/types'
import { clearSourceCache, fetchSourceText, FIRST_FETCH_BYTES, TAIL_FETCH_BYTES, useSourceText } from './useSourceText'

const FRESH = fixtures.source.freshness as SourceResponse['freshness']
const body = (text: string, total: number, block: string | null = 'text'): SourceResponse => ({ freshness: FRESH, text, total_bytes: total, drift: false, file: 'f', locator: 'l', block })
const reply = (status: number, b: unknown) => new Response(JSON.stringify(b), { status, headers: { 'content-type': 'application/json' } })

interface Seen {
  url: URL
  signal: AbortSignal | null | undefined
}
function serve(handler: (u: URL) => Response | Promise<Response>) {
  const seen: Seen[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      const u = new URL(url, 'http://x')
      seen.push({ url: u, signal: init?.signal })
      return handler(u)
    }),
  )
  return seen
}

beforeEach(() => clearSourceCache())
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('fetching a source (DET-60, DET-65)', () => {
  it('asks for 256 KB from offset 0 on the first fetch', async () => {
    const seen = serve(() => reply(200, body('hello', 5)))
    const r = await fetchSourceText('src://a', 'text')
    expect(r).toMatchObject({ text: 'hello', totalBytes: 5, tooLarge: false, tail: null, block: 'text' })
    expect(seen).toHaveLength(1)
    expect(seen[0].url.searchParams.get('limit')).toBe(String(FIRST_FETCH_BYTES))
    expect(FIRST_FETCH_BYTES).toBe(262_144)
    expect(seen[0].url.searchParams.get('part')).toBe('text')
  })

  it('an output larger than the first fetch also gets its last 16 KB, at offset total − 16384', async () => {
    const total = 900_000
    const seen = serve((u) => (u.searchParams.get('offset') === null ? reply(200, body('x'.repeat(FIRST_FETCH_BYTES), total, 'tool_result')) : reply(200, body('tail text', total, 'tool_result'))))
    const r = await fetchSourceText('src://o', 'output')
    expect(r.tooLarge).toBe(true)
    expect(r.tail).toBe('tail text')
    expect(seen).toHaveLength(2)
    expect(seen[1].url.searchParams.get('offset')).toBe(String(total - TAIL_FETCH_BYTES))
    expect(seen[1].url.searchParams.get('limit')).toBe(String(TAIL_FETCH_BYTES))
  })

  it('a text larger than the cut is marked too large and has no tail unless it is an output', async () => {
    const seen = serve(() => reply(200, body('y'.repeat(FIRST_FETCH_BYTES), 1_000_000)))
    const r = await fetchSourceText('src://t', 'text')
    expect(r.tooLarge).toBe(true)
    expect(r.tail).toBeNull()
    expect(seen).toHaveLength(1)
  })

  it('counts the bytes it got, not the characters', async () => {
    serve(() => reply(200, body('é'.repeat(10), 20)))
    const r = await fetchSourceText('src://u', 'text')
    expect(r.fetchedBytes).toBe(20)
    expect(r.tooLarge).toBe(false)
  })
})

describe('the hook', () => {
  it('fetches once per (src, part) in a session, however often the row comes back', async () => {
    const seen = serve(() => reply(200, body('once', 4)))
    const a = renderHook(() => useSourceText('s1', 'src://a', 'text'))
    await waitFor(() => expect(a.result.current.status).toBe('ready'))
    a.unmount()
    const b = renderHook(() => useSourceText('s1', 'src://a', 'text'))
    expect(b.result.current.status).toBe('ready') // from the cache, at once
    expect(seen).toHaveLength(1)
  })

  it('shares one request between two readers of the same text', async () => {
    const seen = serve(async () => {
      await new Promise((r) => setTimeout(r, 20))
      return reply(200, body('shared', 6))
    })
    const a = renderHook(() => useSourceText('s1', 'src://a', 'text'))
    const b = renderHook(() => useSourceText('s1', 'src://a', 'text'))
    await waitFor(() => expect(a.result.current.status).toBe('ready'))
    await waitFor(() => expect(b.result.current.status).toBe('ready'))
    expect(seen).toHaveLength(1)
  })

  it('the same pointer for another part is another fetch', async () => {
    const seen = serve(() => reply(200, body('x', 1)))
    const a = renderHook(() => useSourceText('s1', 'src://a', 'input'))
    const b = renderHook(() => useSourceText('s1', 'src://a', 'output'))
    await waitFor(() => expect(a.result.current.status).toBe('ready'))
    await waitFor(() => expect(b.result.current.status).toBe('ready'))
    expect(seen).toHaveLength(2)
  })

  it('a new session starts with an empty cache', async () => {
    const seen = serve(() => reply(200, body('x', 1)))
    const a = renderHook(({ s }) => useSourceText(s, 'src://a', 'text'), { initialProps: { s: 's1' } })
    await waitFor(() => expect(a.result.current.status).toBe('ready'))
    a.rerender({ s: 's2' })
    await waitFor(() => expect(seen).toHaveLength(2))
  })

  it('aborts the request when the row changes', async () => {
    const seen = serve(
      (u) => (u.searchParams.get('src') === 'src://slow' ? new Promise<Response>(() => {}) : reply(200, body('fast', 4))),
    )
    const r = renderHook(({ src }) => useSourceText('s1', src, 'text'), { initialProps: { src: 'src://slow' } })
    await waitFor(() => expect(seen).toHaveLength(1))
    expect(seen[0].signal?.aborted).toBe(false)
    r.rerender({ src: 'src://fast' })
    await waitFor(() => expect(r.result.current.status).toBe('ready'))
    expect(seen[0].signal?.aborted).toBe(true)
  })

  it('fetches nothing for a row with no pointer', () => {
    const seen = serve(() => reply(200, body('x', 1)))
    const r = renderHook(() => useSourceText('s1', null, 'text'))
    expect(r.result.current.status).toBe('loading')
    expect(seen).toHaveLength(0)
  })

  it('a 404 source_missing is the missing state (DET-66); other failures are errors that Retry repeats', async () => {
    let n = 0
    serve((u) => {
      const src = u.searchParams.get('src')
      if (src === 'src://gone') return reply(404, { error: 'source file not found', code: 'source_missing' })
      n += 1
      return n === 1 ? reply(500, { error: 'boom', code: 'internal' }) : reply(200, body('ok', 2))
    })
    const gone = renderHook(() => useSourceText('s1', 'src://gone', 'text'))
    await waitFor(() => expect(gone.result.current.status).toBe('missing'))
    const bad = renderHook(() => useSourceText('s1', 'src://bad', 'text'))
    await waitFor(() => expect(bad.result.current.status).toBe('error'))
    const s = bad.result.current
    if (s.status !== 'error') throw new Error('expected error')
    expect(s.message).toBe('boom')
    act(() => s.retry())
    await waitFor(() => expect(bad.result.current.status).toBe('ready'))
  })
})
