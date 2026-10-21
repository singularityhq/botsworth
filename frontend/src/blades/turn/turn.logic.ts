import type { AnyRow, GapRow, RowRef, SessionDetail, TurnRow } from '../../api/types'
import { fmt } from '../../lib/format'
import type { Bars, MsRange } from '../../state/url'
import { excludedFromTimeRanking, clip } from '../shared/finders'
import { makeAxis, tzOffsetMs, type Axis } from '../shared/histogram.logic'
import { gapFolds } from '../shared/waterfall.logic'
import { GAP_MIN_MS, gapState } from '../shared/states'
import type { Rich } from '../shared/toastText'
import { hasRows } from '../session/turnList.logic'
import type { AnswersCost } from '../shared/Answers'
import { flatRows, valueOf, type AgentRange, type Item, type RowNode } from './rowList.logic'

/**
 * Small pure rules of the turn blade that are not about the row list: column grids, the clock with seconds, the "big figure" rule.
 * The later sections add the histogram rule, the Waterfall axis, the status text and the step-through.
 */

// ---- grid (TRN-11) -----------------------------------------------------------------------------------------------------

/** TRN-11: below this blade width the Ledger drops the measure bar and narrows its columns. */
export const NARROW_PX = 860

export type LedgerColumn = 'when' | 'glyph' | 'name' | 'flags' | 'bar' | 'cost' | 'time' | 'tokens'

const WIDE: Record<LedgerColumn, string> = { when: '76px', glyph: '20px', name: 'minmax(0,1fr)', flags: '96px', bar: '104px', cost: '72px', time: '72px', tokens: '56px' }
const NARROW: Record<Exclude<LedgerColumn, 'bar'>, string> = { when: '72px', glyph: '20px', name: 'minmax(0,1fr)', flags: '56px', cost: '64px', time: '64px', tokens: '64px' }

export interface LedgerGrid {
  ids: LedgerColumn[]
  template: string
}

/**
 * TRN-11: `76px 20px minmax(0,1fr) 96px 104px 72px 72px 56px` (When, glyph, name, flags, bar, Cost, Time, Tokens) from 860 px; narrower:
 * `72px 20px minmax(0,1fr) 56px 64px 64px 64px`, the bar column dropped (the spec lists seven tracks for eight cells; the bar is the one
 * that goes, as the prototype's container rule hides it).
 */
export function ledgerGrid(width: number): LedgerGrid {
  if (width >= NARROW_PX) {
    const ids: LedgerColumn[] = ['when', 'glyph', 'name', 'flags', 'bar', 'cost', 'time', 'tokens']
    return { ids, template: ids.map((c) => WIDE[c]).join(' ') }
  }
  const ids: LedgerColumn[] = ['when', 'glyph', 'name', 'flags', 'cost', 'time', 'tokens']
  return { ids, template: ids.map((c) => NARROW[c as keyof typeof NARROW]).join(' ') }
}

/** TRN-40 (SES-40): Waterfall columns When, glyph, name (300 px, 200 under 860), the track, Time. */
export function waterfallGrid(width: number): string {
  return width >= NARROW_PX ? '76px 20px minmax(0,300px) minmax(0,1fr) 64px' : '64px 20px minmax(0,200px) minmax(0,1fr) 56px'
}

// ---- figures -----------------------------------------------------------------------------------------------------------

/** TRN-31 (`.big`): the figure of the active measure is 600 ink1 when it is at least a quarter of the list's largest. */
export function isBig(value: number | null, max: number): boolean {
  return value !== null && max > 0 && value >= 0.25 * max
}

/** TRN-30: the start clock with seconds, `HH:MM:SS`, in the browser zone. */
export function clockSec(ms: number, tz: string): string {
  return new Date(ms + tzOffsetMs(tz, ms)).toISOString().slice(11, 19)
}

// ---- heights and scales of the list ---------------------------------------------------------------------------------------

/** SYS-22 dense rows, the Waterfall's 32 px rows (SES-40), the band's error card. */
export const ROW_H = 36
export const ROW_H_WF = 32
export const BAND_ERROR_H = 128

export function itemHeight(it: Item, row = ROW_H): number {
  return it.type === 'band' ? (it.status === 'loading' ? ROW_H * 3 : BAND_ERROR_H) : row
}

/** Band nesting of an item (TRN-35): 0 on the main thread. */
export function itemLevel(it: Item): number {
  return it.type === 'row' ? it.node.ancestors.length : it.type === 'band' || (it.type === 'more' && it.kind === 'band') ? it.level : 0
}

/** The list's largest value per measure, over the rows it shows: the scale of the bars and the `.big` rule (SYS-39, TRN-31). */
export function maxByMeasure(items: readonly Item[], ra?: ReadonlyMap<string, AgentRange>): Record<Bars, number> {
  const max: Record<Bars, number> = { cost: 0, time: 0, tokens: 0 }
  for (const n of flatRows(items)) for (const m of ['cost', 'time', 'tokens'] as const) max[m] = Math.max(max[m], valueOf(n.row, m, ra) ?? 0) // FALLBACK-OK: SYS-31 (a row with no figure adds nothing to the scale)
  return max
}

