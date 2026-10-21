import type { GapRow, TurnRow } from '../../api/types'
import { fmt } from '../../lib/format'
import { axisLabels, makeAxis, type Axis, type AxisLabel, type Fold } from './histogram.logic'
import { gapState } from './states'

/**
 * The Waterfall pattern (SES-40): an axis with folded breaks, the fold marks in the header, bars of at least 2 px, agent tails.
 * The session Waterfall (SES-42) and the turn Waterfall (TRN-40) both use it; only the fold threshold and break width differ.
 */

/** SES-42 (Kai ruling 2026-10-12): the session Waterfall folds waits of 2 min or more into 12 px breaks. */
export const SESSION_FOLD_MIN_MS = 120_000
export const SESSION_FOLD_PX = 12
/** SES-42: marks closer than about this merge into one. */
export const MARK_MERGE_PX = 10
/** SES-40: a bar is at least this wide. */
export const MIN_BAR_PX = 2
/** SES-42: a thin agent tail. */
export const TAIL_PX = 2

/** The gaps that fold: wait states (every stretch but agents-only and background commands) of at least `minMs`, clipped to the axis. Real time folds nothing. */
export function gapFolds(gaps: readonly GapRow[], a: number, b: number, minMs: number, realTime: boolean): Fold[] {
  if (realTime) return []
  return gaps
    .filter((g) => g.state !== 'agents' && g.state !== 'tool' && g.dur_ms >= minMs && g.end_ms > a && g.start_ms < b)
    .map((g) => ({ a: Math.max(a, g.start_ms), b: Math.min(b, g.end_ms) }))
    .filter((f) => f.b > f.a)
    .sort((p, q) => p.a - q.a)
}

export function sessionAxis(a: number, b: number, width: number, gaps: readonly GapRow[], realTime: boolean): Axis {
  return makeAxis(a, b, width, gapFolds(gaps, a, b, SESSION_FOLD_MIN_MS, realTime), SESSION_FOLD_PX)
}

export interface FoldMark {
  /** Left and right edge in px; the right edge is the break's end. */
  x0: number
  x1: number
  n: number
  ms: number
  /** Tooltip text (SES-42). */
  title: string
  /** Visible text: `≈` or `≈×N`. */
  text: string
}

/** SES-42 tooltip: "N waits of <total> folded", one wait "Wait of <duration> folded". */
export function markTitle(n: number, ms: number): string {
  return n === 1 ? `Wait of ${fmt.dur(ms)} folded` : `${n} waits of ${fmt.dur(ms)} folded`
}

/** Folds whose breaks start less than `MARK_MERGE_PX` after the previous mark ends merge into one mark "≈×N". */
export function foldMarks(axis: Axis): FoldMark[] {
  const out: FoldMark[] = []
  for (const f of axis.folds) {
    const x0 = axis.x(f.a)
    const x1 = x0 + axis.foldPx
    const last = out[out.length - 1]
    if (last && x0 - last.x1 < MARK_MERGE_PX) {
      last.x1 = x1
      last.n += 1
      last.ms += f.b - f.a
    } else {
      out.push({ x0, x1, n: 1, ms: f.b - f.a, title: '', text: '' })
    }
  }
  for (const m of out) {
    m.title = markTitle(m.n, m.ms)
    m.text = m.n === 1 ? '≈' : `≈×${m.n}`
  }
  return out
}

/** Clock labels of the header row (SES-40): the SES-29 steps. The fold marks have their own row below, so labels need not avoid them. */
export function headerLabels(axis: Axis, tz: string): AxisLabel[] {
  return axisLabels(axis, tz)
}

export interface BarBox {
  left: number
  width: number
}

/** SES-40: a bar from `startMs` to `endMs` on the axis, at least 2 px wide. */
export function barBox(axis: Axis, startMs: number, endMs: number, minPx = MIN_BAR_PX): BarBox {
  const left = axis.x(startMs)
  return { left, width: Math.max(minPx, axis.x(endMs) - left) }
}

export interface Tail extends BarBox {
  agent: string | null
  endMs: number
  /** Tooltip text (SES-42): "<agent>: ran to HH:MM". */
  title: string
}

/**
 * SES-42: one thin tail per agent run that ends after the turn's end, from the turn's end to the run's end. The shortest is drawn first,
 * so the longest is drawn last (on top).
 */
export function agentTails(turn: Pick<TurnRow, 'end_ms' | 'agent_runs'>, axis: Axis, tz: string): Tail[] {
  const end = turn.end_ms
  if (end === null) return []
  return turn.agent_runs
    .filter((r) => r.end_ms > end)
    .sort((p, q) => p.end_ms - q.end_ms)
    .map((r) => {
      const box = barBox(axis, end, r.end_ms, MIN_BAR_PX)
      return { ...box, agent: r.agent_name, endMs: r.end_ms, title: `${r.agent_name ?? 'agent'}: ran to ${fmt.clock(r.end_ms, tz)}` } // FALLBACK-OK: SES-42 (an agent run with no name is called "agent")
    })
}

/** The axis head's caption (SES-40): names the axis. */
export function axisCaption(realTime: boolean, foldMinMs: number): string {
  if (realTime) return 'Real time'
  return `Waits of ${foldMinMs % 60_000 === 0 ? `${foldMinMs / 60_000} min` : `${foldMinMs / 1000} s`} or more folded`
}

/** State of a gap row's bar (SES-42: hatched in its state colour). */
export const gapBarState = (g: GapRow) => gapState(g)

/** The turn's span for its bar (SES-21): both ends needed; a turn with no start or end has no bar (SES-85). */
export function turnSpan(t: Pick<TurnRow, 'start_ms' | 'end_ms'>): { start: number; end: number } | null {
  return t.start_ms !== null && t.end_ms !== null && t.end_ms >= t.start_ms ? { start: t.start_ms, end: t.end_ms } : null
}
