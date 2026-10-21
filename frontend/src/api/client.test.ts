import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { client, clearEtags, getJson, scopeParams, urls } from './client'
import { ApiError, NetError, classifyError } from './errors'
import { fixtures } from './__fixtures__'
import type { Scope } from './types'

const SCOPE: Scope = {
  from_day: '2026-10-06', to_day: '2026-10-12', q: '', providers: [], models: [], branches: [], projects: [], show_automated: false,
}

const json = (body: unknown, init: ResponseInit & { etag?: string } = {}) =>
  new Response(JSON.stringify(body), {
    status: init.status ?? 200,
    headers: { 'content-type': 'application/json', ...(init.etag ? { etag: init.etag } : {}) },
  })

let fetchMock: ReturnType<typeof vi.fn>
beforeEach(() => {
  fetchMock = vi.fn()
  vi.stubGlobal('fetch', fetchMock)
  clearEtags()
})
afterEach(() => vi.unstubAllGlobals())

describe('scopeParams (API-2, API-3)', () => {
  it('writes nothing for the default scope except the days and the browser zone', () => {
    expect(scopeParams(SCOPE).toString()).toBe('tz=Asia%2FKolkata&from=2026-10-06&to=2026-10-12')
  })

  it('writes only the browser zone for an all-time default scope', () => {
    expect(scopeParams({ ...SCOPE, from_day: null, to_day: null }).toString()).toBe('tz=Asia%2FKolkata')
  })

  it('repeats the key for each list value, in order', () => {
    const p = scopeParams({ ...SCOPE, providers: ['codex', 'omp'], models: ['m1', 'm2'], branches: ['main'], projects: ['/a', '/b'] })
    expect(p.getAll('provider')).toEqual(['codex', 'omp'])
    expect(p.getAll('model')).toEqual(['m1', 'm2'])
    expect(p.getAll('branch')).toEqual(['main'])
    expect(p.getAll('project')).toEqual(['/a', '/b'])
    expect(p.toString()).toContain('provider=codex&provider=omp')
  })

  it('sends q when set, and auto=show only when automated sessions are shown', () => {
    expect(scopeParams({ ...SCOPE, q: 'rbac sync' }).get('q')).toBe('rbac sync')
    expect(scopeParams({ ...SCOPE, show_automated: true }).get('auto')).toBe('show')
    expect(scopeParams(SCOPE).has('auto')).toBe(false)
    expect(scopeParams(SCOPE).has('q')).toBe(false)
  })

  it('refuses a scope with exactly one day (QRY-3)', () => {
    expect(() => scopeParams({ ...SCOPE, to_day: null })).toThrow()
    expect(() => scopeParams({ ...SCOPE, from_day: null })).toThrow()
  })
})

