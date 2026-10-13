import type { AnyRow, FindResult, ToolRow, TurnRow } from '../../api/types'
import { fmt } from '../../lib/format'
import type { Patch } from '../../state/nav'
import type { Bars, MsRange, TurnOrder } from '../../state/url'
import type { IconName } from '../../ui/glyphs'
import type { Rich } from './toastText'

/**
 * The finder engine (SES-50 to SES-57): one hit set per kind from API-25, next and previous with wrap, the `b` ranking, the toasts,
 * and the plan for landing on a row. Blade-agnostic: the session blade starts a walk from its cursor turn, the turn blade (F4a) from its
 * cursor row. Pure; the hook that fetches is `useFinders`.
 */

export type FinderKind = 'waits' | 'failures' | 'skills' | 'agents'
export const FINDER_KINDS: readonly FinderKind[] = ['waits', 'failures', 'skills', 'agents']

export interface FinderInfo {
  /** Singular noun for the toasts: "<What> N of M", "No <what> in this session." */
  what: string
  /** Chip word by count (SES-31). */
  one: string
  many: string
  icon: IconName
  key: string
  /** `e` has no previous-failure key (SES-54); `a` has none either (SES-56). */
  prevKey: string | null
}

export const FINDERS: Record<FinderKind, FinderInfo> = {
  waits: { what: 'wait', one: 'wait', many: 'waits', icon: 'hourglass', key: 'w', prevKey: 'W' },
  failures: { what: 'failure', one: 'failed', many: 'failed', icon: 'circle-alert', key: 'e', prevKey: null },
  skills: { what: 'skill load', one: 'skill load', many: 'skill loads', icon: 'book-open', key: 's', prevKey: 'S' },
  agents: { what: 'agent', one: 'agent', many: 'agents', icon: 'bot', key: 'a', prevKey: null },
}

export type Hit = FindResult['hits'][number]

/** Position of each numbered turn in session order (turns by time). Hits in a turn that is not here (a harness turn) are skipped (SES-51). */
export type TurnPositions = ReadonlyMap<string, number>

export function turnPositions(turnsInOrder: readonly { turn: string }[]): TurnPositions {
  return new Map(turnsInOrder.map((t, i) => [t.turn, i]))
}

// ---- the hit set ---------------------------------------------------------------------------------------------------------

/**
 * The hits a finder visits: the API's hits (already in session order, QRY-38) in numbered turns, and with a search active only rows that
 * match it (SES-51). `matching` is the set of row ids of the search's hits, or null when no search applies.
 */
export function hitPool(result: FindResult, positions: TurnPositions, matching: ReadonlySet<string> | null): Hit[] {
  return result.hits.filter((h) => positions.has(h.turn) && (matching === null || matching.has(h.row.id)))
}

export function matchingRows(search: FindResult | undefined): ReadonlySet<string> | null {
  return search ? new Set(search.hits.map((h) => h.row.id)) : null
}

/**
 * The first hit that breaks SES-51's session order (turns by time), or null. API-25 says hits come in session order, so the engine
 * trusts it; this check exists so a disagreement is reported with one example, never silently re-sorted.
 */
export function orderViolation(pool: readonly Hit[], positions: TurnPositions): { index: number; hit: Hit } | null {
  let last = -1
  for (let i = 0; i < pool.length; i++) {
    const p = positions.get(pool[i].turn)
    if (p === undefined) throw new Error(`finders: hit ${pool[i].row.id} is in a turn outside the session order`)
    if (p < last) return { index: i, hit: pool[i] }
    last = p
  }
  return null
}

// ---- walking (SES-51) ----------------------------------------------------------------------------------------------------------

