import type { Histogram, Stretch } from '../../api/types'
import { fmt } from '../../lib/format'
import { modelFamily, type ModelFamily } from '../../lib/model'
import type { Bars, MsRange } from '../../state/url'
import { gapState, STATE_KEYS, type StateKey } from './states'

/**
 * The time axis and the geometry of the Over time histogram (SES-29, SES-30, TRN-21). Pure: wire data in, pixel numbers out.
 * The same axis serves the Waterfall (SES-40), so the Waterfall logic imports it from here.
 */

/** SES-29: a wait of 10 min or more folds to one fixed column. */
export const HIST_FOLD_MIN_MS = 600_000
export const HIST_FOLD_PX = 24
/** SES-29: bins are 3 px apart and 2 px wide. */
export const BIN_PITCH = 3
export const BIN_WIDTH = 2
/** SES-30, TRN-24: a drag shorter than this sets nothing. */
export const BRUSH_MIN_PX = 4
/** SES-29: a non-empty bin is at least this tall. */
export const MIN_BAR_PX = 2
/** SES-29: axis labels are at least this far apart. */
export const LABEL_MIN_PX = 44

export interface Fold {
  a: number
  b: number
}

export interface Axis {
  a: number
  b: number
  width: number
  folds: readonly Fold[]
  /** Width in px of one fold after the cap on the share of the axis the folds may take. */
  foldPx: number
  /** Position in px of an instant; clamped to the axis. */
  x(t: number): number
  /** The instant at a position in px (inverse of `x`; a position inside a fold maps linearly across it). */
  inv(px: number): number
}

/** Waits (stretches of any state but `agents`) of at least `minMs`, clipped to [a, b], in time order. Agents-only stretches are never folded: something ran. */
export function waitFolds(stretches: readonly Stretch[], a: number, b: number, minMs: number): Fold[] {
  return stretches
    .filter((s) => s.state !== 'agents' && s.dur_ms >= minMs && s.end_ms > a && s.start_ms < b)
    .map((s) => ({ a: Math.max(a, s.start_ms), b: Math.min(b, s.end_ms) }))
    .filter((f) => f.b > f.a)
    .sort((p, q) => p.a - q.a)
}

/** Folds never take more than this share of the axis, so a session of very many waits keeps room for its bars. */
const MAX_FOLD_SHARE = 0.5

/**
 * The axis from `a` to `b` over `width` px. Each fold takes `foldPx` (capped so all folds together stay within half the axis); the rest of
 * the time is linear. With no folds the axis is plain real time.
 */
export function makeAxis(a: number, b: number, width: number, folds: readonly Fold[], foldPx: number): Axis {
  if (!(b > a)) throw new Error(`makeAxis: empty span ${a}..${b}`)
  const fpx = folds.length === 0 ? foldPx : Math.min(foldPx, (width * MAX_FOLD_SHARE) / folds.length)
  const foldedMs = folds.reduce((t, f) => t + (f.b - f.a), 0)
  const k = (width - folds.length * fpx) / Math.max(1, b - a - foldedMs) // px per ms outside folds
  const x = (t0: number): number => {
    const t = Math.max(a, Math.min(b, t0))
    let px = 0
    let last = a
    for (const f of folds) {
      if (t <= f.a) break
      px += (f.a - last) * k
      if (t < f.b) return px + (fpx * (t - f.a)) / (f.b - f.a)
      px += fpx
      last = f.b
    }
    return px + (t - last) * k
  }
  const inv = (px0: number): number => {
    const px = Math.max(0, Math.min(width, px0))
    let pos = 0
    let last = a
    for (const f of folds) {
      const run = (f.a - last) * k
      if (px < pos + run) return last + (px - pos) / k
      pos += run
      if (px < pos + fpx) return f.a + ((px - pos) / fpx) * (f.b - f.a)
      pos += fpx
      last = f.b
    }
    return Math.min(b, k > 0 ? last + (px - pos) / k : last)
  }
  return { a, b, width, folds, foldPx: fpx, x, inv }
}

