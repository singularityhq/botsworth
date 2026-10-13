import { browserZone } from '../state/zone'
import { ApiError, NetError, isAbort } from './errors'
import type {
  AgentRowPage, Day, Detail, DaySeries, Facets, FindKind, FindMeasure, FindResult, GroupKey, Histogram,
  IngestStatus, PaletteResult, PeriodSummary, Resolved, Scope, SessionDetail, SessionPage, SortDir, SortKey,
  SourcePart, SourceResponse, TurnList, TurnRows,
} from './types'

/**
 * The typed HTTP client for every interfaces.md §3.2 endpoint the UI uses
 * (FE-2). `urls` builds the request URL of each endpoint; `useApi` keys on
 * that same string (FE-4), and `client` fetches it. There is one
 * implementation: no mock lives in the product (FE-3).
 */

const enc = encodeURIComponent

/** Path plus the query string with keys sorted, so equal requests have equal URLs (FE-4). */
function build(path: string, params?: URLSearchParams): string {
  if (!params) return path
  params.sort()
  const s = params.toString()
  return s ? `${path}?${s}` : path
}

/** Scope → query parameters (API-3). Lists repeat the key; defaults are omitted. `tz` is always sent: the browser zone (FE-9 [D50]). */
export function scopeParams(scope: Scope): URLSearchParams {
  if ((scope.from_day === null) !== (scope.to_day === null)) {
    throw new Error('scopeParams: from_day and to_day must both be set or both be null (QRY-3)')
  }
  const p = new URLSearchParams({ tz: browserZone() })
  if (scope.from_day !== null && scope.to_day !== null) {
    p.set('from', scope.from_day)
    p.set('to', scope.to_day)
  }
  if (scope.q !== '') p.set('q', scope.q)
  for (const v of scope.providers) p.append('provider', v)
  for (const v of scope.models) p.append('model', v)
  for (const v of scope.branches) p.append('branch', v)
  for (const v of scope.projects) p.append('project', v)
  if (scope.show_automated) p.set('auto', 'show')
  return p
}

function setIf(p: URLSearchParams, key: string, value: string | number | undefined, dflt?: string | number): void {
  if (value === undefined || value === dflt) return
  p.set(key, String(value))
}

export interface DaysWindow {
  from: Day
  to: Day
  /** 'week' for periods over 92 days (D41 M10); 'day' is the default and is omitted. */
  bucket?: 'day' | 'week'
}

export interface SessionListOpts {
  sort?: SortKey
  dir?: SortDir
  offset?: number
  limit?: number
  group?: GroupKey
  group_key?: string
  group_limit?: number
}

export interface HistogramOpts {
  bins?: number
  from_ms?: number
  to_ms?: number
}

export interface FindOpts {
  kind: FindKind
  measure?: FindMeasure
  text?: string
  limit?: number
}

export interface SourceOpts {
  part: SourcePart
  offset?: number
  limit?: number
}

