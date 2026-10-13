import type { Day, PaletteResult, Provider, SessionRow } from '../../../api/types'
import { fmt } from '../../../lib/format'
import { addDays } from '../../../lib/day'
import { parseDateRows, PRESETS, resolvePeriod, type Period, type Preset } from '../../../state/period'
import type { UrlState } from '../../../state/url'

/**
 * What the ⌘K palette shows and does (EXP-67 to EXP-73): the sections for empty and typed input, and the action each row runs.
 * The server finds filter values, projects and sessions (API-14); dates are parsed here (FE-10).
 */

export type FilterKind = 'model' | 'branch' | 'provider' | 'project'

/** EXP-68 "Filter by…": the four prefixes with an ink3 example, in this order. */
export const FILTER_PREFIXES: readonly { kind: FilterKind; example: string }[] = [
  { kind: 'model', example: 'model:opus' },
  { kind: 'branch', example: 'branch:main' },
  { kind: 'provider', example: 'provider:codex' },
  { kind: 'project', example: 'project:bizdemo' },
]

type ServerProject = PaletteResult['projects'][number]

/** The session fields a palette row needs. API-14 sessions carry all of them; the "Largest" rows come from API-12 and are in period and in filters by construction. */
export interface PaletteSession {
  session: string
  session_id: string
  provider: Provider
  name: string
  title_source: SessionRow['title_source']
  project: string
  project_name: string
  color_slot: SessionRow['color_slot']
  start_ms: number
  cost_usd: number | null
  cost_lower_bound: boolean
  n_unpriced: number
  is_automated: boolean
  in_period: boolean
  in_filters: boolean
  first_day: Day | null
}

export function fromSessionRow(r: SessionRow): PaletteSession {
  return {
    session: r.session, session_id: r.session_id, provider: r.provider, name: r.name, title_source: r.title_source,
    project: r.project, project_name: r.project_name, color_slot: r.color_slot, start_ms: r.start_ms, cost_usd: r.cost_usd,
    cost_lower_bound: r.cost_lower_bound, n_unpriced: r.n_unpriced, is_automated: r.is_automated,
    in_period: true, in_filters: true, first_day: null,
  }
}

export const plural = (n: number, one: string) => `${n} ${n === 1 ? one : `${one}s`}`

/** EXP-70: `19 sessions · $559.00 · 1.93B tokens`. A figure that is not in the data (null tokens) is left out. */
export function factsText(n: number, cost: number | null, lowerBound: boolean, tok: number | null): string {
  const money = fmt.money(cost, { lowerBound })
  return [plural(n, 'session'), money, tok === null ? '' : `${fmt.tokens(tok)} tokens`].filter((p) => p !== '').join(' · ')
}

export interface DateItem {
  kind: 'date'
  key: string
  label: string
  /** A preset the row stands for, or null for one calendar day. */
  preset: Preset | null
  /** Null for "All time" only. */
  from: Day | null
  to: Day | null
}
export interface FilterItem { kind: 'filter'; key: string; filter: FilterKind; value: string; label: string; n: number }
export interface PrefixItem { kind: 'prefix'; key: string; filter: FilterKind; example: string }
export interface ProjectItem { kind: 'project'; key: string; row: ServerProject }
export interface SessionItem { kind: 'session'; key: string; row: PaletteSession }
export type PaletteItem = DateItem | FilterItem | PrefixItem | ProjectItem | SessionItem

export interface Section {
  id: 'dates' | 'filter-by' | 'largest' | 'filters' | 'projects' | 'sessions'
  title: string
  items: PaletteItem[]
}

function presetItem(preset: Preset, today: Day): DateItem {
  const label = PRESETS.find((p) => p.id === preset)!.label
  const r = resolvePeriod({ kind: 'preset', preset }, today)
  return { kind: 'date', key: `date:${preset}`, label, preset, from: r.from, to: r.to }
}


/** EXP-68: empty input shows Dates (Today, Yesterday, Last 7 days), Filter by…, and Largest in this period. */
export function emptySections(today: Day, largest: readonly SessionRow[]): Section[] {
  return [
    { id: 'dates', title: 'Dates', items: (['today', 'yesterday', 'last7'] as const).map((p) => presetItem(p, today)) },
    { id: 'filter-by', title: 'Filter by…', items: FILTER_PREFIXES.map((f) => ({ kind: 'prefix', key: `prefix:${f.kind}`, filter: f.kind, example: f.example })) },
    ...(largest.length > 0
      ? [{ id: 'largest' as const, title: 'Largest in this period', items: largest.slice(0, 6).map((r): PaletteItem => ({ kind: 'session', key: `s:${r.session}`, row: fromSessionRow(r) })) }]
      : []),
  ]
}