describe('urls', () => {
  it('sends the browser zone as tz on every Scope route and on the session route (QRY-3, API-3, FE-9 [D50])', () => {
    const all = [
      urls.explorerSummary(SCOPE), urls.explorerDays(SCOPE, { from: '2026-09-22', to: '2026-10-12' }), urls.explorerSessions(SCOPE),
      urls.explorerFacets(SCOPE), urls.palette('x', SCOPE), urls.sessionDetail('s'),
    ]
    for (const u of all) expect(new URL(u, 'http://x').searchParams.get('tz'), u).toBe('Asia/Kolkata')
  })

  it('sorts the query so equal requests have equal URLs', () => {
    const a = urls.explorerSummary({ ...SCOPE, q: 'x', providers: ['omp'] })
    const b = urls.explorerSummary({ ...SCOPE, providers: ['omp'], q: 'x' })
    expect(a).toBe(b)
    expect(a).toBe('/api/explorer/summary?from=2026-10-06&provider=omp&q=x&to=2026-10-12&tz=Asia%2FKolkata')
  })

  it('keeps the order of repeated values when it sorts', () => {
    expect(urls.explorerSummary({ ...SCOPE, models: ['z', 'a'] })).toContain('model=z&model=a')
  })

  it('sends the chart window instead of the period for days (API-11), and bucket=week only for weeks', () => {
    expect(urls.explorerDays(SCOPE, { from: '2026-09-22', to: '2026-10-12' })).toBe(
      '/api/explorer/days?tz=Asia%2FKolkata&window_from=2026-09-22&window_to=2026-10-12',
    )
    expect(urls.explorerDays({ ...SCOPE, q: 'x' }, { from: '2026-01-05', to: '2026-10-12', bucket: 'week' })).toBe(
      '/api/explorer/days?bucket=week&q=x&tz=Asia%2FKolkata&window_from=2026-01-05&window_to=2026-10-12',
    )
    expect(urls.explorerDays(SCOPE, { from: 'a', to: 'b', bucket: 'day' })).not.toContain('bucket')
  })

  it('omits session-list parameters at their defaults', () => {
    expect(urls.explorerSessions(SCOPE, {})).toBe('/api/explorer/sessions?from=2026-10-06&to=2026-10-12&tz=Asia%2FKolkata')
    expect(
      urls.explorerSessions(SCOPE, { sort: 'start', dir: 'desc', offset: 0, limit: 200, group_limit: 5 }),
    ).toBe('/api/explorer/sessions?from=2026-10-06&to=2026-10-12&tz=Asia%2FKolkata')
  })

  it('sends session-list parameters that differ from the defaults', () => {
    const u = urls.explorerSessions(SCOPE, { sort: 'cost', dir: 'asc', offset: 200, limit: 6, group: 'project', group_key: '/a', group_limit: 20 })
    const p = new URL(u, 'http://x').searchParams
    expect(Object.fromEntries(p)).toMatchObject({ sort: 'cost', dir: 'asc', offset: '200', limit: '6', group: 'project', group_key: '/a', group_limit: '20' })
  })

  it('puts the palette text next to the scope', () => {
    const p = new URL(urls.palette('model:opus', SCOPE), 'http://x').searchParams
    expect(p.get('text')).toBe('model:opus')
    expect(p.get('from')).toBe('2026-10-06')
  })

  it('encodes op ids with / and # in the path', () => {
    const op = 'claude_code/a11ce5e5-1111/session/-'
    expect(urls.sessionDetail(op)).toBe('/api/sessions/claude_code%2Fa11ce5e5-1111%2Fsession%2F-?tz=Asia%2FKolkata')
    expect(urls.turnRows('claude_code/u/turn/2#3')).toBe('/api/turns/claude_code%2Fu%2Fturn%2F2%233/rows')
    expect(urls.opDetail('a/b#c')).toBe('/api/ops/a%2Fb%23c/detail')
    expect(urls.agentRows('a/b#c', {})).toBe('/api/ops/a%2Fb%23c/rows')
    expect(urls.sessionReview('a/b')).toBe('/api/sessions/a%2Fb/review')
    expect(urls.sessionTurns('a/b')).toBe('/api/sessions/a%2Fb/turns')
    expect(urls.sessionHistogram('a/b')).toBe('/api/sessions/a%2Fb/histogram')
    expect(urls.turnHistogram('a#b')).toBe('/api/turns/a%23b/histogram')
  })

  it('encodes ids and finder text in the query', () => {
    expect(urls.sessionResolve('s', 'gap-1')).toBe('/api/sessions/s/resolve?id=gap-1')
    expect(urls.sessionResolve('s', 'a/b#c')).toBe('/api/sessions/s/resolve?id=a%2Fb%23c')
    expect(urls.sessionFind('s', { kind: 'search', text: 'a&b c' })).toBe('/api/sessions/s/find?kind=search&text=a%26b+c')
  })

  it('omits finder defaults: measure cost and limit 2000', () => {
    expect(urls.sessionFind('s', { kind: 'biggest', measure: 'cost', limit: 2000 })).toBe('/api/sessions/s/find?kind=biggest')
    expect(urls.sessionFind('s', { kind: 'biggest', measure: 'time', limit: 50 })).toBe('/api/sessions/s/find?kind=biggest&limit=50&measure=time')
  })

  it('passes part to source, and drops offset 0 and the default limit', () => {
    expect(urls.source('src://claude_code/p/f.jsonl#L12', { part: 'output' })).toBe(
      '/api/source?part=output&src=src%3A%2F%2Fclaude_code%2Fp%2Ff.jsonl%23L12',
    )
    const p = new URL(urls.source('x', { part: 'thinking', offset: 100, limit: 262144 }), 'http://x').searchParams
    expect(p.get('part')).toBe('thinking')
    expect(p.get('offset')).toBe('100')
    expect(p.get('limit')).toBe('262144')
  })

  it('sends the turn range only when brushed, and min_gap_ms only when not the default', () => {
    expect(urls.turnRows('t')).toBe('/api/turns/t/rows')
    expect(urls.turnRows('t', { from_ms: 10, to_ms: 20 })).toBe('/api/turns/t/rows?from_ms=10&to_ms=20')
    expect(urls.sessionTurns('s', 120000)).toBe('/api/sessions/s/turns')
    expect(urls.sessionTurns('s', 60000)).toBe('/api/sessions/s/turns?min_gap_ms=60000')
  })

  it('sends histogram parameters that are set', () => {
    expect(urls.turnHistogram('t', { bins: 120, from_ms: 1, to_ms: 2 })).toBe('/api/turns/t/histogram?bins=120&from_ms=1&to_ms=2')
  })

  it('has the status URL without a query', () => {
    expect(urls.ingestStatus()).toBe('/api/ingest/status')
  })
})

