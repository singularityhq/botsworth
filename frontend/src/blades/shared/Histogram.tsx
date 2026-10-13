import { useEffect, useMemo, useRef, useState } from 'react'
import type { Histogram as HistogramWire } from '../../api/types'
import type { Bars, MsRange } from '../../state/url'
import { Money, Tokens } from '../../ui/figures'
import { SHAPES } from '../../ui/model-mark.logic'
import {
  axisLabels, barHeight, barsWord, binColumns, binField, tailLabelX, binIndexAt, binTip, brushRange, familyColumns, familyTotals, histogramLabel, histogramSpan, HIST_FOLD_MIN_MS,
  HIST_FOLD_PX, laneSegments, makeAxis, MODEL_STACK, stackHeights, waitFolds, BIN_PITCH, BIN_WIDTH, type Axis, type BinTip,
} from './histogram.logic'
import { STATES } from './states'
import { useElementWidth } from './useElementWidth'

const HEIGHT = 104
const TOP = 12 // room for the skill pins above the bars
const LANE = 8
const LABEL_ROOM = 20

interface HistogramProps {
  data: HistogramWire
  bars: Bars
  tz: string
  /** The brushed range, drawn as a selection (SES-30, TRN-24). */
  range: MsRange | null
  /** A 2 px struct line at this instant: the open turn's start (SES-29). */
  cursorMs?: number | null
  /** No folds: real time (the turn histogram inside a zoom, TRN-21). */
  realTime?: boolean
  /** Waits of this length or more fold (SES-29: 10 min). */
  foldMinMs?: number
  /** Bars stacked by model family in the SES-22 ramp (the turn histogram, TRN-22). */
  stacked?: boolean
  /** Right end of the caption. */
  hint: string
  onBrush: (range: MsRange) => void
}

/** The colour a CSS custom property holds. Charts take every colour from the theme tokens, never a literal. */
function token(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim()
}

/**
 * SES-29 / TRN-21: the Over time histogram. A 104 px canvas inset by its caller: bars in 3 px bins on a time axis whose long waits fold,
 * the state lane, turn ticks, skill pins, the cursor line, clock labels, and a brush. Blade-agnostic: the session blade and the turn
 * blade differ only in the data, `stacked`, `foldMinMs` and `hint`.
 */
export function Histogram({ data, bars, tz, range, cursorMs = null, realTime = false, foldMinMs = HIST_FOLD_MIN_MS, stacked = false, hint, onBrush }: HistogramProps) {
  const [box, width] = useElementWidth<HTMLDivElement>()
  const canvas = useRef<HTMLCanvasElement>(null)
  const [drag, setDrag] = useState<{ x0: number; x1: number } | null>(null)
  const [tip, setTip] = useState<{ x: number; tip: BinTip } | null>(null)

  const axis: Axis | null = useMemo(() => {
    if (width <= 0) return null
    const { a, b } = histogramSpan(data)
    const folds = realTime ? [] : waitFolds(data.stretches, a, b, foldMinMs)
    return makeAxis(a, b, width, folds, HIST_FOLD_PX)
  }, [data, width, realTime, foldMinMs])

  useEffect(() => {
    const cv = canvas.current
    if (!cv || !axis) return
    draw(cv, axis, data, bars, tz, cursorMs, stacked)
  }, [axis, data, bars, tz, cursorMs, stacked])

  const sel = drag ?? (range && axis ? { x0: axis.x(range.start_ms), x1: axis.x(range.end_ms) } : null)

  const localX = (e: React.PointerEvent) => e.clientX - box.current!.getBoundingClientRect().left
  const onDown = (e: React.PointerEvent) => {
    if (e.button !== 0) return
    box.current!.setPointerCapture(e.pointerId)
    const x = localX(e)
    setDrag({ x0: x, x1: x })
    setTip(null)
  }
  const onMove = (e: React.PointerEvent) => {
    const x = localX(e)
    if (drag) {
      setDrag({ x0: drag.x0, x1: x })
      return
    }
    if (!axis) return
    const i = binIndexAt(data, axis, x)
    setTip(i === null ? null : { x, tip: binTip(data, i, bars, tz) })
  }
  const onUp = (e: React.PointerEvent) => {
    if (!drag || !axis) return
    const r = brushRange(drag.x0, localX(e), axis)
    setDrag(null)
    if (r) onBrush(r)
  }

  return (
    <div>
      <div data-histogram-caption className="mx-6 mt-3 flex items-baseline gap-4 text-ink3">
        <b className="font-semibold text-ink1">Over time</b>
        <span>bars {barsWord(bars)}</span>
        <span>lane: what ran</span>
        <span>≈ wait over {foldMinMs / 60_000} min, folded</span>
        <span className="flex-1" />
        <span>{hint}</span>
      </div>
      {stacked ? <FamilyLegend data={data} bars={bars} /> : null}
      <div
        ref={box}
        role="img"
        data-histogram
        aria-label={axis ? histogramLabel(data, bars, tz, axis) : 'Over time'}
        className="relative mx-6 mb-2 mt-1 cursor-crosshair select-none touch-none"
        style={{ height: HEIGHT }}
        onPointerDown={onDown}
        onPointerMove={onMove}
        onPointerUp={onUp}
        onPointerLeave={() => setTip(null)}
      >
        <canvas ref={canvas} className="absolute inset-0 h-full w-full" />
        {sel ? (
          <div
            aria-hidden="true"
            className="pointer-events-none absolute border-x border-struct bg-struct-soft"
            style={{ left: Math.min(sel.x0, sel.x1), width: Math.max(2, Math.abs(sel.x1 - sel.x0)), top: 10, height: HEIGHT - 30 }}
          />
        ) : null}
        {tip && !drag ? (
          <div
            role="tooltip"
            className="pointer-events-none absolute top-0 z-10 -translate-y-full whitespace-nowrap rounded-control bg-l4 px-2.5 py-2 text-body text-ink1 shadow-pop"
            style={{ left: Math.max(0, Math.min(width - 160, tip.x - 80)) }}
          >
            <div className="fig">
              {tip.tip.range} · {tip.tip.total}
            </div>
            {tip.tip.families.map((f) => (
              <div key={f.family} className="flex items-center gap-2">
                <FamilyMark family={f.family} />
                <span className="fig text-ink2">{f.text}</span>
              </div>
            ))}
          </div>
        ) : null}
      </div>
    </div>
  )
}