// ---- bars ---------------------------------------------------------------------------------------------------

/** What a measure sums in a bin. SES-29: cost for Cost and for Time, tokens for Tokens. */
/** The left edge of the "after the turn" label: 6 px into the tail, moved left as far as it needs for the whole text to fit in `width` (FX2 item 7). */
export function tailLabelX(tailX: number, textW: number, width: number): number {
  return Math.max(0, Math.min(tailX + 6, width - textW - 4))
}

export function binField(bars: Bars): 'cost_usd' | 'tok_total' {
  return bars === 'tokens' ? 'tok_total' : 'cost_usd'
}

type BinLike = { cost_usd: number; tok_total: number }

/**
 * The bin values summed into 3 px columns along the drawn axis. A bin narrower than a column adds to the column it falls in; a bin wider
 * than a column (it crosses a fold, or the axis is zoomed) shares its value over the columns it covers in proportion to the px each gets,
 * so the sum is kept.
 */
export function binColumns(
  h: { start_ms: number; bin_ms: number; bins: readonly BinLike[] }, axis: Axis, field: 'cost_usd' | 'tok_total', pitch = BIN_PITCH,
): Float64Array {
  const cols = new Float64Array(Math.max(1, Math.ceil(axis.width / pitch)))
  h.bins.forEach((bin, i) => {
    const v = bin[field]
    if (!(v > 0)) return
    const t0 = Math.max(axis.a, h.start_ms + i * h.bin_ms)
    const t1 = Math.min(axis.b, h.start_ms + (i + 1) * h.bin_ms)
    if (t1 <= t0) return
    spread(cols, axis.x(t0), axis.x(t1), v, pitch)
  })
  return cols
}

function spread(cols: Float64Array, x0: number, x1: number, v: number, pitch: number): void {
  const last = cols.length - 1
  const c0 = Math.min(last, Math.floor(x0 / pitch))
  const c1 = Math.min(last, Math.floor(Math.max(x0, x1 - 1e-9) / pitch))
  if (c0 === c1 || x1 <= x0) {
    cols[c0] += v
    return
  }
  const span = x1 - x0
  for (let c = c0; c <= c1; c++) {
    const lo = Math.max(x0, c * pitch)
    const hi = Math.min(x1, (c + 1) * pitch)
    if (hi > lo) cols[c] += (v * (hi - lo)) / span
  }
}

/** SES-29: height is linear in the value (no square root), 2 px at least for a non-empty bin, 0 for none. */
export function barHeight(value: number, max: number, avail: number): number {
  if (!(value > 0) || !(max > 0)) return 0
  return Math.max(MIN_BAR_PX, (value / max) * avail)
}

/** SES-22: stack order, bottom to top. */
export const MODEL_STACK: readonly ModelFamily[] = ['opus', 'sonnet', 'fable', 'haiku', 'gpt', 'other']

/** TRN-22: per family, the 3 px columns of the by-model bins (turn only). Index 0 of `by_model` is cost, 1 is tokens. */
export function familyColumns(h: Histogram, axis: Axis, bars: Bars, pitch = BIN_PITCH): Map<ModelFamily, Float64Array> {
  const out = new Map<ModelFamily, Float64Array>()
  const slot = bars === 'tokens' ? 1 : 0
  h.bins.forEach((bin, i) => {
    if (!bin.by_model) return
    const t0 = Math.max(axis.a, h.start_ms + i * h.bin_ms)
    const t1 = Math.min(axis.b, h.start_ms + (i + 1) * h.bin_ms)
    if (t1 <= t0) return
    for (const [model, pair] of Object.entries(bin.by_model)) {
      const v = pair[slot]
      if (!(v > 0)) continue
      const fam = modelFamily(model)
      let cols = out.get(fam)
      if (!cols) {
        cols = new Float64Array(Math.max(1, Math.ceil(axis.width / pitch)))
        out.set(fam, cols)
      }
      spread(cols, axis.x(t0), axis.x(t1), v, pitch)
    }
  })
  return out
}