describe('client requests', () => {
  it('fetches the URL and returns the typed body', async () => {
    fetchMock.mockResolvedValue(json(fixtures.explorerFacets))
    const body = await client.explorerFacets(SCOPE)
    expect(fetchMock.mock.calls[0][0]).toBe(urls.explorerFacets(SCOPE))
    expect(body.automated.n).toBe(12)
  })

  it('passes the AbortSignal to fetch', async () => {
    fetchMock.mockResolvedValue(json(fixtures.sessionDetail))
    const ac = new AbortController()
    await client.sessionDetail('s', ac.signal)
    expect(fetchMock.mock.calls[0][1].signal).toBe(ac.signal)
  })

  it('has a method for every endpoint the UI uses', () => {
    expect(Object.keys(client).sort()).toEqual([
      'agentRows', 'explorerDays', 'explorerFacets', 'explorerSessions', 'explorerSummary', 'ingestStatus', 'opDetail',
      'palette', 'sessionDetail', 'sessionFind', 'sessionHistogram', 'sessionResolve', 'sessionReview', 'sessionTurns', 'source',
      'turnHistogram', 'turnRows',
    ])
  })
})

describe('errors (API-4, FE-8)', () => {
  it('turns an API error body into an ApiError with status, code and the server text', async () => {
    fetchMock.mockResolvedValue(json({ error: 'bad day: 2026-13-01', code: 'bad_request' }, { status: 400 }))
    const e = await getJson('/x').catch((err: unknown) => err)
    expect(e).toBeInstanceOf(ApiError)
    expect(e).toMatchObject({ status: 400, code: 'bad_request', message: 'bad day: 2026-13-01' })
    expect(classifyError(e)).toBe('failed')
  })

  it('maps 503 no_store and 503 schema_mismatch (FE-8)', async () => {
    fetchMock.mockResolvedValueOnce(json({ error: 'no store at x', code: 'no_store' }, { status: 503 }))
    expect(classifyError(await getJson('/x').catch((e: unknown) => e))).toBe('no_store')
    fetchMock.mockResolvedValueOnce(json({ error: 'store schema 6', code: 'schema_mismatch' }, { status: 503 }))
    expect(classifyError(await getJson('/x').catch((e: unknown) => e))).toBe('schema_mismatch')
  })

  it('maps another 503 to a plain failure', async () => {
    fetchMock.mockResolvedValue(json({ error: 'busy', code: 'internal' }, { status: 503 }))
    expect(classifyError(await getJson('/x').catch((e: unknown) => e))).toBe('failed')
  })

  it('handles an error response that is not JSON', async () => {
    fetchMock.mockResolvedValue(new Response('<html>Bad gateway</html>', { status: 502, headers: { 'content-type': 'text/html' } }))
    const e = await getJson('/x').catch((err: unknown) => err)
    expect(e).toBeInstanceOf(ApiError)
    expect(e).toMatchObject({ status: 502, code: 'bad_response' })
  })

  it('handles an error response whose JSON body is malformed', async () => {
    fetchMock.mockResolvedValue(new Response('{oops', { status: 500, headers: { 'content-type': 'application/json' } }))
    expect(await getJson('/x').catch((err: unknown) => err)).toMatchObject({ status: 500, code: 'bad_response' })
  })

  it('gives a JSON error body without a code the code bad_response', async () => {
    fetchMock.mockResolvedValue(json({ error: 'boom' }, { status: 500 }))
    expect(await getJson('/x').catch((err: unknown) => err)).toMatchObject({ status: 500, code: 'bad_response', message: 'boom' })
  })

  it('refuses a 200 that is not JSON', async () => {
    fetchMock.mockResolvedValue(new Response('<html>', { status: 200, headers: { 'content-type': 'text/html' } }))
    expect(await getJson('/x').catch((err: unknown) => err)).toMatchObject({ status: 200, code: 'bad_response' })
  })

  it('turns a rejected fetch into NetError (server unreachable)', async () => {
    fetchMock.mockRejectedValue(new TypeError('Failed to fetch'))
    const e = await getJson('/x').catch((err: unknown) => err)
    expect(e).toBeInstanceOf(NetError)
    expect(classifyError(e)).toBe('unreachable')
  })

  it('passes an abort through unchanged', async () => {
    const abort = new DOMException('aborted', 'AbortError')
    fetchMock.mockRejectedValue(abort)
    const e = await getJson('/x').catch((err: unknown) => err)
    expect(e).toBe(abort)
    expect(classifyError(e)).toBe('aborted')
  })

  it('rejects an error of another kind in classifyError', () => {
    expect(() => classifyError(new RangeError('bug'))).toThrow()
  })
})

