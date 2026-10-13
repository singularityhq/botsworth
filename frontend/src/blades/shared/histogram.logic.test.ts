import { describe, expect, it } from 'vitest'
import type { Histogram, Stretch } from '../../api/types'
import { parseUrl, serializeUrl } from '../../state/url'
import {
  axisLabels, barHeight, rangeCost, binIndexAt, binTip, familyTotals, binColumns, brushRange, dominantState, familyColumns, histogramLabel, labelStepMs, laneSegments, makeAxis,
  stackHeights, tailLabelX, waitFolds,
} from './histogram.logic'

const MIN = 60_000
const T0 = Date.UTC(2026, 9, 1, 4, 0, 0) // 09:30 in Asia/Kolkata
const TZ = 'Asia/Kolkata'

function stretch(startMin: number, durMin: number, o: Partial<Stretch> = {}): Stretch {
  return {
    start_ms: T0 + startMin * MIN, end_ms: T0 + (startMin + durMin) * MIN, dur_ms: durMin * MIN, state: 'none', label: 'you',
    turn: null, next_turn: null, running: [], ...o,
  }
}

describe('waitFolds (SES-29)', () => {
  it('folds waits of 10 min or more and nothing shorter', () => {
    const f = waitFolds([stretch(0, 9), stretch(20, 10), stretch(60, 45)], T0, T0 + 200 * MIN, 10 * MIN)
    expect(f).toEqual([
      { a: T0 + 20 * MIN, b: T0 + 30 * MIN },
      { a: T0 + 60 * MIN, b: T0 + 105 * MIN },
    ])
  })

  it('never folds an agents-only stretch: something ran', () => {
    expect(waitFolds([stretch(0, 60, { state: 'agents', label: 'agents' })], T0, T0 + 100 * MIN, 10 * MIN)).toEqual([])
  })

  it('clips a wait to the axis', () => {
    expect(waitFolds([stretch(-30, 60)], T0, T0 + 100 * MIN, 10 * MIN)).toEqual([{ a: T0, b: T0 + 30 * MIN }])
  })
})

describe('axis with folds (SES-29: a 24 px column per fold)', () => {
  // 100 min over 1000 px, one 40 min fold at 30..70 min: 60 min linear over 976 px.
  const axis = makeAxis(T0, T0 + 100 * MIN, 1000, [{ a: T0 + 30 * MIN, b: T0 + 70 * MIN }], 24)
  const k = 976 / (60 * MIN)

  it('maps time before the fold linearly', () => {
    expect(axis.x(T0 + 10 * MIN)).toBeCloseTo(10 * MIN * k, 6)
  })

  it('gives the fold exactly 24 px, and a point inside it a share of them', () => {
    expect(axis.x(T0 + 30 * MIN)).toBeCloseTo(30 * MIN * k, 6)
    expect(axis.x(T0 + 70 * MIN) - axis.x(T0 + 30 * MIN)).toBeCloseTo(24, 6)
    expect(axis.x(T0 + 50 * MIN) - axis.x(T0 + 30 * MIN)).toBeCloseTo(12, 6)
  })

  it('continues linearly after the fold and ends at the width', () => {
    expect(axis.x(T0 + 100 * MIN)).toBeCloseTo(1000, 6)
    expect(axis.x(T0 + 80 * MIN)).toBeCloseTo(30 * MIN * k + 24 + 10 * MIN * k, 6)
  })

  it('inverts x on both sides of and inside a fold', () => {
    for (const m of [5, 29, 45, 69, 71, 99]) expect(axis.inv(axis.x(T0 + m * MIN))).toBeCloseTo(T0 + m * MIN, -1)
  })

  it('is plain real time with no folds', () => {
    const real = makeAxis(T0, T0 + 100 * MIN, 1000, [], 24)
    expect(real.x(T0 + 50 * MIN)).toBeCloseTo(500, 6)
  })

  it('caps the share of the axis the folds take at half, so many waits keep room for bars', () => {
    const folds = Array.from({ length: 60 }, (_, i) => ({ a: T0 + i * 20 * MIN, b: T0 + i * 20 * MIN + 10 * MIN }))
    const a = makeAxis(T0, T0 + 1200 * MIN, 1000, folds, 24)
    expect(a.foldPx * 60).toBeCloseTo(500, 6)
    expect(a.x(T0 + 1200 * MIN)).toBeCloseTo(1000, 6)
  })
})

