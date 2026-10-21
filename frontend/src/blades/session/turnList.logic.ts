import type { GapRow, TurnRow } from '../../api/types'
import { fmt } from '../../lib/format'
import type { Bars, MsRange } from '../../state/url'
import { GAP_MIN_MS, gapReason, gapState, kindOf, type StateKey } from '../shared/states'
import { localDay } from '../../ui/tz'

/**
 * The session turn list (SES-33 to SES-38): which entries are rows, in what order, what each row shows.
 * Pure: wire items in, entries out. The components only draw them.
 */

export type NumberedTurn = TurnRow & { display_id: string }

export type ListEntry =
  | { type: 'turn'; key: string; row: NumberedTurn; ix: number }
  | { type: 'gap'; key: string; gap: GapRow; state: StateKey; reason: string; turnIx: number | null }
  | { type: 'fold'; key: string; rows: TurnRow[] }

export type TurnEntry = Extract<ListEntry, { type: 'turn' }>

/** `T12` → 12. Harness turns have no display id (QRY-2). */
export function displayIx(displayId: string | null): number | null {
  const m = displayId === null ? null : /^T(\d+)$/.exec(displayId)
  return m ? Number(m[1]) : null
}

export function isNumbered(r: TurnRow): r is NumberedTurn {
  return r.kind !== 'harness' && r.display_id !== null
}

type Item = TurnRow | GapRow

/**
 * Time order (SES-36). An item with no start (S2 §5) sorts by `seq`: it takes the position of the item before it in the
 * server's order, and stays after it (SES-85). A gap that ends where a turn starts sorts above that turn because it starts earlier.
 */
export function orderItems(items: readonly Item[]): Item[] {
  let carry = Number.NEGATIVE_INFINITY
  const keyed = items.map((it, seq) => {
    if (it.start_ms !== null) carry = it.start_ms
    return { it, key: carry, seq }
  })
  keyed.sort((a, b) => a.key - b.key || a.seq - b.seq)
  return keyed.map((k) => k.it)
}

export interface ListFilter {
  /** Search active: the turn op ids with a matching row (SES-32). null = no search. */
  kept: ReadonlySet<string> | null
  /** Session range (SES-30). null = none. */
  range: MsRange | null
}

export const NO_FILTER: ListFilter = { kept: null, range: null }

const overlaps = (start: number | null, end: number | null, r: MsRange) =>
  start !== null && end !== null && end > r.start_ms && start < r.end_ms

/**
 * Entries of the list, in order: numbered turns, gap rows (state `none`, 2 min or more, SES-36) and harness folds (SES-35).
 * Agents-only stretches are never rows here (SES-19). With a search, only the matching turns remain (SES-32).
 * With a range, turns overlapping it, gaps overlapping it and folds that start inside it remain (SES-30).
 */
export function buildEntries(items: readonly Item[], filter: ListFilter = NO_FILTER): ListEntry[] {
  const ordered = orderItems(items)
  const numbered = ordered.filter((i): i is NumberedTurn => i.type === 'turn' && isNumbered(i))
  const firstTurnIsHuman = numbered.length > 0 && numbered[0].kind === 'human'
  const ixByTurn = new Map(numbered.map((t) => [t.turn, displayIx(t.display_id)]))
  const out: ListEntry[] = []
  let fold: TurnRow[] = []
  const flush = () => {
    if (fold.length === 0) return
    out.push({ type: 'fold', key: `fold-${fold[0].turn}-${out.length}`, rows: fold })
    fold = []
  }
  for (const it of ordered) {
    if (it.type === 'gap') {
      flush()
      if (it.state === 'agents' || it.dur_ms < GAP_MIN_MS) continue
      const reason = gapReason(it, { firstTurnIsHuman })
      out.push({ type: 'gap', key: it.id, gap: it, state: gapState(it), reason, turnIx: it.turn === null ? null : (ixByTurn.get(it.turn) ?? null) }) // FALLBACK-OK: SES-37 (a stretch attached to no numbered turn has no turn to open)
    } else if (isNumbered(it)) {
      flush()
      const ix = displayIx(it.display_id)
      // A numbered turn always has a display id of the form T<n> (QRY-2).
      if (ix === null) throw new Error(`turn ${it.turn}: display_id ${it.display_id} is not T<n>`)
      out.push({ type: 'turn', key: it.turn, row: it, ix })
    } else {
      fold.push(it)
    }
  }
  flush()
  return applyFilter(out, filter)
}

function applyFilter(entries: ListEntry[], { kept, range }: ListFilter): ListEntry[] {
  return entries.filter((e) => {
    if (e.type === 'turn') {
      if (kept && !kept.has(e.row.turn)) return false
      return !range || overlaps(e.row.start_ms, e.row.end_ms, range)
    }
    if (kept) return false // "Turns with “x”" lists matching turns only
    if (!range) return true
    if (e.type === 'gap') return overlaps(e.gap.start_ms, e.gap.end_ms, range)
    return e.rows.some((r) => r.start_ms !== null && r.start_ms >= range.start_ms && r.start_ms < range.end_ms)
  })
}