/** SES-22 / SYS-41: the family's shape in its chart colour. */
function FamilyMark({ family }: { family: (typeof MODEL_STACK)[number] }) {
  return (
    <svg viewBox="0 0 10 10" width={10} height={10} aria-hidden="true" style={{ fill: `var(--chart-model-${family})` }}>
      <path d={SHAPES[family].d} />
    </svg>
  )
}

/** TRN-22: a legend under the caption of a stacked chart: each family present, its short name and its total for the measure. */
function FamilyLegend({ data, bars }: { data: HistogramWire; bars: Bars }) {
  const totals = familyTotals(data, bars)
  if (totals.length === 0) return null
  return (
    <div className="mx-6 mt-1 flex items-center gap-4 text-ink2">
      {totals.map((t) => (
        <span key={t.family} className="inline-flex items-center gap-1.5">
          <FamilyMark family={t.family} />
          <span>{t.family}</span>
          {bars === 'tokens' ? <Tokens value={t.value} /> : <Money value={t.value} weight={400} />}
        </span>
      ))}
    </div>
  )
}

/** SES-20's hatch (135°, 2 px lines every 5 px) as a canvas pattern in `colour`. */
function hatch(g: CanvasRenderingContext2D, colour: string): CanvasPattern {
  const tile = document.createElement('canvas')
  tile.width = 5
  tile.height = 5
  const t = tile.getContext('2d')
  if (!t) throw new Error('Histogram: no 2d canvas context for the hatch tile')
  t.strokeStyle = colour
  t.lineWidth = 2
  for (const o of [-5, 0, 5]) {
    t.beginPath()
    t.moveTo(o, 5 + 0)
    t.lineTo(o + 5, 0)
    t.stroke()
  }
  const p = g.createPattern(tile, 'repeat')
  if (!p) throw new Error('Histogram: could not make the hatch pattern')
  return p
}