describe('binColumns: bin x mapping across folds (SES-29)', () => {
  const folds = [{ a: T0 + 30 * MIN, b: T0 + 70 * MIN }]
  const axis = makeAxis(T0, T0 + 100 * MIN, 1000, folds, 24)
  // 100 one-minute bins; cost 1 in minute 10, cost 2 in minute 80.
  const bins = Array.from({ length: 100 }, (_, i) => ({ cost_usd: i === 10 ? 1 : i === 80 ? 2 : 0, main_cost_usd: 0, tok_total: 0 }))
  const h = { start_ms: T0, bin_ms: MIN, bins }

  it('puts each bin at its drawn position, past the fold included (a 1 min bin is 16 px here, so it covers about 6 columns)', () => {
    const cols = binColumns(h, axis, 'cost_usd')
    const within = (m: number) => {
      const c0 = Math.floor(axis.x(T0 + m * MIN) / 3)
      const c1 = Math.floor(axis.x(T0 + (m + 1) * MIN) / 3)
      return cols.slice(c0, c1 + 1).reduce((a, v) => a + v, 0)
    }
    expect(within(10)).toBeCloseTo(1, 9)
    expect(within(80)).toBeCloseTo(2, 9) // minute 80 is drawn 24 px + 30 min of scale later than a fold-free axis would put it
    expect(cols.reduce((a, v) => a + v, 0)).toBeCloseTo(3, 9)
    expect(cols[Math.floor(axis.x(T0 + 50 * MIN) / 3)]).toBe(0) // inside the fold nothing was spent
  })

  it('keeps the sum when one bin is wider than a column (it covers a fold)', () => {
    const wide = { start_ms: T0, bin_ms: 100 * MIN, bins: [{ cost_usd: 5, main_cost_usd: 0, tok_total: 0 }] }
    const cols = binColumns(wide, axis, 'cost_usd')
    expect(cols.reduce((a, v) => a + v, 0)).toBeCloseTo(5, 9)
    expect(cols.filter((v) => v > 0).length).toBeGreaterThan(300)
  })

  it('adds bins narrower than a column into the same column', () => {
    const real = makeAxis(T0, T0 + 100 * MIN, 30, [], 24) // 10 columns of 3 px: 10 min each
    const b = { start_ms: T0, bin_ms: MIN, bins: Array.from({ length: 100 }, () => ({ cost_usd: 1, main_cost_usd: 0, tok_total: 0 })) }
    expect(Array.from(binColumns(b, real, 'cost_usd'))).toEqual(new Array(10).fill(10))
  })
})

describe('barHeight (SES-29: linear, 2 px minimum)', () => {
  it('is linear in the value', () => {
    expect(barHeight(50, 100, 60)).toBe(30)
    expect(barHeight(25, 100, 60)).toBe(15)
  })

  it('is 2 px for a non-empty bin however small, and 0 for an empty one', () => {
    expect(barHeight(0.01, 100, 60)).toBe(2)
    expect(barHeight(0, 100, 60)).toBe(0)
  })
})

