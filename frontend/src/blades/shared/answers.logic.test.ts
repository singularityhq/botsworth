import { describe, expect, it } from 'vitest'
import { fixtures } from '../../api/__fixtures__'
import type { TimeSplit } from '../../api/types'
import {
  costCaption, DISPLAY_KEYS, gapStartMs, longestStalled, jumpToast, legendStates, planJump, runningSplit, segmentWidths, stateTotals, totalsSum, substateBreakdown,
  type Longest,
} from './answers.logic'

const S = fixtures.sessionDetail.session

describe('stateTotals (SES-15, SES-16)', () => {
  it('the eight segments (seven states and stalled) sum to the wall (fixture session)', () => {
    const t = stateTotals(S.time_split as TimeSplit)
    expect(totalsSum(t)).toBe(S.wall_ms)
    expect(t).toEqual({ model: 120000, tool: 45000, agents: 300000, you: 30000, agentmsg: 0, limit: 30000, stalled: 0, none: 0 })
  })

  it('the legend `you` sums its substates (SES-15, D54)', () => {
    const t = stateTotals({
      parts: [
        { state: 'you', substate: 'ask', ms: 100 }, { state: 'you', substate: 'permission', ms: 20 }, { state: 'you', substate: 'reply', ms: 3 },
        { state: 'none', substate: 'before_first', ms: 40 }, { state: 'none', substate: 'none', ms: 5 },
      ],
    })
    expect(t.you).toBe(123)
    expect(t.none).toBe(45)
  })

  it('lists background under tool for the legend tooltip, leaving out other substates, and lists no substate under none: stalled has its own item (SES-15, D55, D56)', () => {
    const split: TimeSplit = { parts: [
      { state: 'tool', substate: null, ms: 7 }, { state: 'tool', substate: 'background', ms: 40 },
      { state: 'none', substate: 'none', ms: 5 }, { state: 'none', substate: 'before_first', ms: 6 }, { state: 'none', substate: 'stalled', ms: 90 },
    ] }
    expect(substateBreakdown(split, 'tool')).toEqual([{ substate: 'background', ms: 40 }])
    expect(substateBreakdown(split, 'none')).toEqual([])
    expect(substateBreakdown(split, 'model')).toEqual([])
  })

  it('lists test and wait under tool and agents for the legend tooltip (SES-15, D56)', () => {
    const split: TimeSplit = { parts: [
      { state: 'tool', substate: 'test', ms: 30 }, { state: 'tool', substate: 'wait', ms: 5 },
      { state: 'agents', substate: 'test', ms: 20 }, { state: 'agents', substate: null, ms: 9 },
    ] }
    expect(substateBreakdown(split, 'tool')).toEqual([{ substate: 'test', ms: 30 }, { substate: 'wait', ms: 5 }])
    expect(substateBreakdown(split, 'agents')).toEqual([{ substate: 'test', ms: 20 }])
  })

  it('counts background as running time and stalled as its own total, outside none; the totals still sum to the wall (SES-19a, SES-26 [D56])', () => {
    const split: TimeSplit = { parts: [
      { state: 'model', substate: null, ms: 100 }, { state: 'tool', substate: null, ms: 10 }, { state: 'tool', substate: 'background', ms: 40 },
      { state: 'none', substate: 'stalled', ms: 90 }, { state: 'none', substate: 'none', ms: 60 },
    ] }
    const t = stateTotals(split)
    expect(t.tool).toBe(50)
    expect(t.stalled).toBe(90)
    expect(t.none).toBe(60)
    expect(totalsSum(t)).toBe(300)
    expect(runningSplit(t, 300)).toEqual({ running: 150, nothing: 150 }) // B = wall - A still holds the stalled time
  })

  it('lists the substates of you for the legend tooltip, in SES-15 order, leaving out empty ones', () => {
    expect(substateBreakdown({ parts: [{ state: 'you', substate: 'reply', ms: 3 }, { state: 'you', substate: 'ask', ms: 100 }, { state: 'model', substate: null, ms: 9 }] }, 'you')).toEqual([
      { substate: 'ask', ms: 100 },
      { substate: 'reply', ms: 3 },
    ])
  })

  it('splits running from nothing running against the wall', () => {
    const t = stateTotals(S.time_split as TimeSplit)
    expect(runningSplit(t, S.wall_ms)).toEqual({ running: 465000, nothing: 60000 })
  })
})