/** EXP-69: the sections that have results, in this order: Filters, Dates, Projects, Sessions. */
export function typedSections(text: string, today: Day, data: PaletteResult | undefined): Section[] {
  const out: Section[] = []
  if (data && data.filter_values.length > 0) {
    out.push({
      id: 'filters',
      title: 'Filters',
      items: data.filter_values.map((f) => ({ kind: 'filter', key: `f:${f.kind}:${f.value}`, filter: f.kind, value: f.value, label: f.label, n: f.n })),
    })
  }
  const dates = parseDateRows(text, today).map((r): DateItem => {
    if (r.kind === 'range') {
      const preset = r.preset!
      return { kind: 'date', key: `date:${preset}`, label: PRESETS.find((p) => p.id === preset)!.label, preset, from: r.from, to: r.to }
    }
    return { kind: 'date', key: `date:${r.from}`, label: fmt.date(r.from!), preset: null, from: r.from, to: r.to }
  })
  if (dates.length > 0) out.push({ id: 'dates', title: 'Dates', items: dates })
  if (data && data.projects.length > 0) {
    out.push({ id: 'projects', title: 'Projects', items: data.projects.map((p) => ({ kind: 'project', key: `p:${p.project}`, row: p })) })
  }
  if (data && data.sessions.length > 0) {
    out.push({ id: 'sessions', title: 'Sessions', items: data.sessions.map((s) => ({ kind: 'session', key: `s:${s.session}`, row: s })) })
  }
  return out
}

export const flatItems = (sections: readonly Section[]): PaletteItem[] => sections.flatMap((s) => s.items)

/** EXP-71: ↑/↓ move the selection and wrap. */
export function moveSelection(i: number, delta: 1 | -1, n: number): number {
  return n <= 0 ? 0 : (i + delta + n) % n
}

// ---- actions (EXP-72) --------------------------------------------------------

export interface RunContext {
  url: UrlState
  today: Day
}

export interface Undo {
  message: string
  /** Restores the search, filters, automated rule and period; the session stays open. */
  patch: Partial<UrlState>
}

export type Plan =
  /** A Filter by… row: put the prefix into the input and keep the palette open. */
  | { type: 'prefix'; text: string }
  /** A date, filter or project row: change the scope and close every blade (`patch.s` is null). */
  | { type: 'scope'; patch: Partial<UrlState> }
  /** A session row: open it in blade 1 with `patch` applied, and show the toast when filters were cleared. */
  | { type: 'session'; session: string; patch: Partial<UrlState>; toast: Undo | null }

/** The period a date row sets. A day that is today or yesterday uses that preset, so the URL and title read as the preset. */
export function datePeriod(d: DateItem, today: Day): Period {
  if (d.preset !== null) return { kind: 'preset', preset: d.preset }
  const day = d.from!
  if (day === today) return { kind: 'preset', preset: 'today' }
  if (day === addDays(today, -1)) return { kind: 'preset', preset: 'yesterday' }
  return { kind: 'custom', from: day, to: day }
}

const addUnique = <T,>(list: readonly T[], v: T): T[] => (list.includes(v) ? [...list] : [...list, v])

/**
 * EXP-72, what Enter or a click does:
 * - a date sets the period only: search and filters stay, all blades close;
 * - a filter adds its value to its filter, all blades close, the period stays;
 * - a project sets the project filter to it (and shows automated sessions when every session of the project is automated, D38 M5, D41 N2);
 * - a session opens in blade 1. Outside the period, the period becomes its first day with a model call. Hidden by the search or filters,
 *   those are cleared (and automated shown if needed) and the toast offers Undo.
 */
export function planFor(item: PaletteItem, c: RunContext): Plan {
  switch (item.kind) {
    case 'prefix':
      return { type: 'prefix', text: `${item.filter}:` }
    case 'date':
      return { type: 'scope', patch: { period: datePeriod(item, c.today), s: null } }
    case 'filter':
      switch (item.filter) {
        case 'model':
          return { type: 'scope', patch: { models: addUnique(c.url.models, item.value), s: null } }
        case 'branch':
          return { type: 'scope', patch: { branches: addUnique(c.url.branches, item.value), s: null } }
        case 'provider':
          return { type: 'scope', patch: { providers: addUnique(c.url.providers, item.value as Provider), s: null } }
        case 'project':
          return { type: 'scope', patch: { projects: addUnique(c.url.projects, item.value), s: null } }
      }
      throw new Error('unreachable filter kind')
    case 'project': {
      const r = item.row
      const allAutomated = r.n_sessions > 0 && r.n_automated === r.n_sessions
      return { type: 'scope', patch: { projects: [r.project], ...(allAutomated ? { auto: true } : {}), s: null } }
    }
    case 'session': {
      const r = item.row
      const patch: Partial<UrlState> = { s: r.session }
      if (!r.in_period) {
        if (r.first_day === null) throw new Error('planFor: a session outside the period must carry first_day (API-14)')
        patch.period = { kind: 'custom', from: r.first_day, to: r.first_day }
      }
      let toast: Undo | null = null
      if (!r.in_filters) {
        patch.q = ''
        patch.providers = []
        patch.models = []
        patch.branches = []
        patch.projects = []
        if (r.is_automated) patch.auto = true
        toast = {
          message: 'Filters cleared to show this session',
          patch: {
            q: c.url.q, providers: c.url.providers, models: c.url.models, branches: c.url.branches, projects: c.url.projects,
            auto: c.url.auto, period: c.url.period,
          },
        }
      }
      return { type: 'session', session: r.session, patch, toast }
    }
  }
}