// ---- cursor (SES-38) ---------------------------------------------------------

export function cursorStops(entries: readonly ListEntry[]): TurnEntry[] {
  return entries.filter((e): e is TurnEntry => e.type === 'turn')
}

/** A turn has rows when it has a call, agent, skill load or failure. Exact without a row count: a tool row always has an issuing llm_call in the same turn (lead ruling, 2026-10-12). */
export function hasRows(r: TurnRow): boolean {
  return r.n_llm_calls > 0 || r.n_agents > 0 || r.n_skill_loads > 0 || r.n_failed > 0
}

/** On open the cursor is on the first numbered turn that has rows; when none has, on the first numbered turn. */
export function initialCursor(entries: readonly ListEntry[]): string | null {
  const stops = cursorStops(entries)
  // FALLBACK-OK: SES-38 (no numbered turn has rows: the first numbered turn; no numbered turn: no cursor)
  return (stops.find((s) => hasRows(s.row)) ?? stops[0])?.key ?? null
}

/** j/k: move by `delta` over the turns the list keeps; clamp at the ends. A cursor outside the list lands on the first (or last) stop. */
export function moveCursor(stops: readonly TurnEntry[], current: string | null, delta: number): string | null {
  if (stops.length === 0) return null
  const i = stops.findIndex((s) => s.key === current)
  if (i < 0) return stops[delta < 0 ? stops.length - 1 : 0].key
  return stops[Math.max(0, Math.min(stops.length - 1, i + delta))].key
}

/** SES-38: when the cursor row leaves the visible area, scroll so it sits about 30 % from the top. Returns the new scrollTop, or null when the row is visible. */
export function scrollTarget(rowTop: number, rowH: number, viewTop: number, viewH: number): number | null {
  if (rowTop >= viewTop && rowTop + rowH <= viewTop + viewH) return null
  return Math.max(0, Math.round(rowTop - 0.3 * viewH))
}

/** The row element of a turn, found by its op id (op ids hold slashes, so no selector string). */
export function findRow(box: HTMLElement | null | undefined, key: string): HTMLElement | null {
  // FALLBACK-OK: SES-38 (a cursor row that is not drawn, e.g. while the list loads, has nothing to scroll to)
  return Array.from(box?.querySelectorAll<HTMLElement>('[data-turn-key]') ?? []).find((el) => el.dataset.turnKey === key) ?? null
}

/**
 * Scroll `box` so that `row` is fully inside it, moving the smallest distance (the effect of `block: 'nearest'`), and touch nothing else.
 * `row.scrollIntoView` also scrolls every scrollable ancestor, which pulled the blade strip back to the rail (F4b report, FX 8).
 */
export function revealInBox(box: HTMLElement, row: HTMLElement): void {
  const b = box.getBoundingClientRect()
  const r = row.getBoundingClientRect()
  if (r.top < b.top) box.scrollTop += r.top - b.top
  else if (r.bottom > b.bottom) box.scrollTop += Math.min(r.bottom - b.bottom, r.top - b.top)
}

// ---- row content ---------------------------------------------------------------

/** SES-34 item 2: the glyph shows only when the kinds in the list vary. */
export function kindsVary(entries: readonly ListEntry[]): boolean {
  const kinds = new Set(cursorStops(entries).map((s) => kindOf(s.row.kind, s.row.origin, s.row.label).key))
  return kinds.size > 1
}

export type Flag =
  | { type: 'model'; model: string }
  | { type: 'more'; n: number }
  | { type: 'agents'; n: number }
  | { type: 'skills'; n: number }
  | { type: 'failed'; n: number }
  | { type: 'search'; n: number }

/** SES-34 item 4: model marks (most costly first, at most 2, then `+N`), agents, skill loads, failures; each hidden at 0. With a search, only `search N`. */
export function turnFlags(r: TurnRow, searchCount: number | null): Flag[] {
  if (searchCount !== null) return [{ type: 'search', n: searchCount }]
  const flags: Flag[] = []
  const byCost = r.models.map((m, i) => ({ m, i })).sort((a, b) => (b.m.cost_usd ?? -1) - (a.m.cost_usd ?? -1) || a.i - b.i) // FALLBACK-OK: SES-34 item 4 (a model with no cost is least costly)
  for (const { m } of byCost.slice(0, 2)) flags.push({ type: 'model', model: m.model })
  if (byCost.length > 2) flags.push({ type: 'more', n: byCost.length - 2 })
  if (r.n_agents > 0) flags.push({ type: 'agents', n: r.n_agents })
  if (r.n_skill_loads > 0) flags.push({ type: 'skills', n: r.n_skill_loads })
  if (r.n_failed > 0) flags.push({ type: 'failed', n: r.n_failed })
  return flags
}