describe('legendStates (SES-26)', () => {
  const base = Object.fromEntries(DISPLAY_KEYS.map((k) => [k, 0])) as ReturnType<typeof stateTotals>

  it('keeps states of 30 s or more, largest first', () => {
    const t = { ...base, model: 120000, tool: 45000, agents: 300000, you: 30000, limit: 29999 }
    expect(legendStates(t)).toEqual(['agents', 'model', 'tool', 'you'])
  })

  it('never lists a state under 30 s', () => {
    expect(legendStates({ ...base, model: 29999, tool: 1000 })).toEqual([])
  })

  it('shows at most four states', () => {
    const t = { ...base, model: 100000, tool: 90000, agents: 80000, you: 70000, agentmsg: 60000, limit: 50000, none: 40000 }
    expect(legendStates(t)).toEqual(['model', 'tool', 'agents', 'you'])
  })

  it('before_first sits inside none: a session whose only wait is before the first message lists "nothing running" (D41 M11)', () => {
    const t = stateTotals({ parts: [{ state: 'model', substate: null, ms: 100000 }, { state: 'none', substate: 'before_first', ms: 1860000 }] })
    expect(legendStates(t)).toEqual(['none', 'model'])
  })

  it('lists stalled first when it is 30 s or more, even when other states are larger, and counts it toward the four', () => {
    expect(legendStates({ ...base, stalled: 30000, model: 900000, tool: 800000, agents: 700000, you: 600000 })).toEqual(['stalled', 'model', 'tool', 'agents'])
    expect(legendStates({ ...base, stalled: 29999, model: 900000 })).toEqual(['model'])
  })

  it('leaves stalled out of nothing running: a session with only stalled time lists "stalled" and no "nothing running"', () => {
    const t = stateTotals({ parts: [{ state: 'model', substate: null, ms: 100000 }, { state: 'none', substate: 'stalled', ms: 600000 }, { state: 'none', substate: 'none', ms: 10000 }] })
    expect(t).toMatchObject({ stalled: 600000, none: 10000 })
    expect(legendStates(t)).toEqual(['stalled', 'model'])
  })

  it('ties keep the SES-15 order', () => {
    expect(legendStates({ ...base, tool: 60000, model: 60000 })).toEqual(['model', 'tool'])
  })
})

describe('segmentWidths (SES-26: 2 px minimum per segment)', () => {
  it('shares the width in proportion to the values, less the 2 px gaps', () => {
    const w = segmentWidths([100, 300], 402)
    expect(w[0]).toBeCloseTo(100)
    expect(w[1]).toBeCloseTo(300)
  })

  it('draws a tiny segment at 2 px and keeps the total', () => {
    const w = segmentWidths([1, 1000000, 1000000], 1000)
    expect(w[0]).toBe(2)
    expect(w.reduce((a, b) => a + b, 0)).toBeCloseTo(1000 - 4)
    expect(w[1]).toBeCloseTo(w[2])
  })

  it('handles several tiny segments and none', () => {
    const w = segmentWidths([1, 2, 1000000], 200)
    expect(w.slice(0, 2)).toEqual([2, 2])
    expect(segmentWidths([], 100)).toEqual([])
  })
})

