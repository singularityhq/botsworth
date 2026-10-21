import { useEffect, useSyncExternalStore } from 'react'
import type { Day, GroupKey, Provider, Scope, SortDir, SortKey } from '../api/types'
import { isDay } from '../lib/day'
import { DEFAULT_PERIOD, PRESETS, resolvePeriod, type Period, type Preset } from './period'

/**
 * One schema for the whole query string: the explorer's scope and sort
 * (EXP-74) and the session, turn and detail blades (SES-8). `parseUrl` drops
 * unknown values (EXP-75, SES-11); `serializeUrl` omits defaults.
 * The blade list is derived from the string, never stored (architecture
 * "Routing and state").
 */

export type Measure = 'cost' | 'tokens'
export type Bars = 'cost' | 'time' | 'tokens'
export type ViewMode = 'ledger' | 'waterfall'
/** SES-8 [D56]: the session blade adds `review`. */
export type SessionView = ViewMode | 'review'
export type TurnOrder = 'cost' | 'duration'
export interface MsRange {
  start_ms: number
  end_ms: number
}

export interface UrlState {
  // explorer (EXP-74)
  period: Period
  q: string
  providers: Provider[]
  models: string[]
  branches: string[]
  projects: string[]
  auto: boolean
  measure: Measure
  sort: SortKey
  dir: SortDir
  group: GroupKey | null
  // session blade (SES-8)
  s: string | null
  bars: Bars
  find: string
  range: MsRange | null
  over: boolean
  sview: SessionView
  sreal: boolean
  info: boolean
  // turn blade
  t: number | null
  step: string | null
  view: ViewMode
  order: TurnOrder | null
  real: boolean
  zoom: MsRange | null
  thist: boolean | null
  tinfo: boolean
  // detail blade
  detail: boolean
}

export const DEFAULT_URL_STATE: UrlState = {
  period: DEFAULT_PERIOD,
  q: '',
  providers: [],
  models: [],
  branches: [],
  projects: [],
  auto: false,
  measure: 'cost',
  sort: 'start',
  dir: 'desc',
  group: null,
  s: null,
  bars: 'cost',
  find: '',
  range: null,
  over: false,
  sview: 'ledger',
  sreal: false,
  info: false,
  t: null,
  step: null,
  view: 'ledger',
  order: null,
  real: false,
  zoom: null,
  thist: null,
  tinfo: false,
  detail: false,
}

export type BladeLevel = 'explorer' | 'session' | 'turn' | 'detail'

/** Parameters each blade owns; closing or reopening a blade resets them. `bars` outlives its blades. */
export const LEVEL_PARAMS: Record<Exclude<BladeLevel, 'explorer'>, readonly (keyof UrlState)[]> = {
  session: ['s', 'find', 'range', 'over', 'sview', 'sreal', 'info'],
  turn: ['t', 'step', 'view', 'order', 'real', 'zoom', 'thist', 'tinfo'],
  detail: ['detail'],
}

const PROVIDERS: readonly Provider[] = ['claude_code', 'codex', 'omp']
const SORTS: readonly SortKey[] = ['start', 'cost', 'wall', 'active', 'tokens']
const PRESET_IDS: readonly Preset[] = PRESETS.map((p) => p.id)

function oneOf<T extends string>(value: string | null, allowed: readonly T[]): T | null {
  return value !== null && (allowed as readonly string[]).includes(value) ? (value as T) : null
}

function unique(values: string[]): string[] {
  return [...new Set(values.filter((v) => v !== ''))]
}

function parseRange(value: string | null): MsRange | null {
  const m = value === null ? null : /^(\d+)-(\d+)$/.exec(value)
  if (!m) return null
  const [start_ms, end_ms] = [Number(m[1]), Number(m[2])]
  return start_ms < end_ms ? { start_ms, end_ms } : null
}