/** Where a walk starts. The session blade has no row: it starts at its cursor turn. The turn blade starts after its cursor row. */
export type From =
  | { at: 'turn'; turn: string }
  /** The session rail with a turn open on a row a finder landed on: continue from that hit. A row not in this hit set falls back to the turn start. */
  | { at: 'hit'; turn: string; row: string }
  | {
      at: 'row'
      turn: string
      row: string
      /** Ids of every row of that turn in As-it-ran order, rows inside agents after their agent row. */
      order: readonly string[]
      /**
       * Depth-0 agent rows of that turn with their start. A hit in this turn that is not in `order` sits inside an agent whose rows are not
       * loaded: it is placed just after the latest agent row that started at or before it, so the walk neither skips nor repeats it.
       */
      agentStarts?: readonly { id: string; start_ms: number }[]
    }

/**
 * The next (dir 1) or previous (dir -1) hit, wrapping at the ends.
 * - From a turn: next is the first hit at or after the start of that turn, previous the last hit before it.
 * - From a row: next is the first hit after that row, previous the last hit before it.
 * - From a hit: next is the hit after it in the set, previous the one before it, wrapping.
 * - No start (`null`): next is the first hit, previous the last.
 */
export function stepHit(pool: readonly Hit[], positions: TurnPositions, from: From | null, dir: 1 | -1): { hit: Hit; index: number } | null {
  if (pool.length === 0) return null
  const pick = (i: number) => ({ hit: pool[i], index: i })
  if (from === null) return dir > 0 ? pick(0) : pick(pool.length - 1)
  if (from.at === 'hit') {
    const at = pool.findIndex((h) => h.row.id === from.row)
    if (at >= 0) return pick((at + dir + pool.length) % pool.length)
    return stepHit(pool, positions, { at: 'turn', turn: from.turn }, dir)
  }
  const cp = positions.get(from.turn)
  if (cp === undefined) throw new Error(`finders: the cursor turn ${from.turn} is not a numbered turn of the session`)
  const rowIx = (h: Hit): number => {
    if (from.at !== 'row') return 0
    const at = from.order.indexOf(h.row.id)
    if (at >= 0) return at
    let owner = -1
    for (const a of from.agentStarts ?? []) if (h.start_ms !== null && a.start_ms <= h.start_ms) owner = Math.max(owner, from.order.indexOf(a.id))
    return owner + 0.5
  }
  // A cursor row that is not in the order (a row folded away) starts the walk at the turn's start.
  const curIx = from.at === 'row' ? from.order.indexOf(from.row) : 0
  // `after`: the hit lies after the start point. For a turn start the turn's own rows count as after it.
  const after = (h: Hit): boolean => {
    const p = positions.get(h.turn)!
    if (p !== cp) return p > cp
    return from.at === 'turn' ? true : rowIx(h) > curIx
  }
  const before = (h: Hit): boolean => {
    const p = positions.get(h.turn)!
    if (p !== cp) return p < cp
    return from.at === 'turn' ? false : rowIx(h) < curIx
  }
  if (dir > 0) {
    const i = pool.findIndex(after)
    return pick(i < 0 ? 0 : i)
  }
  for (let i = pool.length - 1; i >= 0; i--) if (before(pool[i])) return pick(i)
  return pick(pool.length - 1)
}

// ---- toasts (SES-51, SES-52, SES-71) -------------------------------------------------------------------------------------------

export function clip(text: string, max: number): string {
  return text.length <= max ? text : `${text.slice(0, max - 1)}…`
}

const capital = (s: string) => s[0].toUpperCase() + s.slice(1)

/** SES-51: "<What> N of M: **<row name, 60 chars>** · turn K", a wait gets "· **<duration>**". `index` is 0-based. */
export function finderToast(kind: FinderKind, index: number, total: number, hit: Hit): Rich {
  const out: Rich = [`${capital(FINDERS[kind].what)} ${index + 1} of ${total}: `, { b: clip(hit.label, 60), plain: true }, ` · turn ${turnNumber(hit)}`]
  if (kind === 'waits' && hit.value !== null) out.push(' · ', { b: fmt.dur(hit.value) })
  return out
}