/** The index of the item that holds the cursor: its row, or the quiet fold it is inside. -1 when it is in none. */
export function cursorIndexOf(items: readonly Item[], cursor: string | null): number {
  if (cursor === null) return -1
  return items.findIndex((it) => (it.type === 'row' ? it.node.id === cursor : it.type === 'fold' && it.nodes.some((n) => n.id === cursor)))
}

// ---- the histogram rule (TRN-20) -------------------------------------------------------------------------------------------

/** TRN-20: a turn of this wall or more shows the histogram by default. */
export const HIST_WALL_MS = 30 * 60_000

/** TRN-20: the histogram shows with at least one agent row or a wall of 30 min or more; `thist` in the URL overrides the rule either way. */
export function histogramShown(nAgentRows: number, wallMs: number | null, thist: boolean | null): boolean {
  return thist ?? (nAgentRows >= 1 || (wallMs ?? 0) >= HIST_WALL_MS) // FALLBACK-OK: SES-85 (a turn with no wall has no wall rule)
}

/** TRN-2, TRN-20: the "Over time" disclosure is in the meta line only when the rule does not show the chart, or the URL overrides it. */
export function overTimeToggleShown(nAgentRows: number, wallMs: number | null, thist: boolean | null): boolean {
  return thist !== null || !histogramShown(nAgentRows, wallMs, null)
}

// ---- the turn axis (TRN-21, D41 N1) ---------------------------------------------------------------------------------------------

export interface TurnAxisSpan {
  a: number
  b: number
  /** Where the tail zone starts: the turn's end, when an agent ran past it. */
  tailFrom: number | null
}

/** D41 N1: from the turn's start to the later of its end and the latest agent run end. A turn with no start or end has no axis. */
export function turnAxisSpan(t: Pick<TurnRow, 'start_ms' | 'end_ms' | 'agent_runs'>): TurnAxisSpan | null {
  if (t.start_ms === null || t.end_ms === null) return null
  const latest = Math.max(t.end_ms, ...t.agent_runs.map((r) => r.end_ms))
  return { a: t.start_ms, b: Math.max(latest, t.start_ms + 1), tailFrom: latest > t.end_ms ? t.end_ms : null }
}

// ---- moving between turns (TRN-50, TRN-53) ----------------------------------------------------------------------------------------

export interface NumberedStop {
  ix: number
  row: TurnRow
}

/**
 * The numbered turn `j`/`k` or `t`/`T` swap in: the next (or previous) one that has rows to show. With a search active a turn needs a match;
 * otherwise it needs a model call, agent, skill load or failure. null when there is none, so the cursor is at the session's edge.
 */
export function stepToTurn(stops: readonly NumberedStop[], fromIx: number, dir: 1 | -1, kept: ReadonlySet<string> | null): NumberedStop | null {
  const at = stops.findIndex((s) => s.ix === fromIx)
  if (at < 0) return null
  for (let i = at + dir; i >= 0 && i < stops.length; i += dir) {
    const s = stops[i]
    if (kept === null ? hasRows(s.row) : kept.has(s.row.turn)) return s
  }
  return null
}

/** TRN-50: "Turn 1 → turn 2: <turn text, 70 chars>". */
export function handoffToast(from: number, to: { ix: number; row: TurnRow }): Rich {
  return [`Turn ${from} → turn ${to.ix}: ${clip(to.row.label, 70)}`]
}

export function sessionEdgeToast(dir: 1 | -1): Rich {
  return [dir > 0 ? 'Last row of the session.' : 'First row of the session.']
}

// ---- legend jump inside the turn (TRN-4) ----------------------------------------------------------------------------------------

type Longest = SessionDetail['session']['longest']

/** The longest main-thread call, tool and gap of each state (`you` also counts tool rows that waited on the user) in this turn: what a legend click in the turn blade goes to (R3-proto `jumpState(k, "t")`). */
export function longestInTurn(rows: readonly AnyRow[], turn: string): Longest {
  const none: Longest = { model: null, tool: null, agents: null, you: null, agentmsg: null, limit: null, none: null }
  const out = { ...none }
  const take = (state: keyof Longest, row: RowRef, dur: number | null) => {
    if (dur === null || (out[state] !== null && out[state]!.dur_ms >= dur)) return
    out[state] = { row, turn, dur_ms: dur }
  }
  for (const r of rows) {
    if (r.type === 'call' && r.depth === 0) take('model', { type: 'call', id: r.id }, r.dur_ms)
    else if (r.type === 'tool' && r.depth === 0 && r.user_wait !== null) take('you', { type: 'tool', id: r.id }, r.dur_ms) // QRY-29: a main-thread call that waited on the user is a `you` target (D54)
    else if (r.type === 'tool' && r.depth === 0 && !excludedFromTimeRanking(r)) take('tool', { type: 'tool', id: r.id }, r.dur_ms)
    else if (r.type === 'gap' && r.dur_ms >= GAP_MIN_MS) take(gapState(r), { type: 'gap', id: r.id }, r.dur_ms)
  }
  return out
}

