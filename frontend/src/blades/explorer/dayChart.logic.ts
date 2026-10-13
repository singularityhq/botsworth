import type { ColorSlot, Day, DayBar, SessionRow } from '../../api/types'
import { addDays, diffDays, mondayOf } from '../../lib/day'
import { fmt } from '../../lib/format'
import type { ChartWindow, DayRange } from '../../state/period'
import type { Measure } from '../../state/url'

/** Day chart rules (EXP-32 to EXP-41): columns from the window, stacks by colour slot, the max rule, drag and keyboard selection,
 *  tooltip content, the session footprint. No React. */

export const PLOT_H = 200
/** Room above the tallest bar for its value label (EXP-36). */
export const LABEL_H = 18

/** Stack order: slots 1 to 6 bottom to top, then "other" (EXP-33). */
export const slotOrder = (slot: ColorSlot | null): number => slot ?? 7

export interface Segment {
  slot: ColorSlot | null
  value: number
}

export interface ProjectValue {
  project: string
  name: string
  slot: ColorSlot | null
  value: number
}

export interface Column {
  from: Day
  to: Day
  bucket: 'day' | 'week'
  total: number
  segments: Segment[]
  /** The projects of the column, highest first, for the tooltip. */
  projects: ProjectValue[]
  inPeriod: boolean
  /** An 8 px gap precedes a Monday day column (EXP-33). */
  monday: boolean
  lowerBound: boolean
  nUnpriced: number
  nSessions: number
}

const measureOf = (m: Measure, cost: number, tok: number): number => (m === 'cost' ? cost : tok)

function weekStarts(win: ChartWindow): { from: Day; to: Day }[] {
  const out: { from: Day; to: Day }[] = []
  let from = win.from
  while (diffDays(from, win.to) >= 0) {
    const nextMon = addDays(mondayOf(from), 7)
    const to = diffDays(nextMon, win.to) >= 0 ? addDays(nextMon, -1) : win.to
    out.push({ from, to })
    from = addDays(to, 1)
  }
  return out
}

/** One column per day of the window (EXP-32); week columns, clipped to the window, for the week bucket (D41 M10). A day or week the
 *  server sent no row for is an empty column. */
export function buildColumns(win: ChartWindow, rows: readonly DayBar[], period: DayRange, measure: Measure): Column[] {
  const byFrom = new Map(rows.map((r) => [r.from_day, r]))
  const spans =
    win.bucket === 'week'
      ? weekStarts(win)
      : Array.from({ length: diffDays(win.from, win.to) + 1 }, (_, i) => {
          const d = addDays(win.from, i)
          return { from: d, to: d }
        })
  return spans.map((span) => {
    const row = byFrom.get(span.from)
    const bySlot = new Map<number, Segment>()
    const projects: ProjectValue[] = []
    for (const p of row?.by_project ?? []) {
      const value = measureOf(measure, p.cost_usd, p.tok_total)
      projects.push({ project: p.project, name: p.project_name, slot: p.color_slot, value })
      const k = slotOrder(p.color_slot)
      const seg = bySlot.get(k)
      if (seg) seg.value += value
      else bySlot.set(k, { slot: p.color_slot, value })
    }
    projects.sort((a, b) => b.value - a.value || a.name.localeCompare(b.name))
    const segments = [...bySlot.entries()].sort((a, b) => a[0] - b[0]).map(([, s]) => s).filter((s) => s.value > 0)
    const from = period.from
    const to = period.to
    // all time (both null) puts every column in the period; a week column counts when it overlaps the period (its dates are already clipped)
    const inPeriod = from === null || to === null ? true : diffDays(from, span.to) >= 0 && diffDays(span.from, to) >= 0
    return {
      from: span.from,
      to: span.to,
      bucket: win.bucket,
      total: row ? measureOf(measure, row.cost_usd, row.tok_total) : 0,
      segments,
      projects,
      inPeriod,
      monday: win.bucket === 'day' && mondayOf(span.from) === span.from,
      lowerBound: row?.cost_lower_bound ?? false, // FALLBACK-OK: explorer.md EXP-32 (a day with no row has no calls: no lower bound)
      nUnpriced: row?.n_unpriced ?? 0, // FALLBACK-OK: explorer.md EXP-32 (a day with no row has no unpriced calls)
      nSessions: row?.n_sessions ?? 0, // FALLBACK-OK: explorer.md EXP-32 (a day with no row has no sessions)
    }
  })
}

/** The largest column of the window; the bars scale to it (EXP-33) and the dashed rule marks it (EXP-36). */
export function windowMax(cols: readonly Column[]): number {
  return cols.reduce((m, c) => Math.max(m, c.total), 0)
}

/** The highest column inside the period: the only one with a value label (EXP-36). The first wins a tie. Null when none has a value. */
export function peakIndex(cols: readonly Column[]): number | null {
  let best: number | null = null
  cols.forEach((c, i) => {
    if (c.inPeriod && c.total > 0 && (best === null || c.total > cols[best].total)) best = i
  })
  return best
}

/** Bar height in px: linear from 0 to the window max (EXP-33). A column with a value is at least 2 px tall. */
export function barHeight(total: number, max: number, plotH: number = PLOT_H): number {
  if (max <= 0 || total <= 0) return 0
  return Math.max(2, (total / max) * plotH)
}