function draw(cv: HTMLCanvasElement, axis: Axis, h: HistogramWire, bars: Bars, tz: string, cursorMs: number | null, stacked: boolean): void {
  const W = axis.width
  const dpr = window.devicePixelRatio || 1
  cv.width = Math.round(W * dpr)
  cv.height = Math.round(HEIGHT * dpr)
  const g = cv.getContext('2d')
  if (!g) throw new Error('Histogram: no 2d canvas context')
  g.scale(dpr, dpr)
  const bh = HEIGHT - TOP - LANE - LABEL_ROOM
  const laneY = TOP + bh + 4

  // Where the turn's own activity ended and agents kept running (D41 N1): a darker well after `tail_from_ms`, labelled.
  if (h.tail_from_ms !== null) {
    const x0 = axis.x(h.tail_from_ms)
    g.fillStyle = token('--color-l2')
    g.fillRect(0, TOP - 2, x0, bh + LANE + 8)
    g.fillStyle = token('--color-l1')
    g.fillRect(x0, TOP - 2, W - x0, bh + LANE + 8)
    g.font = "12px 'IBM Plex Mono', monospace"
    g.fillStyle = token('--color-ink3')
    const tail = 'after the turn'
    g.fillText(tail, tailLabelX(x0, g.measureText(tail).width, W), TOP + 10)
  }

  // Bars: linear, 2 px wide every 3 px.
  const cols = binColumns(h, axis, binField(bars))
  const max = Math.max(1e-12, ...cols)
  if (stacked) {
    const fam = familyColumns(h, axis, bars)
    for (let c = 0; c < cols.length; c++) {
      if (!(cols[c] > 0)) continue
      const segs = stackHeights([...fam].map(([family, v]) => ({ family, value: v[c] })).filter((s) => s.value > 0), max, bh)
      let y = TOP + bh
      for (const s of segs) {
        g.fillStyle = token(`--chart-model-${s.family}`)
        g.fillRect(c * BIN_PITCH, y - s.h, BIN_WIDTH, Math.max(1, s.h - 1)) // 1 px gap of the surface between segments (SES-22)
        y -= s.h
      }
    }
  } else {
    g.fillStyle = token(bars === 'tokens' ? '--color-ink2' : '--color-money')
    for (let c = 0; c < cols.length; c++) {
      const hgt = barHeight(cols[c], max, bh)
      if (hgt > 0) g.fillRect(c * BIN_PITCH, TOP + bh - hgt, BIN_WIDTH, hgt)
    }
  }

  // State lane: every state in its SES-20 colour. Wait states are hatched at full strength: at the 55 % opacity SES-29 names, --st-none
  // reads 1.8:1 on l1 and misses the 3:1 floor for graphics, and SES-20 already says nothing running is hatched.
  for (const s of laneSegments(h, axis)) {
    const colour = token(`--st-${s.state}`)
    g.fillStyle = STATES[s.state].hatched ? hatch(g, colour) : colour
    g.fillRect(s.x0, laneY, Math.max(1, s.x1 - s.x0), LANE)
  }

  // Folds: a l3 column with a centred ≈.
  g.font = "12px 'IBM Plex Mono', monospace"
  for (const f of axis.folds) {
    const x0 = axis.x(f.a)
    g.fillStyle = token('--color-l3')
    g.fillRect(x0 + 2, TOP, Math.max(2, axis.foldPx - 4), bh)
    g.fillStyle = token('--color-ink3')
    g.fillText('≈', x0 + axis.foldPx / 2 - 4, TOP + bh / 2 + 4)
  }

  // Marks: a 4 px ink3 tick per numbered turn start, an ink1 triangle per skill load.
  g.fillStyle = token('--color-ink3')
  for (const t of h.marks.turn_starts ?? []) { // FALLBACK-OK: interfaces.md API-31 (turn_starts is session only; a turn histogram draws no turn ticks)
    if (t.start_ms > axis.a && t.start_ms < axis.b) g.fillRect(axis.x(t.start_ms), TOP - 4, 1, 4)
  }
  g.fillStyle = token('--color-ink1')
  for (const s of h.marks.skill_loads) {
    if (s.start_ms <= axis.a || s.start_ms >= axis.b) continue
    const px = axis.x(s.start_ms)
    g.beginPath()
    g.moveTo(px - 4, 0)
    g.lineTo(px + 4, 0)
    g.lineTo(px, 7)
    g.fill()
  }

  // Clock labels: 12 px mono ink3.
  g.fillStyle = token('--color-ink3')
  for (const l of axisLabels(axis, tz)) g.fillText(l.text, Math.min(W - 36, l.x), HEIGHT - 2)

  // The open turn.
  if (cursorMs !== null && cursorMs >= axis.a && cursorMs <= axis.b) {
    g.fillStyle = token('--color-struct')
    g.fillRect(axis.x(cursorMs) - 1, TOP - 2, 2, bh + LANE + 6)
  }
}
