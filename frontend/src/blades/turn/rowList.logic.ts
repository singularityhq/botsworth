import type { AnyRow, ToolRow, TurnRows } from '../../api/types'
import type { Bars, MsRange, TurnOrder } from '../../state/url'
import { GAP_MIN_MS } from '../shared/states'
import { excludedFromTimeRanking } from '../shared/finders'
import { failureText } from './rowName'

/**
 * The turn blade's row list (TRN-12 to TRN-14, TRN-35, TRN-37): which rows show, in what order, and which are folded. Pure: wire rows in,
 * items out. The components draw the items; the stepper in the detail blade (F4b) imports `visibleRows` and `positionOf` to count its position.
 */

/** TRN-14: the Cost and Duration orders show this many rows before "Show N more". */
export const ORDER_TOP = 15
/** TRN-37: a run of this many quiet rows folds. */
export const QUIET_RUN_MIN = 4
/** TRN-37: a quiet row takes less than this much time. */
export const QUIET_TIME_MS = 60_000
/** TRN-37: a quiet model call costs less than this share of the turn. */
export const QUIET_COST_SHARE = 0.02
/** TRN-30: rows inside agents indent by level, at most this many levels. */
export const MAX_INDENT = 3
/** TRN-35 (D38 M8): at most this many rows of a band load per page. */
export const BAND_PAGE = 500

// ---- nodes -----------------------------------------------------------------------------------------------------

/** A row in the flat list: its depth (indent level) and the ids of the agent rows that contain it, outermost first. */
export interface RowNode {
  id: string
  row: AnyRow
  depth: number
  ancestors: readonly string[]
}

/** One loaded page of an agent's rows (API-32). */
export interface AgentPage {
  rows: readonly AnyRow[]
}

/** The row's start. An agent row starts with its earliest run; a row with no start is null (TRN-64). */
export function rowStart(r: AnyRow): number | null {
  if (r.type === 'agent') return r.runs.length === 0 ? null : Math.min(...r.runs.map((x) => x.start_ms))
  return r.start_ms
}

/** The row's end, for a gap's overlap with a zoom and for the Waterfall bar. */
export function rowEnd(r: AnyRow): number | null {
  switch (r.type) {
    case 'call':
      return r.end_ms
    case 'tool':
      // FALLBACK-OK: TRN-41 (a tool with no end time is drawn from its start for its duration, else as a 2 px mark)
      return r.end_ms ?? (r.dur_ms === null ? r.start_ms : r.start_ms + r.dur_ms)
    case 'agent':
      return r.runs.length === 0 ? null : Math.max(...r.runs.map((x) => x.end_ms))
    case 'gap':
      return r.end_ms
  }
}

function walk(
  rows: readonly AnyRow[], base: { ancestors: readonly string[]; depth: number }, pages: ReadonlyMap<string, AgentPage>, out: RowNode[],
): void {
  // The agent rows of this list that have rows after them, by depth; a row is inside the nearest one shallower than itself.
  const chain: { id: string; depth: number }[] = []
  rows.forEach((r, i) => {
    if (r.type === 'gap' && r.dur_ms < GAP_MIN_MS) return // SES-16: a stretch becomes a gap row at 2 min
    const depth = r.type === 'gap' ? base.depth : r.depth
    while (chain.length > 0 && chain[chain.length - 1].depth >= depth) chain.pop()
    const ancestors = [...base.ancestors, ...chain.map((c) => c.id)]
    out.push({ id: r.id, row: r, depth, ancestors })
    if (r.type !== 'agent') return
    const next = rows[i + 1]
    if (next !== undefined && next.type !== 'gap' && next.depth > depth) {
      chain.push({ id: r.id, depth }) // its rows follow inline in this list
      return
    }
    const page = pages.get(r.id)
    if (page) walk(page.rows, { ancestors: [...ancestors, r.id], depth: depth + 1 }, pages, out)
  })
}