/** Value text for the measure: money in Cost, a token count in Tokens. */
export function measureText(value: number, measure: Measure, lowerBound = false): string {
  return measure === 'cost' ? fmt.money(value, { lowerBound }) : fmt.tokens(value)
}

// ---- labels -----------------------------------------------------------------

/** The label under a column (EXP-34): the day number. Week columns (more than 92 days) carry the month on the first column of each month. */
export function columnLabel(col: Column, prev: Column | null): string {
  if (col.bucket === 'day') return String(Number(col.from.slice(8, 10)))
  if (prev !== null && prev.from.slice(0, 7) === col.from.slice(0, 7)) return ''
  return fmt.dateRange(col.from, col.from).split(' ')[0]
}

/** `Wed Oct 7` for a day; `Oct 6 – Oct 12` for the dates a week column covers (D41 M10). */
export function columnTitle(col: Column): string {
  return col.from === col.to ? fmt.date(col.from) : fmt.dateRange(col.from, col.to)
}

/** The column's accessible name, `Wed Oct 7, $549.34` (EXP-34). */
export function columnName(col: Column, measure: Measure): string {
  return `${columnTitle(col)}, ${measureText(col.total, measure, col.lowerBound)}`
}

export interface Tip {
  title: string
  total: string
  lines: { name: string; slot: ColorSlot | null; value: string }[]
  more: number
}

/** The day tooltip (EXP-37): the title and total, up to 4 projects, then "N more". */
export function tooltipModel(col: Column, measure: Measure): Tip {
  const shown = col.projects.filter((p) => p.value > 0)
  return {
    title: `Calls on ${columnTitle(col)}`, // D51: the bar counts calls that started that day; the header counts whole sessions
    total: measureText(col.total, measure, col.lowerBound),
    lines: shown.slice(0, 4).map((p) => ({ name: p.name, slot: p.slot, value: measureText(p.value, measure) })),
    more: Math.max(0, shown.length - 4),
  }
}

// ---- selection by pointer and keyboard (EXP-38, EXP-38a) --------------------

/** The period that covers columns `a` to `b`, in either order. */
export function columnsRange(cols: readonly Column[], a: number, b: number): { from: Day; to: Day } {
  const lo = Math.min(a, b)
  const hi = Math.max(a, b)
  return { from: cols[lo].from, to: cols[hi].to }
}

/** A press and release on the same column is a click (EXP-38). */
export function isClick(anchor: number, release: number): boolean {
  return anchor === release
}

export interface KeyState {
  focus: number
  /** The column a shift-extension started from; null when no extension is running. */
  anchor: number | null
}

export interface KeyResult {
  state: KeyState
  /** The period to apply now, or null when the key only moves focus. */
  range: { from: Day; to: Day } | null
}

/** ←/→ move focus; Enter sets the period to the focused column; shift+←/→ extends the period from the focused column (EXP-38a).
 *  Null for a key the chart does not use. */
export function chartKey(state: KeyState, key: string, shift: boolean, cols: readonly Column[]): KeyResult | null {
  const last = cols.length - 1
  if (key === 'Enter') return { state: { focus: state.focus, anchor: null }, range: columnsRange(cols, state.focus, state.focus) }
  if (key !== 'ArrowLeft' && key !== 'ArrowRight') return null
  const focus = Math.max(0, Math.min(last, state.focus + (key === 'ArrowRight' ? 1 : -1)))
  if (!shift) return { state: { focus, anchor: null }, range: null }
  const anchor = state.anchor ?? state.focus
  return { state: { focus, anchor }, range: columnsRange(cols, anchor, focus) }
}

// ---- footprint of a hovered session (EXP-40) ---------------------------------

export interface Footprint {
  /** The session's share inside this column, in the measure. */
  value: number
}

/** Columns in which the session made calls, with its share of each (EXP-40). Week columns add the days they cover. */
export function footprint(row: Pick<SessionRow, 'day_split'>, cols: readonly Column[], measure: Measure): Map<number, Footprint> {
  const out = new Map<number, Footprint>()
  cols.forEach((c, i) => {
    let value = 0
    let hit = false
    for (const d of row.day_split) {
      if (diffDays(c.from, d.day) >= 0 && diffDays(d.day, c.to) >= 0) {
        hit = true
        value += measureOf(measure, d.cost_usd, d.tok_total)
      }
    }
    if (hit) out.set(i, { value })
  })
  return out
}

/** Where the session's box sits inside a bar: bottom offset and height, in px (EXP-40). The box lies in the session's project slot. */
export function footprintBox(col: Column, slot: ColorSlot | null, value: number, max: number, plotH: number = PLOT_H): { bottom: number; height: number } | null {
  const k = slotOrder(slot)
  const seg = col.segments.find((s) => slotOrder(s.slot) === k)
  if (!seg || max <= 0) return null
  const below = col.segments.filter((s) => slotOrder(s.slot) < k).reduce((a, s) => a + s.value, 0)
  const height = Math.min(seg.value, value)
  return { bottom: (below / max) * plotH, height: Math.max(3, (height / max) * plotH) }
}