describe('labelStepMs and axisLabels (SES-29)', () => {
  it('takes the first step of 5, 15, 30, 60, 120, 240, 480 min giving fewer than 9 labels', () => {
    expect(labelStepMs(40 * MIN)).toBe(5 * MIN) // 8 labels
    expect(labelStepMs(45 * MIN)).toBe(15 * MIN) // 9 labels at 5 min is not fewer than 9
    expect(labelStepMs(8 * 60 * MIN)).toBe(60 * MIN) // 8 labels at 60 min
    expect(labelStepMs(9 * 60 * MIN)).toBe(120 * MIN) // 9 labels at 60 min is not fewer than 9
    expect(labelStepMs(100 * 60 * MIN)).toBe(480 * MIN) // no step gives fewer than 9: the last
  })

  it('labels at clock multiples in the store zone, at least 44 px apart, left to right', () => {
    const axis = makeAxis(T0, T0 + 120 * MIN, 1000, [], 24) // 09:30 to 11:30 in Kolkata
    const labels = axisLabels(axis, TZ)
    expect(labels.map((l) => l.text)).toEqual(['09:30', '09:45', '10:00', '10:15', '10:30', '10:45', '11:00', '11:15']) // 15 min step: 8 labels over 2 h
    for (let i = 1; i < labels.length; i++) expect(labels[i].x - labels[i - 1].x).toBeGreaterThanOrEqual(44)
  })

  it('skips a label that would sit closer than 44 px to the last one', () => {
    const axis = makeAxis(T0, T0 + 40 * MIN, 120, [], 24) // 40 min over 120 px: a 5 min step is 15 px
    const labels = axisLabels(axis, TZ)
    for (let i = 1; i < labels.length; i++) expect(labels[i].x - labels[i - 1].x).toBeGreaterThanOrEqual(44)
    expect(labels.length).toBeLessThan(8)
  })

  it('puts no label inside a fold', () => {
    // the fold covers 10:00..11:00 Kolkata (30..90 min after T0)
    const axis = makeAxis(T0, T0 + 180 * MIN, 1000, [{ a: T0 + 30 * MIN, b: T0 + 90 * MIN }], 24)
    const labels = axisLabels(axis, TZ, 30 * MIN)
    expect(labels.every((l) => !(l.t > T0 + 30 * MIN && l.t < T0 + 90 * MIN))).toBe(true)
    expect(labels.map((l) => l.text)).toContain('09:30')
    expect(labels.map((l) => l.text)).toContain('12:00')
    expect(labels.map((l) => l.text)).not.toContain('10:30')
  })
})

describe('brushRange (SES-30, TRN-24)', () => {
  const axis = makeAxis(T0, T0 + 100 * MIN, 1000, [], 24)

  it('does nothing for a drag under 4 px', () => {
    expect(brushRange(100, 103.9, axis)).toBeNull()
    expect(brushRange(300, 300, axis)).toBeNull()
  })

  it('sets a range for a drag of 4 px or more, whichever way it goes', () => {
    expect(brushRange(100, 104, axis)).toEqual({ start_ms: T0 + 10 * MIN, end_ms: Math.round(axis.inv(104)) })
    expect(brushRange(500, 400, axis)).toEqual({ start_ms: T0 + 40 * MIN, end_ms: T0 + 50 * MIN })
  })

  it('goes into the `range` URL param as <start>-<end> and reads back', () => {
    const r = brushRange(400, 500, axis)!
    const q = serializeUrl({ ...parseUrl(''), s: 'x', range: r })
    expect(q).toContain(`range=${r.start_ms}-${r.end_ms}`)
    expect(parseUrl(q).range).toEqual(r)
  })
})

function hist(o: Partial<Histogram> = {}): Histogram {
  return {
    freshness: {} as Histogram['freshness'], start_ms: T0, bin_ms: 10 * MIN, tail_from_ms: null,
    bins: [
      { cost_usd: 1, main_cost_usd: 0, tok_total: 100, state_ms: { model_ms: 5, tool_ms: 3, agents_ms: 0, none_ms: 0 } },
      { cost_usd: 2, main_cost_usd: 0, tok_total: 200, state_ms: { model_ms: 1, tool_ms: 1, agents_ms: 0, none_ms: 8 } },
    ],
    marks: { turn_starts: [], skill_loads: [] }, stretches: [], ...o,
  }
}