/** Segment heights of one stacked column, bottom to top in SES-22 order. Each present family gets at least 1 px; the whole stack is linear against `max`. */
export function stackHeights(values: readonly { family: ModelFamily; value: number }[], max: number, avail: number): { family: ModelFamily; h: number }[] {
  const total = values.reduce((a, v) => a + v.value, 0)
  const full = barHeight(total, max, avail)
  if (full === 0) return []
  return MODEL_STACK.flatMap((family) => {
    const v = values.find((x) => x.family === family)?.value ?? 0 // FALLBACK-OK: SES-22 (a family absent from this column has no segment)
    return v > 0 ? [{ family, h: Math.max(1, (v / total) * full) }] : []
  })
}

// ---- state lane (SES-29) -----------------------------------------------------------------------------------------

export interface LaneSegment {
  state: StateKey
  x0: number
  x1: number
}

/** The state of a bin: the one with the most time, its substates summed; ties go in SES-15 order. A bin with no state time has none. */
export function dominantState(s: Histogram['bins'][number]['state_ms']): StateKey | null {
  const ms: Record<StateKey, number> = { model: 0, tool: 0, agents: 0, you: 0, agentmsg: 0, limit: 0, none: 0 }
  for (const p of s.parts) ms[p.state] += p.ms
  let best: StateKey | null = null
  let bestMs = 0
  for (const state of STATE_KEYS) {
    if (ms[state] > bestMs) {
      best = state
      bestMs = ms[state]
    }
  }
  return best
}

/**
 * The lane in draw order: first each bin by its dominant state (adjacent bins of one state merge), then the stored stretches over them in
 * their labelled state, so a `none` bin shows as you, agentmsg or limit where a stretch says so.
 */
export function laneSegments(h: Histogram, axis: Axis): LaneSegment[] {
  const out: LaneSegment[] = []
  let run: { state: StateKey; t0: number; t1: number } | null = null
  const flush = () => {
    if (run) out.push({ state: run.state, x0: axis.x(run.t0), x1: axis.x(run.t1) })
    run = null
  }
  h.bins.forEach((bin, i) => {
    const state = dominantState(bin.state_ms)
    const t0 = Math.max(axis.a, h.start_ms + i * h.bin_ms)
    const t1 = Math.min(axis.b, h.start_ms + (i + 1) * h.bin_ms)
    if (state === null || t1 <= t0) {
      flush()
      return
    }
    if (run && run.state === state && run.t1 === t0) run.t1 = t1
    else {
      flush()
      run = { state, t0, t1 }
    }
  })
  flush()
  for (const s of h.stretches) {
    const t0 = Math.max(axis.a, s.start_ms)
    const t1 = Math.min(axis.b, s.end_ms)
    if (t1 <= t0) continue
    out.push({ state: gapState(s), x0: axis.x(t0), x1: axis.x(t1) })
  }
  return out
}

// ---- axis labels (SES-29) --------------------------------------------------------------------------------------------

const STEPS_MIN = [5, 15, 30, 60, 120, 240, 480] as const
const MAX_LABELS = 9

/** The first step of 5, 15, 30, 60, 120, 240, 480 min that gives fewer than 9 labels over the span; 480 when none does. */
export function labelStepMs(spanMs: number): number {
  const step = STEPS_MIN.find((m) => spanMs / (m * 60_000) < MAX_LABELS) ?? STEPS_MIN[STEPS_MIN.length - 1]
  return step * 60_000
}