/** SES-51: nothing is hit at all. */
export function noHitToast(kind: FinderKind): Rich {
  return [`No ${FINDERS[kind].what} in this session.`]
}

/** `T12` → `12` (QRY-2: a numbered turn's display id is T<n>). */
export function turnNumber(hit: Pick<Hit, 'display_id'>): string {
  const m = hit.display_id === null ? null : /^T(\d+)$/.exec(hit.display_id)
  return m ? m[1] : '–' // FALLBACK-OK: SES-51 (a hit with no display number prints a dash; harness turns are not walked)
}

// ---- b / B, Biggest (SES-52) -------------------------------------------------------------------------------------------------------

/** The value a measure takes on a turn (SES-12 cost, SES-21 wall, tokens); null when the turn has none. */
export function turnValue(t: TurnRow, bars: Bars): number | null {
  return bars === 'cost' ? t.cost_usd : bars === 'time' ? t.wall_ms : t.tok_total
}

/** Turns with a value, biggest first; equal values keep their list order. */
export function rankTurns<T extends TurnRow>(turns: readonly T[], bars: Bars): T[] {
  return turns
    .map((t, i) => ({ t, i, v: turnValue(t, bars) }))
    .filter((x): x is { t: T; i: number; v: number } => x.v !== null) // FALLBACK-OK: SES-85 (a turn with no value for the measure is left out of the ranking)
    .sort((p, q) => q.v - p.v || p.i - q.i)
    .map((x) => x.t)
}

/** Next position in a ranking: the first `b` (-1 before it) lands on the biggest; `B` before the first stays on the biggest; clamped at the ends. */
export function rankStep(pos: number, delta: 1 | -1, n: number): number {
  return Math.max(0, Math.min(n - 1, pos + delta))
}

/** 1st, 2nd, 3rd, 4th, 11th, 21st. */
export function ordinal(n: number): string {
  const r = n % 100
  if (r >= 11 && r <= 13) return `${n}th`
  return `${n}${({ 1: 'st', 2: 'nd', 3: 'rd' } as Record<number, string>)[n % 10] ?? 'th'}` // FALLBACK-OK: English ordinals (every other last digit takes "th")
}

export function measureValueText(v: number, bars: Bars): string {
  return bars === 'cost' ? fmt.money(v) : bars === 'time' ? fmt.dur(v) : fmt.tokens(v)
}

const MEASURE_WORD: Record<Bars, string> = { cost: 'cost', time: 'time', tokens: 'tokens' }

/** SES-52 session blade: "Biggest turn by cost: **$24.69** · turn 23", then "2nd biggest turn by cost: …". `rank` is 0-based. */
export function biggestTurnToast(rank: number, bars: Bars, value: number, turnIx: number): Rich {
  const lead = rank === 0 ? 'Biggest' : `${ordinal(rank + 1)} biggest`
  return [`${lead} turn by ${MEASURE_WORD[bars]}: `, { b: measureValueText(value, bars) }, ` · turn ${turnIx}`]
}

/** SES-52 turn blade: "Biggest row by cost of 412: **$1.77** · turn 2 · <row name, 50 chars>". `rank` is 0-based, `total` the ranking's size. */
export function biggestRowToast(rank: number, total: number, bars: Bars, hit: Hit): Rich {
  const lead = rank === 0 ? 'Biggest' : `${ordinal(rank + 1)} biggest`
  return [`${lead} row by ${MEASURE_WORD[bars]} of ${total}: `, { b: measureValueText(hit.value ?? 0, bars) }, ` · turn ${turnNumber(hit)} · ${clip(hit.label, 50)}`] // FALLBACK-OK: interfaces.md QRY-38 (a biggest hit always has a value; 0 prints if it did not)
}

/** SES-52: tool rows that wait on the user, or whose derived time hides a silence of 5 min or more, do not rank by time (DM-40, Open Q8). */
export function excludedFromTimeRanking(t: ToolRow): boolean {
  return t.waits_on_user || (t.max_silence_ms !== null && t.max_silence_ms >= 300_000)
}