describe('state lane (SES-29)', () => {
  it('colours a bin by the state with the most time; ties go model, tool, agents, none', () => {
    expect(dominantState({ model_ms: 5, tool_ms: 3, agents_ms: 0, none_ms: 0 })).toBe('model')
    expect(dominantState({ model_ms: 1, tool_ms: 1, agents_ms: 0, none_ms: 8 })).toBe('none')
    expect(dominantState({ model_ms: 2, tool_ms: 2, agents_ms: 2, none_ms: 0 })).toBe('model')
    expect(dominantState({ model_ms: 0, tool_ms: 0, agents_ms: 0, none_ms: 0 })).toBeNull()
  })

  it('draws a stretch over its bins in the stretch label state, so none becomes limit', () => {
    const h = hist({ stretches: [stretch(10, 10, { label: 'limit' })] })
    const axis = makeAxis(T0, T0 + 20 * MIN, 200, [], 24)
    const segs = laneSegments(h, axis)
    expect(segs[0]).toEqual({ state: 'model', x0: 0, x1: 100 })
    expect(segs[1]).toEqual({ state: 'none', x0: 100, x1: 200 })
    expect(segs[2]).toEqual({ state: 'limit', x0: 100, x1: 200 })
  })

  it('merges adjacent bins of one state', () => {
    const bin = { cost_usd: 0, main_cost_usd: 0, tok_total: 0, state_ms: { model_ms: 1, tool_ms: 0, agents_ms: 0, none_ms: 0 } }
    const h = hist({ bins: [bin, bin, bin] })
    const axis = makeAxis(T0, T0 + 30 * MIN, 300, [], 24)
    expect(laneSegments(h, axis)).toEqual([{ state: 'model', x0: 0, x1: 300 }])
  })
})

describe('by-model stack (SES-22, TRN-22)', () => {
  it('stacks bottom to top in the table order and keeps the total linear', () => {
    const segs = stackHeights(
      [{ family: 'haiku', value: 1 }, { family: 'opus', value: 3 }],
      4, 40,
    )
    expect(segs.map((s) => s.family)).toEqual(['opus', 'haiku'])
    expect(segs[0].h).toBeCloseTo(30, 9)
    expect(segs[1].h).toBeCloseTo(10, 9)
  })

  it('groups model ids by family into columns and picks cost or tokens', () => {
    const h = hist({
      bins: [{ cost_usd: 3, main_cost_usd: 0, tok_total: 30, state_ms: { model_ms: 1, tool_ms: 0, agents_ms: 0, none_ms: 0 }, by_model: { 'claude-opus-5-5': [2, 20], 'claude-sonnet-5-5': [1, 10] } }],
    })
    const axis = makeAxis(T0, T0 + 10 * MIN, 3, [], 24)
    const cost = familyColumns(h, axis, 'cost')
    expect(cost.get('opus')![0]).toBe(2)
    expect(cost.get('sonnet')![0]).toBe(1)
    expect(familyColumns(h, axis, 'tokens').get('opus')![0]).toBe(20)
  })
})

describe('histogramLabel (aria-label states range and totals)', () => {
  it('names the clock range, the measure and its total', () => {
    const h = hist()
    const axis = makeAxis(T0, T0 + 20 * MIN, 200, [], 24)
    expect(histogramLabel(h, 'cost', TZ, axis)).toBe('Over time, 09:30–09:50: $3.00 by cost, 0 turn starts, 0 skill loads')
    expect(histogramLabel(h, 'tokens', TZ, axis)).toContain('300 tokens by tokens')
  })
})