/** SES-34 item 8: Waiting shows only from 30 s. The rail shows it from 1 min (SES-4). */
export const WAITING_MIN_MS = 30_000
export const RAIL_WAITING_MIN_MS = 60_000
export function waitingShown(ms: number | null, min = WAITING_MIN_MS): number | null {
  return ms !== null && ms >= min ? ms : null
}

/** SES-34 item 9: `—` when the turn's calls have null tokens. */
export function tokensText(v: number | null): string {
  return v === null ? '—' : fmt.tokens(v)
}

/** The value a bar draws for a measure (SES-31 measure segment); a missing value draws nothing. */
export function measureValue(r: TurnRow, bars: Bars): number {
  const v = bars === 'cost' ? r.cost_usd : bars === 'time' ? r.wall_ms : r.tok_total
  return v ?? 0 // FALLBACK-OK: SES-85 (a missing value draws no bar and is left out of rankings)
}

export function maxMeasure(entries: readonly ListEntry[], bars: Bars): number {
  return Math.max(0, ...cursorStops(entries).map((s) => measureValue(s.row, bars)))
}

/** SES-36: the gap bar length in %, against the longest gap in the list, at least 2 %. */
export function gapBarPct(durMs: number, maxMs: number): number {
  return maxMs > 0 ? Math.max(2, (100 * durMs) / maxMs) : 2
}

export function maxGap(entries: readonly ListEntry[]): number {
  return Math.max(0, ...entries.map((e) => (e.type === 'gap' ? e.gap.dur_ms : 0)))
}

/** SES-35: the fold's summed cost shows from $0.01, so the list reconciles with the header (DM-24). */
export function foldCost(rows: readonly TurnRow[]): number | null {
  let sum: number | null = null
  for (const r of rows) if (r.cost_usd !== null) sum = (sum ?? 0) + r.cost_usd // FALLBACK-OK: SES-35 (a harness turn with no cost adds nothing)
  return sum !== null && sum >= 0.01 ? sum : null
}

export function foldTitle(rows: readonly TurnRow[]): string {
  return `${rows.length} session event${rows.length === 1 ? '' : 's'}`
}

export function foldLabels(rows: readonly TurnRow[]): string {
  return rows.map((r) => r.label).join(', ')
}

/** SYS-34: a session inside one local day shows no weekday. */
export function withinOneDay(startMs: number, endMs: number, tz: string): boolean {
  return localDay(startMs, tz) === localDay(endMs, tz)
}

/** `Turns` or `Turns with “x”` (SES-32, SES-33). */
export function listHead(find: string): string {
  return find === '' ? 'Turns' : `Turns with “${find}”`
}

// ---- grid (SES-33) -------------------------------------------------------------------

export type ColumnId = 'when' | 'kind' | 'name' | 'flags' | 'bar' | 'cost' | 'wall' | 'waiting' | 'tokens' | 'go'

const COLUMN_W: Record<ColumnId, string> = {
  when: '56px', kind: '20px', name: 'minmax(0,1fr)', flags: '112px', bar: '104px', cost: '72px', wall: '64px', waiting: '64px', tokens: '56px', go: '16px',
}
const COLUMN_PX: Record<ColumnId, number> = { when: 56, kind: 20, name: 0, flags: 112, bar: 104, cost: 72, wall: 64, waiting: 64, tokens: 56, go: 16 }
/** Columns leave from the right end, never reorder (SYS-33). */
const DROP_ORDER: ColumnId[] = ['go', 'tokens', 'waiting', 'wall', 'cost', 'bar', 'flags']
const ALL: ColumnId[] = ['when', 'kind', 'name', 'flags', 'bar', 'cost', 'wall', 'waiting', 'tokens', 'go']
const COL_GAP = 12
const PAD_X = 36 // padding 0 16 0 20
const NAME_MIN = 160

export interface ListColumns {
  ids: ColumnId[]
  template: string
}

/** SES-33 grid for a blade `width` px wide. At 860 px and more it is the full grid; narrower, columns drop from the right until the name keeps 160 px. */
export function listColumns(width: number): ListColumns {
  const ids = [...ALL]
  const need = () => ids.reduce((a, c) => a + COLUMN_PX[c], 0) + COL_GAP * (ids.length - 1) + PAD_X + NAME_MIN
  for (const d of DROP_ORDER) {
    if (need() <= width) break
    ids.splice(ids.indexOf(d), 1)
  }
  return { ids, template: ids.map((c) => COLUMN_W[c]).join(' ') }
}

export const GRID_GAP = COL_GAP
export const GRID_PAD = '0 16px 0 20px'