describe('conditional requests (API-6, FE-4)', () => {
  it('sends If-None-Match for a URL it has an ETag for, and reuses the body on 304', async () => {
    fetchMock.mockResolvedValueOnce(json({ n: 1 }, { etag: 'W/"1.7.100.p.tz"' }))
    expect(await getJson('/api/a')).toEqual({ n: 1 })
    expect(fetchMock.mock.calls[0][1].headers['if-none-match']).toBeUndefined()

    fetchMock.mockResolvedValueOnce(new Response(null, { status: 304 }))
    expect(await getJson('/api/a')).toEqual({ n: 1 })
    expect(fetchMock.mock.calls[1][1].headers['if-none-match']).toBe('W/"1.7.100.p.tz"')
  })

  it('replaces the stored body when a later 200 arrives', async () => {
    fetchMock.mockResolvedValueOnce(json({ n: 1 }, { etag: 'e1' }))
    await getJson('/api/a')
    fetchMock.mockResolvedValueOnce(json({ n: 2 }, { etag: 'e2' }))
    expect(await getJson('/api/a')).toEqual({ n: 2 })
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 304 }))
    expect(await getJson('/api/a')).toEqual({ n: 2 })
    expect(fetchMock.mock.calls[2][1].headers['if-none-match']).toBe('e2')
  })

  it('does not send If-None-Match for a response that had no ETag (no-store routes)', async () => {
    fetchMock.mockImplementation(() => Promise.resolve(json({ n: 1 })))
    await getJson('/api/ingest/status')
    await getJson('/api/ingest/status')
    expect(fetchMock.mock.calls[1][1].headers['if-none-match']).toBeUndefined()
  })

  it('forgets the stored copy when the ETag disappears (an ingest started)', async () => {
    fetchMock.mockResolvedValueOnce(json({ n: 1 }, { etag: 'e1' }))
    await getJson('/api/a')
    fetchMock.mockResolvedValueOnce(json({ n: 2 }))
    await getJson('/api/a')
    fetchMock.mockResolvedValueOnce(json({ n: 3 }))
    await getJson('/api/a')
    expect(fetchMock.mock.calls[2][1].headers['if-none-match']).toBeUndefined()
  })

  it('rejects a 304 it cannot match to a stored body', async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 304 }))
    expect(await getJson('/api/never').catch((e: unknown) => e)).toBeInstanceOf(ApiError)
  })

  it('clearEtags drops every stored copy', async () => {
    fetchMock.mockResolvedValueOnce(json({ n: 1 }, { etag: 'e1' }))
    await getJson('/api/a')
    clearEtags()
    fetchMock.mockResolvedValueOnce(json({ n: 1 }, { etag: 'e1' }))
    await getJson('/api/a')
    expect(fetchMock.mock.calls[1][1].headers['if-none-match']).toBeUndefined()
  })
})
