import type { GroupKey, Provider, SessionGroup, SessionRow, SortDir, SortKey } from '../../../api/types'
import { fmt } from '../../../lib/format'
import { DEFAULT_URL_STATE, LEVEL_PARAMS, type UrlState } from '../../../state/url'
import { PROVIDER_NAME } from '../../../ui/provider'
import { rowColumns } from '../../../ui/row.logic'
import { weekdayShort } from '../../../ui/tz'

/**
 * Rules of the explorer's session list and rail: columns (EXP-49), sort (EXP-51), flags (EXP-52), grouping and folds (EXP-56 to
 * EXP-59), cursor (EXP-54, EXP-63). No DOM, no fetch: the components only draw what these functions decide.
 */

/** API-12 page size: pages of 200 load as the list scrolls. */
export const PAGE = 200
/** EXP-58: each group shows its top 5 rows under the current sort. */
export const GROUP_TOP = 5
/** EXP-58: a project with exactly one session costing under this folds into "Small projects". */
export const SMALL_COST = 1
export const SMALL_KEY = '\u0000small-projects'
export const SMALL_NAME = 'Small projects'

// ---- columns and heads (EXP-49, EXP-50) ------------------------------------

export type HeadId = 'start' | 'project' | 'name' | 'flags' | 'activity' | 'cost' | 'wall' | 'active' | 'tokens'
export interface Head {
  id: HeadId
  label: string
  /** The sort this head sets, or null for a head that is not sortable. */
  sort: SortKey | null
  align: 'left' | 'right'
}

const HEADS: readonly Head[] = [
  { id: 'start', label: 'Started', sort: 'start', align: 'left' },
  { id: 'project', label: 'Project', sort: null, align: 'left' },
  { id: 'name', label: 'Session', sort: null, align: 'left' },
  { id: 'flags', label: '', sort: null, align: 'left' },
  { id: 'activity', label: 'Activity', sort: null, align: 'left' },
  { id: 'cost', label: 'Cost', sort: 'cost', align: 'right' },
  { id: 'wall', label: 'Wall', sort: 'wall', align: 'right' },
  { id: 'active', label: 'Active', sort: 'active', align: 'right' },
  { id: 'tokens', label: 'Tokens', sort: 'tokens', align: 'right' },
]

/** EXP-56: when grouped by project the Project column is hidden. Day groups keep it. */
export function showProjectColumn(group: GroupKey | null): boolean {
  return group !== 'project'
}

export function listHeads(group: GroupKey | null): Head[] {
  return HEADS.filter((h) => h.id !== 'project' || showProjectColumn(group))
}

/** The row grid of EXP-49: When 88, Project 136 (not under project grouping), Session 1fr, Flags 128, Activity 200, Cost 184, 64, 64, 56. */
export function listColumns(group: GroupKey | null): string {
  return rowColumns({ context: showProjectColumn(group), magnitude: 200, secondaries: 3 })
}

/** EXP-51: clicking the active head reverses the direction; clicking another head sorts by it, descending. */
export function nextSort(cur: { sort: SortKey; dir: SortDir }, clicked: SortKey): { sort: SortKey; dir: SortDir } {
  if (cur.sort === clicked) return { sort: clicked, dir: cur.dir === 'desc' ? 'asc' : 'desc' }
  return { sort: clicked, dir: 'desc' }
}

// ---- sorting (EXP-51, EXP-57) ----------------------------------------------

export function sortValue(r: SessionRow, key: SortKey): number {
  switch (key) {
    case 'start':
      return r.start_ms
    case 'cost':
      return r.cost_usd ?? 0 // FALLBACK-OK: system.md SYS-31 (a null cost is zero by construction: no model call)
    case 'wall':
      return r.wall_ms
    case 'active':
      return r.active_ms
    case 'tokens':
      return r.tok_total ?? 0 // FALLBACK-OK: system.md SYS-31 (null tokens are zero by construction: no model call)
  }
}

/** EXP-51: ties break by start time, newest first, in either direction. */
export function compareRows(sort: SortKey, dir: SortDir): (a: SessionRow, b: SessionRow) => number {
  const sign = dir === 'desc' ? -1 : 1
  return (a, b) => sign * (sortValue(a, sort) - sortValue(b, sort)) || b.start_ms - a.start_ms
}

// ---- figures (EXP-49, SYS-39) ----------------------------------------------

/** EXP-49, D38 L7: cost is 600 when it is at or above the 90th percentile of the in-scope costs, else 500. A zero cost is never 600. */
export function costWeight(cost: number | null, p90: number | null): 500 | 600 {
  // FALLBACK-OK: explorer.md EXP-49 (no percentile or no cost: the normal weight)
  if (cost === null || p90 === null || !(cost > 0)) return 500
  return cost >= p90 ? 600 : 500
}