/** Request URL of each endpoint. Pure; the hook key for an endpoint is its URL. */
export const urls = {
  ingestStatus: (): string => '/api/ingest/status',
  explorerSummary: (scope: Scope): string => build('/api/explorer/summary', scopeParams(scope)),
  explorerDays: (scope: Scope, w: DaysWindow): string => {
    // API-11: the window replaces the period, so from/to are not sent.
    const p = scopeParams({ ...scope, from_day: null, to_day: null })
    p.set('window_from', w.from)
    p.set('window_to', w.to)
    if (w.bucket === 'week') p.set('bucket', 'week')
    return build('/api/explorer/days', p)
  },
  explorerSessions: (scope: Scope, o: SessionListOpts = {}): string => {
    const p = scopeParams(scope)
    setIf(p, 'sort', o.sort, 'start')
    setIf(p, 'dir', o.dir, 'desc')
    setIf(p, 'offset', o.offset, 0)
    setIf(p, 'limit', o.limit, 200)
    setIf(p, 'group', o.group)
    setIf(p, 'group_key', o.group_key)
    setIf(p, 'group_limit', o.group_limit, 5)
    return build('/api/explorer/sessions', p)
  },
  explorerFacets: (scope: Scope): string => build('/api/explorer/facets', scopeParams(scope)),
  palette: (text: string, scope: Scope): string => {
    const p = scopeParams(scope)
    p.set('text', text)
    return build('/api/palette', p)
  },
  sessionDetail: (session: string): string => build(`/api/sessions/${enc(session)}`, new URLSearchParams({ tz: browserZone() })),
  sessionTurns: (session: string, minGapMs?: number): string => {
    const p = new URLSearchParams()
    setIf(p, 'min_gap_ms', minGapMs, 120000)
    return build(`/api/sessions/${enc(session)}/turns`, p)
  },
  sessionFind: (session: string, o: FindOpts): string => {
    const p = new URLSearchParams({ kind: o.kind })
    setIf(p, 'measure', o.measure, 'cost')
    setIf(p, 'text', o.text)
    setIf(p, 'limit', o.limit, 2000)
    return build(`/api/sessions/${enc(session)}/find`, p)
  },
  sessionHistogram: (session: string, o: HistogramOpts = {}): string =>
    build(`/api/sessions/${enc(session)}/histogram`, histogramParams(o)),
  sessionResolve: (session: string, id: string): string =>
    build(`/api/sessions/${enc(session)}/resolve`, new URLSearchParams({ id })),
  turnHistogram: (turn: string, o: HistogramOpts = {}): string =>
    build(`/api/turns/${enc(turn)}/histogram`, histogramParams(o)),
  turnRows: (turn: string, range?: { from_ms: number; to_ms: number }): string => {
    const p = new URLSearchParams()
    if (range) {
      p.set('from_ms', String(range.from_ms))
      p.set('to_ms', String(range.to_ms))
    }
    return build(`/api/turns/${enc(turn)}/rows`, p)
  },
  agentRows: (engagement: string, o: { offset?: number; limit?: number } = {}): string => {
    const p = new URLSearchParams()
    setIf(p, 'offset', o.offset, 0)
    setIf(p, 'limit', o.limit, 500)
    return build(`/api/ops/${enc(engagement)}/rows`, p)
  },
  opDetail: (op: string): string => `/api/ops/${enc(op)}/detail`,
  source: (src: string, o: SourceOpts): string => {
    const p = new URLSearchParams({ src, part: o.part })
    setIf(p, 'offset', o.offset, 0)
    setIf(p, 'limit', o.limit, 65536)
    return build('/api/source', p)
  },
}

function histogramParams(o: HistogramOpts): URLSearchParams {
  const p = new URLSearchParams()
  setIf(p, 'bins', o.bins)
  setIf(p, 'from_ms', o.from_ms)
  setIf(p, 'to_ms', o.to_ms)
  return p
}

// ---- transport ----------------------------------------------------------

const ETAG_LIMIT = 200
/** Last 200 bodies with an ETag, by URL, for conditional requests (API-6). */
const etags = new Map<string, { etag: string; body: unknown }>()

/** Drop every stored ETag; called when the store changed (FE-7). */
export function clearEtags(): void {
  etags.clear()
}

function rememberEtag(url: string, etag: string, body: unknown): void {
  etags.delete(url)
  etags.set(url, { etag, body })
  if (etags.size > ETAG_LIMIT) etags.delete(etags.keys().next().value as string)
}

async function readJson(res: Response): Promise<{ ok: true; body: unknown } | { ok: false }> {
  const type = res.headers.get('content-type') ?? ''
  if (!type.includes('application/json')) return { ok: false }
  try {
    return { ok: true, body: await res.json() }
  } catch (e) {
    if (isAbort(e)) throw e
    return { ok: false }
  }
}

/**
 * GET `url` and return the parsed body. Sends If-None-Match when it holds an
 * ETag for the URL and reuses the stored body on 304 (API-6, FE-4).
 * Failures are ApiError, NetError or AbortError (FE-8).
 */
