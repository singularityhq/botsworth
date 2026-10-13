import type { Day, PeriodSummary, Provider } from '../../api/types'
import { fmt, modelShort } from '../../lib/format'
import { editRange, parseDay, PRESETS, resolvePeriod, type Period } from '../../state/period'
import type { Patch } from '../../state/nav'
import type { UrlState } from '../../state/url'
import { PROVIDER_NAME } from '../../ui/provider'

/** Page-level rules of the explorer: period title (EXP-10), totals line (EXP-12), period fields (EXP-15), filter tokens (EXP-25),
 *  Clear all (EXP-26) and the page state (EXP-77 to EXP-83). No React. */

// ---- period title (EXP-10) ----------------------------------------------

export interface Extent {
  first_day: Day | null
  last_day: Day | null
}

/** The title text. `today` is null until the zone is known; a preset then shows its name only. */
export function periodTitle(period: Period, today: Day | null, extent: Extent | null): string {
  if (period.kind === 'custom') return period.from === period.to ? fmt.date(period.from) : fmt.dateRange(period.from, period.to)
  const label = PRESETS.find((p) => p.id === period.preset)!.label
  if (period.preset === 'all') {
    return extent && extent.first_day !== null && extent.last_day !== null ? `${label} · ${fmt.dateRange(extent.first_day, extent.last_day)}` : label
  }
  if (today === null) return label
  const r = resolvePeriod(period, today)
  const dates = period.preset === 'today' || period.preset === 'yesterday' ? fmt.date(r.from!) : fmt.dateRange(r.from!, r.to!)
  return `${label} · ${dates}`
}

/** The right-aligned range of a preset row (EXP-13). All time uses the data extent. */
export function presetRange(preset: Period & { kind: 'preset' }, today: Day, extent: Extent | null): string {
  const r = resolvePeriod(preset, today)
  if (r.from === null || r.to === null) {
    return extent && extent.first_day !== null && extent.last_day !== null ? fmt.dateRange(extent.first_day, extent.last_day) : ''
  }
  return fmt.dateRange(r.from, r.to)
}

// ---- period fields (EXP-15) ---------------------------------------------

/** Enter in a field: the new range, or null when the text is not a date (the field then shows its previous value, EXP-15). */
export function commitField(range: { from: Day; to: Day }, field: 'from' | 'to', text: string, today: Day): { from: Day; to: Day } | null {
  const day = parseDay(text, today)
  return day === null ? null : editRange(range, field, day)
}

/** A field's text for a day (`Oct 6`, EXP-13). */
export function fieldText(day: Day): string {
  return fmt.dateRange(day, day)
}

// ---- scope narrowing, Clear all (EXP-12, EXP-26) -------------------------

type Scoped = Pick<UrlState, 'q' | 'providers' | 'models' | 'branches' | 'projects' | 'auto'>

/** Search text or any filter value is set. The automated rule is not a narrowing filter (EXP-12). */
export function isNarrowed(u: Scoped): boolean {
  return u.q.trim() !== '' || u.providers.length > 0 || u.models.length > 0 || u.branches.length > 0 || u.projects.length > 0
}

/** Clear all shows when the search text is set or any filter differs from its default (EXP-26). */
export function canClearAll(u: Scoped): boolean {
  return isNarrowed(u) || u.auto
}

/** Exactly the fields EXP-26 resets. The period, measure, sort and grouping stay. */
export function clearAllPatch(): Patch {
  return { q: '', providers: [], models: [], branches: [], projects: [], auto: false }
}

// ---- filter tokens (EXP-25, EXP-27, EXP-29) ------------------------------

export type TokenKind = 'provider' | 'model' | 'branch' | 'project' | 'automated'
export interface FilterToken {
  kind: TokenKind
  /** The value the URL holds (`auto` for the automated token). */
  value: string
  label: string
}

/** One token per selected value, in the order providers, models, branches, projects, automated. The automated token shows only while
 *  automated sessions are hidden and at least one is (EXP-29). */
export function filterTokens(u: Scoped, automatedHidden: number, projectName: (key: string) => string): FilterToken[] {
  const out: FilterToken[] = []
  for (const v of u.providers) out.push({ kind: 'provider', value: v, label: PROVIDER_NAME[v as Provider] ?? v }) // FALLBACK-OK: explorer.md EXP-75 (a value that matches nothing keeps its token)
  for (const v of u.models) out.push({ kind: 'model', value: v, label: modelShort(v) })
  for (const v of u.branches) out.push({ kind: 'branch', value: v, label: v })
  for (const v of u.projects) out.push({ kind: 'project', value: v, label: projectName(v) })
  if (!u.auto && automatedHidden > 0) out.push({ kind: 'automated', value: 'auto', label: `automated hidden · ${automatedHidden}` })
  return out
}