// ---- flags lane (EXP-52) ---------------------------------------------------

export type Flag =
  | { kind: 'provider'; provider: Provider; tooltip: string }
  | { kind: 'automated'; tooltip: string }
  | { kind: 'agents'; n: number; tooltip: string }
  | { kind: 'nights'; n: number; tooltip: string }

/** EXP-52, in this order: provider mark (or `repeat` when automated); `bot` + count at 2 or more sub-agents; `+Nd` when the session ends N local midnights after its start. */
export function flagsFor(row: SessionRow, tz: string): Flag[] {
  const flags: Flag[] = []
  if (row.is_automated) flags.push({ kind: 'automated', tooltip: `Automated · ${PROVIDER_NAME[row.provider]}` })
  else flags.push({ kind: 'provider', provider: row.provider, tooltip: PROVIDER_NAME[row.provider] })
  if (row.n_agents >= 2) flags.push({ kind: 'agents', n: row.n_agents, tooltip: `${row.n_agents} sub-agents` })
  if (row.ends_after_days > 0) {
    flags.push({ kind: 'nights', n: row.ends_after_days, tooltip: `Ends ${weekdayShort(row.end_ms, tz)} ${fmt.clock(row.end_ms, tz)}` })
  }
  return flags
}

// ---- groups (EXP-56 to EXP-59) ---------------------------------------------

export interface DisplayGroup extends SessionGroup {
  /** The "Small projects" group (EXP-58): built here, not by the server. */
  small: boolean
}

function sumNullable(values: (number | null)[]): number | null {
  let sum: number | null = null
  for (const v of values) if (v !== null) sum = (sum ?? 0) + v
  return sum
}

function mergeSmall(groups: SessionGroup[], sort: SortKey, dir: SortDir): DisplayGroup {
  const first = groups[0].period_activity
  const tok = first.tok.map(() => 0)
  for (const g of groups) {
    const a = g.period_activity
    if (a.bin_ms !== first.bin_ms || a.start_ms !== first.start_ms || a.tok.length !== tok.length) {
      throw new Error('mergeSmall: project groups of one response must share one period axis (API-12)')
    }
    a.tok.forEach((v, i) => (tok[i] += v))
  }
  return {
    key: SMALL_KEY,
    name: SMALL_NAME,
    color_slot: null,
    small: true,
    n_sessions: groups.reduce((n, g) => n + g.n_sessions, 0),
    cost_usd: sumNullable(groups.map((g) => g.cost_usd)),
    cost_lower_bound: groups.some((g) => g.cost_lower_bound),
    n_unpriced: groups.reduce((n, g) => n + g.n_unpriced, 0),
    tok_total: sumNullable(groups.map((g) => g.tok_total)),
    wall_ms: groups.reduce((n, g) => n + g.wall_ms, 0),
    active_ms: groups.reduce((n, g) => n + g.active_ms, 0),
    period_activity: { bin_ms: first.bin_ms, start_ms: first.start_ms, tok },
    rows: groups.flatMap((g) => g.rows).sort(compareRows(sort, dir)),
  }
}

/**
 * The groups in display order. The server orders groups by the sort measure (QRY-26, EXP-57), and that order is kept. Under project
 * grouping, every project with exactly one session costing under $1.00 leaves its place and joins one "Small projects" group at the end (EXP-58).
 */
export function displayGroups(groups: readonly SessionGroup[], mode: GroupKey, sort: SortKey, dir: SortDir): DisplayGroup[] {
  if (mode !== 'project') return groups.map((g) => ({ ...g, small: false }))
  const isSmall = (g: SessionGroup) => g.n_sessions === 1 && (g.cost_usd ?? 0) < SMALL_COST // FALLBACK-OK: system.md SYS-31 (null cost is zero)
  const big = groups.filter((g) => !isSmall(g)).map((g) => ({ ...g, small: false }))
  const small = groups.filter(isSmall)
  return small.length > 0 ? [...big, mergeSmall(small, sort, dir)] : big
}

/** EXP-59: groups other than "Small projects" start expanded. `toggled` holds the groups the user flipped from that default. */
export function groupOpen(g: Pick<DisplayGroup, 'key' | 'small'>, toggled: ReadonlySet<string>): boolean {
  return toggled.has(g.key) ? g.small : !g.small
}

/** EXP-56: project groups carry the project's name; a day group's name is its local start day, `Mon Oct 12`, built from the key (a Day). */
export function groupTitle(g: Pick<DisplayGroup, 'key' | 'name'>, mode: GroupKey): string {
  return mode === 'day' ? fmt.date(g.key) : g.name
}