/** The value a main-thread row takes on a measure (SES-52): calls by cost, time or tokens; tools by time; agent rows with their whole figure; gaps by time. */
export function rowValue(r: AnyRow, bars: Bars): number | null {
  switch (r.type) {
    case 'call':
      return bars === 'cost' ? r.cost_usd : bars === 'time' ? r.dur_ms : r.tok.total
    case 'tool':
      return bars === 'time' && !excludedFromTimeRanking(r) ? r.dur_ms : null
    case 'agent':
      return bars === 'cost' ? r.cost_usd : bars === 'time' ? r.run_ms : r.tok_total
    case 'gap':
      return bars === 'time' ? r.dur_ms : null
  }
}

/** Main-thread rows (depth 0) with a value above 0, biggest first; equal values keep their order. */
export function rankRows(rows: readonly AnyRow[], bars: Bars): AnyRow[] {
  return rows
    .map((r, i) => ({ r, i, v: 'depth' in r && r.depth !== 0 ? null : rowValue(r, bars) }))
    .filter((x): x is { r: AnyRow; i: number; v: number } => x.v !== null && x.v > 0) // FALLBACK-OK: SES-52 (rows with no value, or none above 0, are not ranked)
    .sort((p, q) => q.v - p.v || p.i - q.i)
    .map((x) => x.r)
}

// ---- landing on a row (SES-50, SES-57) ---------------------------------------------------------------------------------------

export interface GoContext {
  /** The URL as it stands: the open turn (or null), its zoom and order, and whether the detail blade is open. */
  current: { t: number | null; zoom: MsRange | null; order: TurnOrder | null; detail: boolean }
  /** The row is in the turn's current Cost or Duration list. The session blade does not know and passes false; F4a passes its list's answer. */
  rowInOrderedList?: boolean
}

export interface GoPlan {
  /** The URL change: the turn, the row, and the zoom, order and detail steps of SES-50. Applied with `openBlade('turn', …)` when no turn is open, `replace` otherwise (SES-57). */
  patch: Patch
  /** SES-50 step 1: the row sits inside an agent; the turn blade shows that agent's rows inline. Not in the URL. */
  showAgents: boolean
  /** SES-50 step 4: the turn blade opens the quiet fold that hides the row, if one does. Not in the URL. */
  openFold: true
}

/** Whether a finder opens the detail blade when it lands: only `w` does (SES-53). */
export function opensDetail(kind: FinderKind | 'biggest'): boolean {
  return kind === 'waits'
}

/**
 * SES-50: "go to a row", as data. `turnIx` is the display number of the hit's turn.
 * 2. a zoom hides the row (the turn changes, or the row starts outside the zoom): the zoom resets;
 * 3. the Order is Cost or Duration and the row is not in that list: Order goes back to "As it ran";
 * 6. the detail blade keeps its state, except where the finder opens it.
 * A hit on the turn itself (a slash turn with no step, SES-55) opens the turn at its first row: `step` is null.
 */
export function goPlan(hit: Hit, kind: FinderKind | 'biggest', turnIx: number, ctx: GoContext): GoPlan {
  const { current } = ctx
  const sameTurn = current.t === turnIx
  const patch: Patch = {
    t: turnIx,
    step: hit.row.type === 'turn' ? null : hit.row.id,
    detail: opensDetail(kind) ? true : current.detail,
  }
  if (current.zoom !== null) {
    const outside = hit.start_ms !== null && (hit.start_ms < current.zoom.start_ms || hit.start_ms >= current.zoom.end_ms)
    if (!sameTurn || outside) patch.zoom = null
  }
  if (current.order !== null && ctx.rowInOrderedList !== true) patch.order = null
  return { patch, showAgents: hit.depth > 0, openFold: true }
}