/**
 * The flat list of the turn's rows (API-22, depth 0) with every loaded agent page spliced in after its agent row. A gap of under 2 min is
 * left out (SES-16). Rows inside a page keep the `depth` they came with; a gap in a page takes the page's level.
 */
export function buildNodes(top: readonly AnyRow[], pages: ReadonlyMap<string, AgentPage> = new Map()): RowNode[] {
  const out: RowNode[] = []
  walk(top, { ancestors: [], depth: 0 }, pages, out)
  return out
}

// ---- figures -----------------------------------------------------------------------------------------------------

/** TRN-33: in a zoom an agent row's cost and tokens cover only its calls that start inside the range. */
export interface AgentRange {
  cost_usd: number | null
  tok_total: number | null
  n_calls_in_range: number
  n_calls: number
}

export function rangeAgentMap(range: TurnRows['range']): ReadonlyMap<string, AgentRange> | undefined {
  return range === null ? undefined : new Map(range.agents.map((a) => [a.row, a]))
}

/** The row's cost for the Cost order and the Cost bar. Tools and gaps have none (SYS-31). */
export function costOf(r: AnyRow, ra?: ReadonlyMap<string, AgentRange>): number | null {
  if (r.type === 'call') return r.cost_usd
  if (r.type === 'agent') return ra?.get(r.id)?.cost_usd ?? r.cost_usd // FALLBACK-OK: TRN-33 (outside a zoom the whole engagement cost; an agent the range does not list has none in range)
  return null
}

/** The row's time: a call's model time, a tool's duration, an agent's run time, a gap's length. */
export function timeOf(r: AnyRow): number | null {
  switch (r.type) {
    case 'call':
    case 'tool':
    case 'gap':
      return r.dur_ms
    case 'agent':
      return r.run_ms
  }
}

export function tokensOf(r: AnyRow, ra?: ReadonlyMap<string, AgentRange>): number | null {
  if (r.type === 'call') return r.tok.total
  if (r.type === 'agent') return ra?.get(r.id)?.tok_total ?? r.tok_total // FALLBACK-OK: TRN-33 (as costOf)
  return null
}

/** The value a measure takes on a row (TRN-31 bar, TRN-14 sort). */
export function valueOf(r: AnyRow, measure: Bars, ra?: ReadonlyMap<string, AgentRange>): number | null {
  return measure === 'cost' ? costOf(r, ra) : measure === 'time' ? timeOf(r) : tokensOf(r, ra)
}

/** The measure the Ledger columns follow: the order when one is set (TRN-11), else the URL's bars. */
export function activeMeasure(order: TurnOrder | null, bars: Bars): Bars {
  return order === 'cost' ? 'cost' : order === 'duration' ? 'time' : bars
}

// ---- items -------------------------------------------------------------------------------------------------------

export type BandState = { status: 'loading' } | { status: 'error'; message: string } | { status: 'ready'; total: number; loaded: number }

export type Item =
  | { type: 'row'; key: string; node: RowNode }
  /** TRN-37: a run of 4 or more quiet rows. */
  | { type: 'fold'; key: string; nodes: RowNode[]; calls: number; tools: number; cost: number | null; firstMs: number | null; lastMs: number | null }
  /** TRN-14: "Show N more" under the Cost or Duration order, with the hidden rows' cost. */
  | { type: 'more'; key: string; kind: 'order'; n: number; cost: number | null }
  /** TRN-35 (D38 M8): the next page of an agent's rows. */
  | { type: 'more'; key: string; kind: 'band'; agent: string; n: number; level: number }
  /** TRN-35: an agent's rows are loading (skeleton rows) or failed (error card with Retry). */
  | { type: 'band'; key: string; agent: string; status: 'loading' | 'error'; message: string | null; level: number }