export interface GroupFold {
  /** Rows beyond the top 5. */
  hidden: number
  /** "Show 7 more" while folded, "Show top 5" once expanded; empty when the group has no more than 5 rows. */
  label: string
  /** Cost of the hidden rows (group cost minus the top rows); null once expanded or when the group has no cost. */
  sum: number | null
}

/** SYS-38: "Show N more · $X" where $X is the group's cost minus the cost of the rows shown. */
export function groupFold(g: Pick<DisplayGroup, 'n_sessions' | 'cost_usd' | 'rows'>, expanded: boolean): GroupFold {
  const hidden = g.n_sessions - GROUP_TOP
  if (hidden <= 0) return { hidden: 0, label: '', sum: null }
  if (expanded) return { hidden, label: `Show top ${GROUP_TOP}`, sum: null }
  if (g.cost_usd === null) return { hidden, label: `Show ${hidden} more`, sum: null }
  const shown = g.rows.slice(0, GROUP_TOP).reduce((s, r) => s + (r.cost_usd ?? 0), 0) // FALLBACK-OK: system.md SYS-31 (null cost is zero)
  return { hidden, label: `Show ${hidden} more`, sum: Math.max(0, Math.round((g.cost_usd - shown) * 1e6) / 1e6) }
}

// ---- paging (API-12) -------------------------------------------------------

/** The pages that cover rows `from` to `to` (exclusive), clamped to `total`, plus `extra` row indices (the cursor's target). Sorted, no duplicates. */
export function wantedPages(from: number, to: number, total: number, extra: readonly number[] = []): number[] {
  if (total <= 0) return []
  const pages = new Set<number>()
  const add = (i: number) => pages.add(Math.floor(Math.max(0, Math.min(i, total - 1)) / PAGE))
  if (to > from) {
    add(from)
    add(to - 1)
  }
  for (const i of extra) add(i)
  return [...pages].sort((a, b) => a - b)
}

/** FE-7, QRY-16: a later page that carries another `last_ingest_ms` or another `total` than page 0 means the store moved; the list refetches from offset 0. */
export function pageConsistent(
  first: { total: number; freshness: { last_ingest_ms: number | null } },
  page: { total: number; freshness: { last_ingest_ms: number | null } },
): boolean {
  return first.total === page.total && first.freshness.last_ingest_ms === page.freshness.last_ingest_ms
}

// ---- cursor (EXP-54, EXP-63, EXP-64, EXP-65) -------------------------------

/** EXP-54: j/k from no selection selects the first row; otherwise one step, stopping at the ends. */
export function moveCursor(cur: number | null, delta: 1 | -1, count: number): number | null {
  if (count <= 0) return null
  if (cur === null) return 0
  return Math.max(0, Math.min(count - 1, cur + delta))
}

/** EXP-63: the rail header reads `4 / 214`. With no row selected (the open session left the scope, EXP-65) the position is a dash. */
export function railPosition(index: number | null, count: number): string {
  return `${index === null ? '–' : index + 1} / ${count}`
}

/** EXP-64/65: the index of the open session in the rows the list holds, or null when it is not there (it left the scope, or its page has not loaded). */
export function openIndex(session: string | null, rowAt: (i: number) => SessionRow | null, count: number): number | null {
  if (session === null) return null
  for (let i = 0; i < count; i++) if (rowAt(i)?.session === session) return i
  return null
}

/** EXP-63: j/k in the rail replaces the open session. The old session's own blade parameters (and those of the blades to its right) return to their defaults. */
export function swapSessionPatch(session: string): Partial<UrlState> {
  const patch: Record<string, unknown> = {}
  for (const level of ['session', 'turn', 'detail'] as const) for (const key of LEVEL_PARAMS[level]) patch[key] = DEFAULT_URL_STATE[key]
  patch.s = session
  return patch as Partial<UrlState>
}

// ---- no-result state (EXP-79, EXP-80) --------------------------------------

export type NoResultKind = 'filtered' | 'empty-period'

/** EXP-79: with search text or a filter active the empty list is "No sessions match"; with none it is an empty period (EXP-80). */
export function noResultKind(s: { q: string; providers: readonly string[]; models: readonly string[]; branches: readonly string[]; projects: readonly string[] }): NoResultKind {
  return s.q !== '' || s.providers.length + s.models.length + s.branches.length + s.projects.length > 0 ? 'filtered' : 'empty-period'
}

/** EXP-79: `"rbacxyz" matches 3 sessions in all time`. */
export function allTimeMatchText(text: string, n: number): string {
  return `"${text}" matches ${n} ${n === 1 ? 'session' : 'sessions'} in all time`
}

/** EXP-78: the list fades from 0.45 to 1 when new data replaces the old; never on a first load, never under reduced motion. */
export function shouldFade(wasStale: boolean, stale: boolean, reducedMotion: boolean): boolean {
  return wasStale && !stale && !reducedMotion
}