/** The URL change that removes a token (EXP-25, EXP-28). */
export function removeTokenPatch(u: Scoped, t: FilterToken): Patch {
  switch (t.kind) {
    case 'provider':
      return { providers: u.providers.filter((v) => v !== t.value) }
    case 'model':
      return { models: u.models.filter((v) => v !== t.value) }
    case 'branch':
      return { branches: u.branches.filter((v) => v !== t.value) }
    case 'project':
      return { projects: u.projects.filter((v) => v !== t.value) }
    case 'automated':
      return { auto: true }
  }
}

/** Toggle one value in a multi-select list (EXP-21 to EXP-23). */
export function toggleValue(list: readonly string[], value: string): string[] {
  return list.includes(value) ? list.filter((v) => v !== value) : [...list, value]
}

/** `conductor/rbac-cont…2026-09-22`: keep the start and the end of a long name (EXP-23). */
export function middleTruncate(text: string, max: number): string {
  if (text.length <= max) return text
  const keep = max - 1
  const head = Math.ceil(keep * 0.65)
  return `${text.slice(0, head)}…${text.slice(text.length - (keep - head))}`
}

/** A project's name from its key when the summary does not list it (EXP-75: a filter value that matches nothing is kept). */
export function projectFallbackName(key: string): string {
  const parts = key.split('/').filter((p) => p !== '')
  return parts.length > 0 ? parts[parts.length - 1] : key
}

// ---- totals line (EXP-12) -------------------------------------------------

export interface TotalsView {
  cost: number
  costLowerBound: boolean
  nUnpriced: number
  tokens: number
  sessions: number
  /** The count of sessions in the period under the automated rule only; null while nothing narrows the set. */
  of: number | null
  hidden: number
}

export function totalsView(s: PeriodSummary, u: Scoped): TotalsView {
  const t = s.totals
  return {
    // FALLBACK-OK: explorer.md EXP-80 (an empty period reads $0.00 and 0 tokens: a real zero, QRY-11 null = no owned call)
    cost: t.cost_usd ?? 0,
    costLowerBound: t.cost_lower_bound,
    nUnpriced: t.n_unpriced,
    tokens: t.tok_total ?? 0, // FALLBACK-OK: explorer.md EXP-80 (a real zero)
    sessions: t.n_sessions,
    of: isNarrowed(u) ? t.n_sessions_period : null,
    hidden: !u.auto && t.n_automated_hidden > 0 ? t.n_automated_hidden : 0,
  }
}

export function sessionsText(v: Pick<TotalsView, 'sessions' | 'of'>): string {
  if (v.of !== null) return `${v.sessions} of ${v.of} sessions`
  return `${v.sessions} ${v.sessions === 1 ? 'session' : 'sessions'}`
}

/** The line as plain text, items 3 spaces apart (the EXP-12 scenario string). */
export function totalsText(v: TotalsView): string {
  const items = [
    fmt.money(v.cost, { lowerBound: v.costLowerBound }),
    `${fmt.tokens(v.tokens)} tokens`,
    sessionsText(v),
  ]
  if (v.hidden > 0) items.push(`${v.hidden} automated hidden`)
  return items.join('   ')
}

// ---- page state (EXP-77 to EXP-83) -----------------------------------------

export type PageState = 'loading' | 'ready' | 'no-results' | 'empty-period'

/** `no-results`: search or a filter is active and nothing matches (EXP-79). `empty-period`: nothing matches and nothing narrows (EXP-80). */
export function pageState(s: PeriodSummary | undefined, u: Scoped): PageState {
  if (s === undefined) return 'loading'
  if (s.totals.n_sessions > 0) return 'ready'
  return isNarrowed(u) ? 'no-results' : 'empty-period'
}

/** `No sessions on Sun Oct 4` or `No sessions from Oct 1 to Oct 3` (EXP-80). */
export function emptyPeriodText(from: Day, to: Day): string {
  return from === to ? `No sessions on ${fmt.date(from)}` : `No sessions from ${fmt.dateRange(from, from)} to ${fmt.dateRange(to, to)}`
}

/** `Sat Oct 3 · 12 sessions` (EXP-80). */
export function nearbyDayText(day: Day, n: number): string {
  return `${fmt.date(day)} · ${n} ${n === 1 ? 'session' : 'sessions'}`
}

/** The all-time line of the no-results card (EXP-79). */
export function allTimeMatchText(q: string, n: number): string {
  return `"${q}" matches ${n} ${n === 1 ? 'session' : 'sessions'} in all time`
}