export interface ViewOpts {
  /** Agent rows whose rows are shown (`l`). */
  openAgents: ReadonlySet<string>
  zoom: MsRange | null
  /** The row ids a search matches, or null with no search (SES-32). */
  search: ReadonlySet<string> | null
  order: TurnOrder | null
  /** "Show N more" was clicked (TRN-14). */
  showAll: boolean
  /** Quiet runs the user opened, by fold key (`foldKey`). */
  unfolded: ReadonlySet<string>
  /** The cursor row: a quiet run that holds it stays open (TRN-37). */
  cursor?: string | null
  /** The turn's cost, for the quiet rule's 2 % (TRN-37). */
  turnCost?: number | null
  rangeAgents?: ReadonlyMap<string, AgentRange>
  /** Load state of each open agent's page (TRN-35). */
  bands?: ReadonlyMap<string, BandState>
}

export const foldKey = (firstId: string): string => `fold-${firstId}`

/** TRN-12: a row starts inside the zoom; a gap overlaps it. */
export function inZoom(r: AnyRow, zoom: MsRange | null): boolean {
  if (zoom === null) return true
  if (r.type === 'gap') return r.end_ms > zoom.start_ms && r.start_ms < zoom.end_ms
  const s = rowStart(r)
  return s !== null && s >= zoom.start_ms && s < zoom.end_ms
}

/** TRN-37: a main-thread call or tool that is not a skill load, not failed, takes under 60 s and, for a call, costs under 2 % of the turn. */
export function isQuiet(n: RowNode, turnCost: number | null): boolean {
  if (n.depth !== 0) return false
  const r = n.row
  if (r.type === 'tool') return r.skill_load === null && failureText(r) === null && (r.dur_ms ?? 0) < QUIET_TIME_MS // FALLBACK-OK: TRN-64 (a tool with no duration took no measurable time)
  if (r.type !== 'call') return false
  if ((r.dur_ms ?? 0) >= QUIET_TIME_MS) return false // FALLBACK-OK: TRN-64 (a call with no model time took none)
  const cost = r.cost_usd ?? 0 // FALLBACK-OK: SYS-31 (a call with no cost costs nothing)
  return turnCost !== null && turnCost > 0 ? cost < QUIET_COST_SHARE * turnCost : true
}

const compare = (a: number, b: number): number => b - a

/**
 * TRN-14: the main-thread rows sorted by the order's measure. Cost: calls and agents by cost, descending; tools and gaps (no cost) last, in
 * their original order. Duration: rows with a time by time, descending; rows without one next; tools that wait on the user or sat silent for
 * 5 min or more last (SES Open Q8).
 */
export function sortedByOrder(nodes: readonly RowNode[], order: TurnOrder, ra?: ReadonlyMap<string, AgentRange>): RowNode[] {
  const main = nodes.filter((n) => n.depth === 0)
  const keyed = main.map((n, i) => {
    const excluded = order === 'duration' && n.row.type === 'tool' && excludedFromTimeRanking(n.row as ToolRow)
    const v = excluded ? null : order === 'cost' ? costOf(n.row, ra) : timeOf(n.row)
    return { n, i, v, excluded }
  })
  const rank = (k: { v: number | null; excluded: boolean }) => (k.excluded ? 2 : k.v === null ? 1 : 0)
  keyed.sort((p, q) => rank(p) - rank(q) || (p.v !== null && q.v !== null ? compare(p.v, q.v) : 0) || p.i - q.i)
  return keyed.map((k) => k.n)
}

function sumCost(nodes: readonly RowNode[], ra?: ReadonlyMap<string, AgentRange>): number | null {
  let sum: number | null = null
  for (const n of nodes) {
    const c = costOf(n.row, ra)
    if (c !== null) sum = (sum ?? 0) + c // FALLBACK-OK: SYS-38 (a sum starts at zero)
  }
  return sum
}

