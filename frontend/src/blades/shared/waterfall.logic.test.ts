import { describe, expect, it } from 'vitest'
import { gap, turn } from '../session/testRows'
import { makeAxis } from './histogram.logic'
import {
  agentTails, axisCaption, barBox, foldMarks, gapFolds, headerLabels, markTitle, sessionAxis, SESSION_FOLD_MIN_MS, SESSION_FOLD_PX,
} from './waterfall.logic'

const MIN = 60_000
const A = Date.UTC(2026, 9, 1, 4, 0, 0)
const TZ = 'Asia/Kolkata'
const g = (startMin: number, durMin: number, o = {}) => gap(A + startMin * MIN, durMin * MIN, o)

describe('session fold rule (SES-42: waits of 2 min or more become 12 px breaks)', () => {
  it('folds a wait of exactly 2 min and not one of 119 s', () => {
    const gaps = [g(10, 2), gap(A + 30 * MIN, 119_000)]
    expect(gapFolds(gaps, A, A + 100 * MIN, SESSION_FOLD_MIN_MS, false)).toEqual([{ a: A + 10 * MIN, b: A + 12 * MIN }])
  })

  it('never folds an agents-only stretch', () => {
    expect(gapFolds([g(10, 30, { state: 'agents', label: 'agents' })], A, A + 100 * MIN, SESSION_FOLD_MIN_MS, false)).toEqual([])
  })

  it('draws each fold as 12 px', () => {
    const axis = sessionAxis(A, A + 100 * MIN, 1000, [g(10, 20), g(50, 20)], false)
    expect(axis.foldPx).toBe(SESSION_FOLD_PX)
    expect(axis.x(A + 30 * MIN) - axis.x(A + 10 * MIN)).toBeCloseTo(12, 9)
  })

  it('is linear in Real time: no folds, the gap keeps its length', () => {
    const axis = sessionAxis(A, A + 100 * MIN, 1000, [g(10, 20)], true)
    expect(axis.folds).toEqual([])
    expect(axis.x(A + 30 * MIN) - axis.x(A + 10 * MIN)).toBeCloseTo(200, 9)
  })
})

describe('fold marks (SES-42: marks less than about 10 px apart merge into "≈×N")', () => {
  it('keeps marks 10 px apart or more separate, and merges closer ones with their total', () => {
    // The second wait starts 1 min after the first ends: about 9 px on this axis, under 10.
    const gaps = [g(10, 5), g(16, 5), g(60, 5)]
    const axis = sessionAxis(A, A + 120 * MIN, 1000, gaps, false)
    const marks = foldMarks(axis)
    expect(marks).toHaveLength(2)
    expect(marks[0]).toMatchObject({ n: 2, ms: 10 * MIN, text: '≈×2', title: '2 waits of 10m folded' })
    expect(marks[1]).toMatchObject({ n: 1, ms: 5 * MIN, text: '≈', title: 'Wait of 5m folded' })
  })

  it('does not merge two marks when the gap between them is 10 px or more', () => {
    const axis = makeAxis(A, A + 100 * MIN, 1000, [{ a: A + 10 * MIN, b: A + 20 * MIN }, { a: A + 40 * MIN, b: A + 50 * MIN }], 12)
    expect(foldMarks(axis).map((m) => m.n)).toEqual([1, 1])
  })

  it('tooltip text: one wait, many waits', () => {
    expect(markTitle(1, 62 * MIN)).toBe('Wait of 1h02m folded')
    expect(markTitle(4, 62 * MIN)).toBe('4 waits of 1h02m folded')
  })

  it('header labels follow the SES-29 steps: none inside a fold, at least 44 px apart', () => {
    const axis = sessionAxis(A, A + 120 * MIN, 1000, [g(30, 30)], false)
    const labels = headerLabels(axis, TZ)
    expect(labels.length).toBeGreaterThan(2)
    for (const l of labels) expect(l.t > A + 30 * MIN && l.t < A + 60 * MIN).toBe(false)
    for (let i = 1; i < labels.length; i++) expect(labels[i].x - labels[i - 1].x).toBeGreaterThanOrEqual(44)
  })
})

describe('bars (SES-40: at least 2 px)', () => {
  const axis = makeAxis(A, A + 100 * MIN, 1000, [], 12)

  it('is at least 2 px wide, however short', () => {
    expect(barBox(axis, A + 10 * MIN, A + 10 * MIN + 1).width).toBe(2)
  })

  it('spans start to end on the axis', () => {
    expect(barBox(axis, A + 10 * MIN, A + 20 * MIN)).toEqual({ left: 100, width: 100 })
  })
})

describe('agent tails (SES-42)', () => {
  const axis = makeAxis(A, A + 100 * MIN, 1000, [], 12)
  const run = (name: string | null, endMin: number) => ({ engagement: `e-${name}-${endMin}`, agent_name: name, start_ms: A, end_ms: A + endMin * MIN })

  it('draws a tail only for a run that ends after the turn, from the turn end to the run end', () => {
    const t = turn(1, { end_ms: A + 20 * MIN, agent_runs: [run('a', 10), run('b', 30)] })
    const tails = agentTails(t, axis, TZ)
    expect(tails).toHaveLength(1)
    expect(tails[0]).toMatchObject({ left: 200, width: 100, agent: 'b', title: 'b: ran to 10:00' })
  })

  it('draws the longest last', () => {
    const t = turn(1, { end_ms: A + 20 * MIN, agent_runs: [run('long', 60), run('short', 25), run('mid', 40)] })
    expect(agentTails(t, axis, TZ).map((x) => x.agent)).toEqual(['short', 'mid', 'long'])
  })

  it('has none for a turn with no end', () => {
    expect(agentTails(turn(null), axis, TZ)).toEqual([])
  })
})

describe('axis caption (SES-40)', () => {
  it('names the axis', () => {
    expect(axisCaption(false, SESSION_FOLD_MIN_MS)).toBe('Waits of 2 min or more folded')
    expect(axisCaption(false, 60_000)).toBe('Waits of 1 min or more folded')
    expect(axisCaption(true, SESSION_FOLD_MIN_MS)).toBe('Real time')
  })
})