const offsetFmt = new Map<string, Intl.DateTimeFormat>()
/** Offset of `tz` from UTC at `utcMs`, in ms. */
export function tzOffsetMs(tz: string, utcMs: number): number {
  let f = offsetFmt.get(tz)
  if (!f) {
    f = new Intl.DateTimeFormat('en-US', { timeZone: tz, hourCycle: 'h23', year: 'numeric', month: 'numeric', day: 'numeric', hour: 'numeric', minute: 'numeric', second: 'numeric' })
    offsetFmt.set(tz, f)
  }
  const parts = f.formatToParts(utcMs)
  const get = (t: string): number => {
    const v = parts.find((p) => p.type === t)?.value
    if (v === undefined) throw new Error(`no ${t} in the formatted date for zone ${tz}`)
    return Number(v)
  }
  return Date.UTC(get('year'), get('month') - 1, get('day'), get('hour'), get('minute'), get('second')) - Math.floor(utcMs / 1000) * 1000
}

export interface AxisLabel {
  t: number
  x: number
  text: string
}

/**
 * Clock labels at multiples of `step` in the browser zone, left to right, at least `minPx` apart, none inside a fold (SES-29).
 * `avoid` lists pixel ranges that must stay clear (the Waterfall's fold marks).
 */
export function axisLabels(axis: Axis, tz: string, step = labelStepMs(axis.b - axis.a), minPx = LABEL_MIN_PX, avoid: readonly [number, number][] = []): AxisLabel[] {
  const out: AxisLabel[] = []
  const off = tzOffsetMs(tz, axis.a)
  let lastPx = Number.NEGATIVE_INFINITY
  for (let t = Math.ceil((axis.a + off) / step) * step - off; t < axis.b; t += step) {
    if (axis.folds.some((f) => t > f.a && t < f.b)) continue
    const x = axis.x(t)
    if (x - lastPx < minPx) continue
    if (avoid.some(([lo, hi]) => x > lo && x < hi)) continue
    lastPx = x
    out.push({ t, x, text: fmt.clock(t, tz) })
  }
  return out
}

// ---- brush (SES-30, TRN-24) ------------------------------------------------------------------------------------------------

/** A drag from `px0` to `px1` (either order) as a range of instants, or null when it is shorter than 4 px. */
export function brushRange(px0: number, px1: number, axis: Axis): MsRange | null {
  const lo = Math.min(px0, px1)
  const hi = Math.max(px0, px1)
  if (hi - lo < BRUSH_MIN_PX) return null
  const start_ms = Math.round(axis.inv(lo))
  const end_ms = Math.round(axis.inv(hi))
  return end_ms > start_ms ? { start_ms, end_ms } : null
}

// ---- caption and label text ----------------------------------------------------------------------------------------------

/** The caption's measure word: Time bars show cost (SES-29). */
export function barsWord(bars: Bars): 'cost' | 'tokens' {
  return bars === 'tokens' ? 'tokens' : 'cost'
}

/** SES-29 / spec 12: the `aria-label` states the range and the totals. */
export function histogramLabel(h: Histogram, bars: Bars, tz: string, axis: Axis): string {
  const sum = (f: 'cost_usd' | 'tok_total') => h.bins.reduce((a, b) => a + b[f], 0)
  const total = bars === 'tokens' ? `${fmt.tokens(sum('tok_total'))} tokens` : fmt.money(sum('cost_usd'))
  return `Over time, ${fmt.range(axis.a, axis.b, tz)}: ${total} by ${barsWord(bars)}, ${h.marks.turn_starts?.length ?? 0} turn starts, ${h.marks.skill_loads.length} skill loads` // FALLBACK-OK: interfaces.md API-31 (turn_starts is session only)
}

/** The axis a histogram covers: `[start_ms, start_ms + bins * bin_ms]`. */
export function histogramSpan(h: Pick<Histogram, 'start_ms' | 'bin_ms' | 'bins'>): { a: number; b: number } {
  return { a: h.start_ms, b: h.start_ms + h.bins.length * h.bin_ms }
}