function parsePeriod(p: URLSearchParams): Period {
  const from = p.get('from')
  const to = p.get('to')
  if (from !== null && to !== null && isDay(from) && isDay(to) && from <= to) return { kind: 'custom', from, to }
  const preset = oneOf(p.get('period'), PRESET_IDS)
  return preset ? { kind: 'preset', preset } : DEFAULT_PERIOD
}

/**
 * Rules that tie parameters together: without a session there is no blade,
 * so every blade parameter returns to its default.
 */
export function normalizeUrlState(state: UrlState): UrlState {
  if (state.s !== null) return state
  const next = { ...state }
  for (const level of ['session', 'turn', 'detail'] as const) {
    for (const key of LEVEL_PARAMS[level]) (next as Record<string, unknown>)[key] = DEFAULT_URL_STATE[key]
  }
  return next
}

/** Query string (with or without the leading `?`) → state. */
export function parseUrl(search: string): UrlState {
  const p = new URLSearchParams(search)
  const tText = p.get('t')
  const t = tText !== null && /^\d+$/.test(tText) && Number(tText) >= 1 ? Number(tText) : null
  const thist = p.get('thist')
  // FALLBACK-OK: explorer.md EXP-74/EXP-75 and session.md SES-8/SES-11 (an absent or unknown parameter takes its default)
  const state: UrlState = {
    period: parsePeriod(p),
    q: p.get('q') ?? '',
    providers: unique(p.getAll('provider')).filter((v): v is Provider => (PROVIDERS as readonly string[]).includes(v)),
    models: unique(p.getAll('model')),
    branches: unique(p.getAll('branch')),
    projects: unique(p.getAll('project')),
    auto: p.get('auto') === 'show',
    measure: oneOf(p.get('measure'), ['cost', 'tokens'] as const) ?? 'cost',
    sort: oneOf(p.get('sort'), SORTS) ?? 'start',
    dir: oneOf(p.get('dir'), ['asc', 'desc'] as const) ?? 'desc',
    group: oneOf(p.get('group'), ['project', 'day'] as const),
    s: p.get('s') || null,
    bars: oneOf(p.get('bars'), ['cost', 'time', 'tokens'] as const) ?? 'cost',
    find: p.get('find') ?? '',
    range: parseRange(p.get('range')),
    over: p.get('over') === '1',
    sview: oneOf(p.get('sview'), ['waterfall', 'review'] as const) ?? 'ledger',
    sreal: p.get('sreal') === '1',
    info: p.get('info') === '1',
    t,
    step: p.get('step') || null,
    view: p.get('view') === 'waterfall' ? 'waterfall' : 'ledger',
    order: oneOf(p.get('order'), ['cost', 'duration'] as const),
    real: p.get('real') === '1',
    zoom: parseRange(p.get('zoom')),
    thist: thist === '0' ? false : thist === '1' ? true : null,
    tinfo: p.get('tinfo') === '1',
    detail: p.get('detail') === '1',
  }
  return normalizeUrlState(state)
}

/** State → query string without the leading `?`. Defaults are omitted; the order is fixed. */
export function serializeUrl(input: UrlState): string {
  const u = normalizeUrlState(input)
  const p = new URLSearchParams()
  if (u.period.kind === 'custom') {
    p.set('from', u.period.from)
    p.set('to', u.period.to)
  } else if (u.period.preset !== 'last7') {
    p.set('period', u.period.preset)
  }
  if (u.q !== '') p.set('q', u.q)
  for (const v of u.providers) p.append('provider', v)
  for (const v of u.models) p.append('model', v)
  for (const v of u.branches) p.append('branch', v)
  for (const v of u.projects) p.append('project', v)
  if (u.auto) p.set('auto', 'show')
  if (u.measure !== 'cost') p.set('measure', u.measure)
  if (u.sort !== 'start') p.set('sort', u.sort)
  if (u.dir !== 'desc') p.set('dir', u.dir)
  if (u.group !== null) p.set('group', u.group)
  if (u.s !== null) p.set('s', u.s)
  if (u.bars !== 'cost') p.set('bars', u.bars)
  if (u.find !== '') p.set('find', u.find)
  if (u.range) p.set('range', `${u.range.start_ms}-${u.range.end_ms}`)
  if (u.over) p.set('over', '1')
  if (u.sview !== 'ledger') p.set('sview', u.sview)
  if (u.sreal) p.set('sreal', '1')
  if (u.info) p.set('info', '1')
  if (u.t !== null) p.set('t', String(u.t))
  if (u.step !== null) p.set('step', u.step)
  if (u.view !== 'ledger') p.set('view', u.view)
  if (u.order !== null) p.set('order', u.order)
  if (u.real) p.set('real', '1')
  if (u.zoom) p.set('zoom', `${u.zoom.start_ms}-${u.zoom.end_ms}`)
  if (u.thist !== null) p.set('thist', u.thist ? '1' : '0')
  if (u.tinfo) p.set('tinfo', '1')
  if (u.detail) p.set('detail', '1')
  return p.toString()
}