describe('costCaption (SES-26, SES-84)', () => {
  it('shows main thread, and agents only from $0.01', () => {
    expect(costCaption({ cost_usd: 12.4, main: 4, agents: 8.4 })).toMatchObject({ showSplit: true, showAgents: true })
    expect(costCaption({ cost_usd: 4.004, main: 4, agents: 0.004 })).toMatchObject({ showSplit: true, showAgents: false })
    expect(costCaption({ cost_usd: 4, main: 4, agents: 0 })).toMatchObject({ showAgents: false })
  })

  it('a session with no priced model call shows no figure and no split bar', () => {
    const c = costCaption({ cost_usd: null, main: null, agents: null })
    expect(c).toMatchObject({ figure: null, noModelCalls: true, showSplit: false, showAgents: false })
  })
})

describe('planJump (SES-27)', () => {
  const L = S.longest as Longest

  it('goes to the longest call of model and the longest gap of a wait state', () => {
    expect(planJump('model', L)).toMatchObject({ kind: 'go', durMs: 125000, row: { type: 'call' } })
    expect(planJump('limit', L)).toMatchObject({ kind: 'go', durMs: 3720000, row: { type: 'gap', id: 'gap-1791786480000' } })
  })

  it('a wait state with no single stretch of 2 min says so and goes nowhere', () => {
    const j = planJump('you', L)
    expect(j).toEqual({ kind: 'none', toast: ['No single stretch of waiting on you is 2 min or longer; the total is many short pauses.'] })
  })

  it('a stretch under 2 min is not a jump target', () => {
    const short = { ...L, none: { row: { type: 'gap' as const, id: 'gap-1' }, turn: 't', dur_ms: 119999 } }
    expect(planJump('none', short).kind).toBe('none')
    const ok = { ...L, none: { row: { type: 'gap' as const, id: 'gap-1' }, turn: 't', dur_ms: 120000 } }
    expect(planJump('none', ok).kind).toBe('go')
  })

  it('a click on stalled goes to the longest stalled stretch, found among the gap rows, and not to a longer none stretch', () => {
    const gap = (id: string, dur_ms: number, substate: string) => ({ id, turn: 't2', dur_ms, state: 'none' as const, substate })
    const stalled = longestStalled([gap('gap-1', 600000, 'stalled'), gap('gap-2', 900000, 'none'), gap('gap-3', 1200000, 'stalled'), gap('gap-4', 99000, 'stalled')])
    expect(stalled).toEqual({ row: { type: 'gap', id: 'gap-3' }, turn: 't2', dur_ms: 1200000 })
    expect(planJump('stalled', { ...L, stalled })).toMatchObject({ kind: 'go', state: 'stalled', durMs: 1200000, row: { id: 'gap-3' } })
    expect(longestStalled([gap('gap-2', 900000, 'none')])).toBeNull()
  })

  it('stalled with no stretch of 2 min says so', () => {
    expect(planJump('stalled', { ...L, stalled: null })).toEqual({ kind: 'none', toast: ['No single stretch of stalled is 2 min or longer; the total is many short pauses.'] })
  })

  it('no tool run at all gives its own toast', () => {
    expect(planJump('tool', L)).toEqual({ kind: 'none', toast: ['No tool run in this session.'] })
  })
})

describe('jumpToast (SES-27)', () => {
  const TZ = 'Asia/Kolkata'
  it('names the gap start clock and the turn', () => {
    const plan = planJump('limit', S.longest as Longest)
    if (plan.kind !== 'go') throw new Error('expected go')
    // 1791786480000 ms = 2026-10-12T06:28Z = 11:58 IST
    expect(jumpToast(plan, 2, TZ)).toEqual(['Longest stretch of usage limit: ', { b: '1h02m' }, ' at 11:58 · turn 2'])
  })

  it('a model call has no clock', () => {
    const plan = planJump('model', S.longest as Longest)
    if (plan.kind !== 'go') throw new Error('expected go')
    expect(jumpToast(plan, 2, TZ)).toEqual(['Longest model call: ', { b: '2m' }, ' · turn 2'])
  })

  it('parses a gap id', () => {
    expect(gapStartMs({ type: 'gap', id: 'gap-1791786480000' })).toBe(1791786480000)
    expect(gapStartMs({ type: 'call', id: 'x' })).toBeNull()
  })
})