describe('hover (TRN-22)', () => {
  const h = hist({
    bins: [{ cost_usd: 3, main_cost_usd: 0, tok_total: 30, state_ms: { model_ms: 1, tool_ms: 0, agents_ms: 0, none_ms: 0 }, by_model: { 'claude-opus-5-5': [2, 20], 'claude-sonnet-5-5': [1, 10] } }, { cost_usd: 0, main_cost_usd: 0, tok_total: 0, state_ms: { model_ms: 0, tool_ms: 0, agents_ms: 0, none_ms: 0 } }],
  })
  const axis = makeAxis(T0, T0 + 20 * MIN, 200, [], 24)

  it('finds the bin under the pointer, and none outside the bins', () => {
    expect(binIndexAt(h, axis, 5)).toBe(0)
    expect(binIndexAt(h, axis, 150)).toBe(1)
  })

  it('shows the bin clock range, its total, and the total per family in stack order', () => {
    const tip = binTip(h, 0, 'cost', TZ)
    expect(tip.range).toBe('09:30–09:40')
    expect(tip.total).toBe('$3.00')
    expect(tip.families).toEqual([{ family: 'opus', text: '$2.00' }, { family: 'sonnet', text: '$1.00' }])
    expect(binTip(h, 0, 'tokens', TZ).families[0].text).toBe('20 tokens')
  })
})

describe('familyTotals (TRN-22 legend)', () => {
  it('lists each family present with its total for the measure, in stack order', () => {
    const bin = (o: Record<string, [number, number]>) => ({ cost_usd: 0, main_cost_usd: 0, tok_total: 0, state_ms: { model_ms: 0, tool_ms: 0, agents_ms: 0, none_ms: 0 }, by_model: o })
    const h = { bins: [bin({ 'claude-sonnet-5-5': [1, 10] }), bin({ 'claude-opus-5-5': [4, 40], 'claude-sonnet-5-5': [2, 20] })] }
    expect(familyTotals(h, 'cost')).toEqual([{ family: 'opus', value: 4 }, { family: 'sonnet', value: 3 }])
    expect(familyTotals(h, 'tokens')).toEqual([{ family: 'opus', value: 40 }, { family: 'sonnet', value: 30 }])
  })
})

describe('rangeCost (SES-13, D47: the clock rule in a brushed range)', () => {
  const bin = (cost: number, main: number) => ({ cost_usd: cost, main_cost_usd: main, tok_total: 0, state_ms: { model_ms: 0, tool_ms: 0, agents_ms: 0, none_ms: 0 } })
  const h = { start_ms: T0, bin_ms: 10 * MIN, bins: [bin(1, 1), bin(4, 1), bin(2, 0.5), bin(8, 8)] }

  it('sums the bins that start inside the range: main from main_cost_usd, agents is the rest', () => {
    const r = rangeCost(h, { start_ms: T0 + 10 * MIN, end_ms: T0 + 30 * MIN })
    expect(r.cost_usd).toBe(6)
    expect(r.main).toBe(1.5)
    expect(r.agents).toBeCloseTo(4.5, 9)
  })

  it('a bin counts when it starts at the range start, not when it starts at the range end', () => {
    expect(rangeCost(h, { start_ms: T0 + 10 * MIN, end_ms: T0 + 20 * MIN }).cost_usd).toBe(4)
  })

  it('a range with no bin start has no cost, and agents is never negative', () => {
    expect(rangeCost(h, { start_ms: T0 + 1, end_ms: T0 + 2 })).toEqual({ cost_usd: 0, main: 0, agents: 0 })
    expect(rangeCost({ ...h, bins: [bin(1, 1.0000001)] }, { start_ms: T0, end_ms: T0 + MIN }).agents).toBe(0)
  })
})

describe('tailLabelX (FX2 item 7: "after the turn" shows whole)', () => {
  it('sits 6 px into the tail when the text fits', () => {
    expect(tailLabelX(100, 101, 800)).toBe(106)
  })
  it('moves left so the whole text ends inside the canvas when the tail starts near the right edge', () => {
    expect(tailLabelX(780, 101, 800)).toBe(800 - 101 - 4)
  })
  it('never goes left of the canvas', () => {
    expect(tailLabelX(0, 900, 800)).toBe(0)
  })
})