export async function getJson<T>(url: string, signal?: AbortSignal): Promise<T> {
  const known = etags.get(url)
  const headers: Record<string, string> = { accept: 'application/json' }
  if (known) headers['if-none-match'] = known.etag
  let res: Response
  try {
    res = await fetch(url, { signal, headers })
  } catch (e) {
    if (isAbort(e)) throw e
    // fetch rejects with TypeError only when the network request fails (FE-8 "server unreachable").
    if (e instanceof TypeError) throw new NetError(`Couldn't reach the Botsworth server: ${e.message}`)
    throw e
  }
  if (res.status === 304) {
    if (!known) throw new ApiError(304, 'bad_response', `${url}: 304 without a stored copy`)
    return known.body as T
  }
  const parsed = await readJson(res)
  if (!res.ok) {
    if (parsed.ok && typeof parsed.body === 'object' && parsed.body !== null) {
      const b = parsed.body as { error?: unknown; code?: unknown }
      // API-4: error bodies are {"error", "code"}; a missing field is a malformed error body.
      const message = typeof b.error === 'string' ? b.error : `HTTP ${res.status}`
      const code = typeof b.code === 'string' ? b.code : 'bad_response' // FALLBACK-OK: interfaces.md API-4 (code is always present; absence means a non-conforming server)
      throw new ApiError(res.status, code, message)
    }
    throw new ApiError(res.status, 'bad_response', `HTTP ${res.status}, response is not JSON`)
  }
  if (!parsed.ok) throw new ApiError(res.status, 'bad_response', `${url}: HTTP ${res.status}, body is not JSON`)
  const etag = res.headers.get('etag')
  if (etag) rememberEtag(url, etag, parsed.body)
  else etags.delete(url)
  return parsed.body as T
}

// ---- endpoints ----------------------------------------------------------

/** One method per UI endpoint (interfaces §3.2). The AbortSignal is always last. */
export const client = {
  ingestStatus: (signal?: AbortSignal) => getJson<IngestStatus>(urls.ingestStatus(), signal),
  explorerSummary: (scope: Scope, signal?: AbortSignal) => getJson<PeriodSummary>(urls.explorerSummary(scope), signal),
  explorerDays: (scope: Scope, w: DaysWindow, signal?: AbortSignal) =>
    getJson<DaySeries>(urls.explorerDays(scope, w), signal),
  explorerSessions: (scope: Scope, o: SessionListOpts, signal?: AbortSignal) =>
    getJson<SessionPage>(urls.explorerSessions(scope, o), signal),
  explorerFacets: (scope: Scope, signal?: AbortSignal) => getJson<Facets>(urls.explorerFacets(scope), signal),
  palette: (text: string, scope: Scope, signal?: AbortSignal) => getJson<PaletteResult>(urls.palette(text, scope), signal),
  sessionDetail: (session: string, signal?: AbortSignal) => getJson<SessionDetail>(urls.sessionDetail(session), signal),
  sessionTurns: (session: string, minGapMs: number | undefined, signal?: AbortSignal) =>
    getJson<TurnList>(urls.sessionTurns(session, minGapMs), signal),
  sessionFind: (session: string, o: FindOpts, signal?: AbortSignal) =>
    getJson<FindResult>(urls.sessionFind(session, o), signal),
  sessionHistogram: (session: string, o: HistogramOpts, signal?: AbortSignal) =>
    getJson<Histogram>(urls.sessionHistogram(session, o), signal),
  sessionResolve: (session: string, id: string, signal?: AbortSignal) =>
    getJson<Resolved>(urls.sessionResolve(session, id), signal),
  turnHistogram: (turn: string, o: HistogramOpts, signal?: AbortSignal) =>
    getJson<Histogram>(urls.turnHistogram(turn, o), signal),
  turnRows: (turn: string, range: { from_ms: number; to_ms: number } | undefined, signal?: AbortSignal) =>
    getJson<TurnRows>(urls.turnRows(turn, range), signal),
  agentRows: (engagement: string, o: { offset?: number; limit?: number }, signal?: AbortSignal) =>
    getJson<AgentRowPage>(urls.agentRows(engagement, o), signal),
  opDetail: (op: string, signal?: AbortSignal) => getJson<Detail>(urls.opDetail(op), signal),
  source: (src: string, o: SourceOpts, signal?: AbortSignal) => getJson<SourceResponse>(urls.source(src, o), signal),
}

export type Client = typeof client