// ---- the status bar (SES-70, TRN-52) ---------------------------------------------------------------------------------------------------

export type SkillCounter =
  | { kind: 'here'; skill: string }
  | { kind: 'after'; skill: string; n: number; /** The load is in the previous numbered turn: its number. */ inTurn: number | null; plusMs: number | null }

const levelOfNode = (n: RowNode): string | null => n.ancestors[n.ancestors.length - 1] ?? null // FALLBACK-OK: TRN-52 (a row outside every agent is on the main thread)

/**
 * TRN-52: the skill counter. `nodes` are the open turn's rows as they ran; `prev` the previous numbered turn's main-thread rows. A skill load at
 * the cursor's own level (the main thread, or the same agent) at or before the cursor counts; N is the rows at that level from the load to the
 * cursor, gap rows excluded. The previous turn counts only for a cursor on the main thread.
 */
export function skillCounter(nodes: readonly RowNode[], cursor: string | null, prev: { ix: number; nodes: readonly RowNode[] } | null): SkillCounter | null {
  const at = cursor === null ? -1 : nodes.findIndex((n) => n.id === cursor)
  if (at < 0) return null
  const here = nodes[at]
  const level = levelOfNode(here)
  const same = nodes.slice(0, at + 1).filter((n) => levelOfNode(n) === level && n.row.type !== 'gap')
  const isLoad = (n: RowNode) => n.row.type === 'tool' && n.row.skill_load !== null
  const loadAt = (list: readonly RowNode[]) => list.map(isLoad).lastIndexOf(true)
  const own = loadAt(same)
  const skillOf = (n: RowNode) => (n.row.type === 'tool' ? n.row.skill_load! : '')
  const startOf = (n: RowNode) => (n.row.type === 'gap' ? n.row.start_ms : n.row.type === 'agent' ? n.row.runs[0]?.start_ms ?? null : n.row.start_ms) // FALLBACK-OK: TRN-64 (an agent with no run has no start)
  const plus = (load: RowNode) => {
    const a = startOf(load)
    const b = startOf(here)
    return a === null || b === null || b < a ? null : b - a
  }
  if (own >= 0) {
    const load = same[own]
    if (load.id === here.id) return { kind: 'here', skill: skillOf(load) }
    return { kind: 'after', skill: skillOf(load), n: same.length - 1 - own, inTurn: null, plusMs: plus(load) }
  }
  if (level !== null || prev === null) return null
  const before = prev.nodes.filter((n) => levelOfNode(n) === null && n.row.type !== 'gap')
  const pl = loadAt(before)
  if (pl < 0) return null
  return { kind: 'after', skill: skillOf(before[pl]), n: before.length - 1 - pl + same.length, inTurn: prev.ix, plusMs: plus(before[pl]) }
}

/** SES-70: the skill counter's text. */
export function skillCounterText(c: SkillCounter): { skill: string; before: string; after: string } {
  if (c.kind === 'here') return { skill: c.skill, before: '', after: ' loaded here' }
  return {
    skill: c.skill,
    before: `row ${c.n} after `,
    after: ` loaded${c.inTurn !== null ? ` in turn ${c.inTurn}` : ''}${c.plusMs !== null ? ` · +${fmt.dur(c.plusMs)}` : ''}`,
  }
}

// ---- the cost answer before the rows arrive (TRN-60) -------------------------------------------------------------------------------

/** The turn's cost answer from the session's turn row: the figure shows at once, before the turn's own rows arrive. */
export function costFromRow(t: TurnRow): AnswersCost {
  return {
    cost_usd: t.cost_usd, cost_lower_bound: t.cost_lower_bound, n_unpriced: t.n_unpriced, main: t.cost_main,
    agents: t.cost_usd !== null && t.cost_main !== null ? Math.max(0, t.cost_usd - t.cost_main) : null,
  }
}

// ---- the Waterfall axis (TRN-40 to TRN-42) ----------------------------------------------------------------------------------------

/** TRN-40, D38 M3: waits of 60 s or more fold. */
export const TURN_FOLD_MIN_MS = 60_000
/** SES-40: a fold column is 24 px unless the view says otherwise. */
export const TURN_FOLD_PX = 24

/** TRN-42: while zoomed the axis is the zoom range in real time. */
export function turnWaterfallAxis(turn: TurnRow, gaps: readonly GapRow[], zoom: MsRange | null, real: boolean, trackW: number): { axis: Axis; tailFrom: number | null } | null {
  if (trackW <= 0) return null
  if (zoom) return { axis: makeAxis(zoom.start_ms, zoom.end_ms, trackW, [], TURN_FOLD_PX), tailFrom: null }
  const span = turnAxisSpan(turn)
  if (!span) return null
  return { axis: makeAxis(span.a, span.b, trackW, gapFolds(gaps, span.a, span.b, TURN_FOLD_MIN_MS, real), TURN_FOLD_PX), tailFrom: span.tailFrom }
}


/** TRN-4, TRN-24 (R3 §7): the caveat on the cost answer while the turn is zoomed. */
export const ZOOM_COST_NOTE = "In a range, cost counts model calls that started inside it. The turn's total counts the whole cost of the agents it started."