/** The blades the URL asks for, left to right (SYS-45; architecture "Routing and state"). */
export function openBlades(u: UrlState): BladeLevel[] {
  const out: BladeLevel[] = ['explorer']
  if (u.s !== null) out.push('session')
  if (u.s !== null && u.t !== null) out.push('turn')
  if (u.s !== null && u.detail) out.push('detail')
  return out
}

/** The explorer's request scope for `state` (QRY-3), with the period resolved against `today`. */
export function toScope(u: UrlState, today: Day): Scope {
  const r = resolvePeriod(u.period, today)
  return {
    from_day: r.from,
    to_day: r.to,
    q: u.q,
    providers: u.providers,
    models: u.models,
    branches: u.branches,
    projects: u.projects,
    show_automated: u.auto,
  }
}

// ---- location access -----------------------------------------------------

/** Where the URL lives. The browser implementation is `browserUrl`; tests pass a fake. */
export interface UrlEnv {
  /** `location.search`, with the leading `?` when not empty. */
  getSearch(): string
  /** Add a history entry whose query is `query` (no leading `?`), tagged with `state`. */
  push(query: string, state?: unknown): void
  /** Rewrite the current entry; its state tag is kept. */
  replace(query: string): void
  /** The state tag of the current entry. */
  getState(): unknown
  /** One step back in history. The new URL arrives later as a change to `subscribe` listeners. */
  back(): void
  subscribe(listener: () => void): () => void
}

const listeners = new Set<() => void>()
let popstateBound = false

function notify(): void {
  for (const l of listeners) l()
}

function pathWith(query: string): string {
  return window.location.pathname + (query === '' ? '' : `?${query}`)
}

export const browserUrl: UrlEnv = {
  getSearch: () => window.location.search,
  push(query, state) {
    window.history.pushState(state ?? null, '', pathWith(query))
    notify()
  },
  replace(query) {
    window.history.replaceState(window.history.state, '', pathWith(query))
    notify()
  },
  getState: () => window.history.state,
  back: () => window.history.back(), // popstate follows and notifies
  subscribe(listener) {
    if (!popstateBound) {
      window.addEventListener('popstate', notify)
      popstateBound = true
    }
    listeners.add(listener)
    return () => listeners.delete(listener)
  },
}

const parsed = new Map<string, UrlState>()

/** Parse once per distinct query string, so the snapshot is stable between renders. */
function parseCached(search: string): UrlState {
  let s = parsed.get(search)
  if (!s) {
    if (parsed.size > 50) parsed.clear()
    s = parseUrl(search)
    parsed.set(search, s)
  }
  return s
}

/**
 * The current URL as state. Rewrites the URL once when it holds unknown
 * values or non-canonical text (EXP-75).
 */
export function useUrlState(env: UrlEnv = browserUrl): UrlState {
  const search = useSyncExternalStore(env.subscribe, env.getSearch, () => '')
  const state = parseCached(search)
  useEffect(() => {
    const canonical = serializeUrl(state)
    if (canonical !== search.replace(/^\?/, '')) env.replace(canonical)
  }, [env, search, state])
  return state
}