/**
 * The items of the row list.
 * - A row shows when it is not inside a folded agent, starts inside the zoom (a gap overlaps it) and matches the search (TRN-12).
 * - Order "As it ran": the rows in order, each open agent's rows right below it; its load state as a band item (skeleton, error) and, when the
 *   page is not complete, a "Show N more" item after its last row (TRN-35). Runs of quiet rows fold (TRN-37) unless a search is active.
 * - Order Cost or Duration: main-thread rows only, the first 15 and a fold with the hidden cost (TRN-14).
 */
export function visibleRows(nodes: readonly RowNode[], o: ViewOpts): Item[] {
  const shown = nodes.filter(
    (n) => n.ancestors.every((a) => o.openAgents.has(a)) && inZoom(n.row, o.zoom) && (o.search === null || o.search.has(n.id)),
  )
  if (o.order !== null) {
    const sorted = sortedByOrder(shown, o.order, o.rangeAgents)
    const take = o.showAll ? sorted : sorted.slice(0, ORDER_TOP)
    const items: Item[] = take.map((node) => ({ type: 'row', key: node.id, node }))
    const hidden = sorted.length - take.length
    if (hidden > 0) items.push({ type: 'more', key: 'more-order', kind: 'order', n: hidden, cost: sumCost(sorted.slice(ORDER_TOP), o.rangeAgents) })
    return items
  }
  const items = withBands(shown, nodes, o)
  return o.search !== null ? items : foldQuiet(items, o)
}

function withBands(shown: readonly RowNode[], all: readonly RowNode[], o: ViewOpts): Item[] {
  // The band of an agent sits one level inside it.
  const levelOf = (agent: string): number => (all.find((n) => n.id === agent)?.ancestors.length ?? 0) + 1 // FALLBACK-OK: TRN-35 (an agent that is not listed sits on the main thread)
  // The last shown row inside each agent, so its "Show N more" goes right after it, innermost agent first.
  const lastIn = new Map<number, string[]>()
  const last = new Map<string, number>()
  shown.forEach((n, i) => n.ancestors.forEach((a) => last.set(a, i)))
  for (const [agent, i] of last) lastIn.set(i, [agent, ...(lastIn.get(i) ?? [])]) // FALLBACK-OK: TRN-35 (the first agent that ends at a row starts its list)
  const out: Item[] = []
  const more = (agent: string): void => {
    const b = o.bands?.get(agent)
    if (b?.status === 'ready' && b.total > b.loaded) out.push({ type: 'more', key: `more-${agent}`, kind: 'band', agent, n: b.total - b.loaded, level: levelOf(agent) })
  }
  shown.forEach((node, i) => {
    out.push({ type: 'row', key: node.id, node })
    if (node.row.type === 'agent' && o.openAgents.has(node.id)) {
      const b = o.bands?.get(node.id)
      if (b?.status === 'loading') out.push({ type: 'band', key: `band-${node.id}`, agent: node.id, status: 'loading', message: null, level: levelOf(node.id) })
      else if (b?.status === 'error') out.push({ type: 'band', key: `band-${node.id}`, agent: node.id, status: 'error', message: b.message, level: levelOf(node.id) })
      else if (!last.has(node.id)) more(node.id) // ready, but none of its rows is shown (a zoom or search hides them)
    }
    // Deepest first: `lastIn` was built from outermost to innermost, so reverse it.
    const closing = lastIn.get(i)
    if (closing) [...closing].sort((p, q) => depthOf(shown, q) - depthOf(shown, p)).forEach(more)
  })
  return out
}

function depthOf(shown: readonly RowNode[], agent: string): number {
  const n = shown.find((x) => x.id === agent)
  return n === undefined ? 0 : n.depth
}