// ---- hover (TRN-22) --------------------------------------------------------------------------------------------------------------

/** The API bin under a pixel position, or null outside the bins. */
export function binIndexAt(h: Pick<Histogram, 'start_ms' | 'bin_ms' | 'bins'>, axis: Axis, px: number): number | null {
  const i = Math.floor((axis.inv(px) - h.start_ms) / h.bin_ms)
  return i >= 0 && i < h.bins.length ? i : null
}

export interface BinTip {
  /** `HH:MM–HH:MM`, the bin's clock range. */
  range: string
  /** The bin's total for the measure, formatted. */
  total: string
  /** Per family, in the stack order (turn only; empty for a session). */
  families: { family: ModelFamily; text: string }[]
}

/** Text of the hover tooltip: the bin's clock range, its total, and its total per model family when the bins carry `by_model`. */
export function binTip(h: Histogram, i: number, bars: Bars, tz: string): BinTip {
  const bin = h.bins[i]
  const t0 = h.start_ms + i * h.bin_ms
  const text = (cost: number, tok: number) => (bars === 'tokens' ? `${fmt.tokens(tok)} tokens` : fmt.money(cost))
  const per = new Map<ModelFamily, [number, number]>()
  for (const [model, [cost, tok]] of Object.entries(bin.by_model ?? {})) { // FALLBACK-OK: interfaces.md API-31 (by_model is turn only; a session bin has no per-family split)
    const fam = modelFamily(model)
    const [c, t] = per.get(fam) ?? [0, 0] // FALLBACK-OK: SES-22 (the first model of a family starts its sum at zero)
    per.set(fam, [c + cost, t + tok])
  }
  return {
    range: `${fmt.clock(t0, tz)}–${fmt.clock(t0 + h.bin_ms, tz)}`,
    total: text(bin.cost_usd, bin.tok_total),
    families: MODEL_STACK.flatMap((family) => {
      const v = per.get(family)
      return v ? [{ family, text: text(v[0], v[1]) }] : []
    }),
  }
}

/** TRN-22: each family present in the by-model bins with its total for the measure, in stack order. Time bars show cost (SES-29). */
export function familyTotals(h: Pick<Histogram, 'bins'>, bars: Bars): { family: ModelFamily; value: number }[] {
  const slot = bars === 'tokens' ? 1 : 0
  const sums = new Map<ModelFamily, number>()
  for (const bin of h.bins) {
    for (const [model, pair] of Object.entries(bin.by_model ?? {})) { // FALLBACK-OK: interfaces.md API-31 (by_model is turn only)
      const fam = modelFamily(model)
      sums.set(fam, (sums.get(fam) ?? 0) + pair[slot]) // FALLBACK-OK: SES-22 (the first bin of a family starts its sum at zero)
    }
  }
  return MODEL_STACK.flatMap((family) => (sums.has(family) && sums.get(family)! > 0 ? [{ family, value: sums.get(family)! }] : []))
}

// ---- range cost (SES-13, D47) -----------------------------------------------------------------------------------------------------

export interface RangeCost {
  cost_usd: number
  main: number
  agents: number
}

/**
 * The clock rule inside a brushed range (SES-13): the cost of model calls that start inside the range. A bin counts when it starts in
 * [start, end) (per-bin resolution, D47). Main thread = Σ `main_cost_usd`; agents = Σ `cost_usd` - Σ `main_cost_usd`.
 */
export function rangeCost(h: Pick<Histogram, 'start_ms' | 'bin_ms' | 'bins'>, range: MsRange): RangeCost {
  let total = 0
  let main = 0
  h.bins.forEach((bin, i) => {
    const t = h.start_ms + i * h.bin_ms
    if (t >= range.start_ms && t < range.end_ms) {
      total += bin.cost_usd
      main += bin.main_cost_usd
    }
  })
  return { cost_usd: total, main, agents: Math.max(0, total - main) }
}