function foldQuiet(items: Item[], o: ViewOpts): Item[] {
  const out: Item[] = []
  let run: Extract<Item, { type: 'row' }>[] = []
  const flush = (): void => {
    const key = run.length > 0 ? foldKey(run[0].node.id) : ''
    const holds = run.some((r) => r.node.id === o.cursor)
    if (run.length >= QUIET_RUN_MIN && !holds && !o.unfolded.has(key)) {
      const nodes = run.map((r) => r.node)
      out.push({
        type: 'fold',
        key,
        nodes,
        calls: nodes.filter((n) => n.row.type === 'call').length,
        tools: nodes.filter((n) => n.row.type === 'tool').length,
        cost: sumCost(nodes),
        firstMs: rowStart(nodes[0].row),
        lastMs: rowStart(nodes[nodes.length - 1].row),
      })
    } else out.push(...run)
    run = []
  }
  for (const it of items) {
    if (it.type === 'row' && isQuiet(it.node, o.turnCost ?? null)) run.push(it) // FALLBACK-OK: TRN-37 (a turn with no cost has no 2 % threshold)
    else {
      flush()
      out.push(it)
    }
  }
  flush()
  return out
}

// ---- position and cursor ---------------------------------------------------------------------------------------------------

/** Every row of the list in order, the rows inside quiet folds included: these are the cursor's stops. */
export function flatRows(items: readonly Item[]): RowNode[] {
  return items.flatMap((it) => (it.type === 'row' ? [it.node] : it.type === 'fold' ? it.nodes : []))
}

/** The fold key of the quiet run that holds `rowId`, or null (a finder or `j`/`k` that lands in a folded run unfolds it, TRN-37). */
export function foldHolding(items: readonly Item[], rowId: string): string | null {
  // FALLBACK-OK: TRN-37 (no folded run holds the row)
  return items.find((it): it is Extract<Item, { type: 'fold' }> => it.type === 'fold' && it.nodes.some((n) => n.id === rowId))?.key ?? null
}

/** TRN-12, SES-70: "row R of M" counts the rows of the current list in its current order. `r` is 0 when the row is not in the list. */
export function positionOf(rowId: string | null, items: readonly Item[]): { r: number; m: number } {
  const rows = flatRows(items)
  return { r: rowId === null ? 0 : rows.findIndex((n) => n.id === rowId) + 1, m: rows.length }
}

/** The cursor to keep after the list changed: the same row when it is still there, else the first row (TRN-14, TRN-24). */
export function settleCursor(items: readonly Item[], cursor: string | null): string | null {
  const rows = flatRows(items)
  if (cursor !== null && rows.some((n) => n.id === cursor)) return cursor
  return rows.length === 0 ? null : rows[0].id // FALLBACK-OK: TRN-62 (an empty list has no cursor)
}

export type Move = { kind: 'to'; id: string } | { kind: 'edge'; dir: 1 | -1 } | { kind: 'none' }

/**
 * `j`/`k` (delta ±1) and `J`/`K` (±10) over the list's rows (TRN-50, TRN-51). A single step off either end is an `edge` (the caller hands off to
 * the next turn); a 10-row step stops at the end. A cursor outside the list lands on the first row (or the last, going back).
 */
export function moveInList(rows: readonly RowNode[], cursor: string | null, delta: number): Move {
  if (rows.length === 0) return { kind: 'none' }
  const at = cursor === null ? -1 : rows.findIndex((n) => n.id === cursor)
  if (at < 0) return { kind: 'to', id: rows[delta < 0 ? rows.length - 1 : 0].id }
  const to = at + delta
  if (to >= 0 && to < rows.length) return { kind: 'to', id: rows[to].id }
  if (Math.abs(delta) === 1) return { kind: 'edge', dir: delta > 0 ? 1 : -1 }
  const edge = rows[delta > 0 ? rows.length - 1 : 0].id
  return edge === cursor ? { kind: 'none' } : { kind: 'to', id: edge }
}

/** Gap rows at the main thread or inside a band carry no depth on the wire; this is their indent level for drawing. */
export function indentLevel(n: RowNode): number {
  return Math.min(n.depth, MAX_INDENT)
}
