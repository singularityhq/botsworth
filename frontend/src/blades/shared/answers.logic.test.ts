import { describe, expect, it } from 'vitest'
import { fixtures } from '../../api/__fixtures__'
import {
  costCaption, gapStartMs, jumpToast, legendStates, planJump, runningSplit, segmentWidths, stateTotals, totalsSum,
  type Longest,
} from './answers.logic'
import { STATE_KEYS } from './states'

const S = fixtures.sessionDetail.session

describe('stateTotals (SES-15, SES-16)', () => {
  it('the seven segments sum to the wall (fixture session)', () => {
    const t = stateTotals(S.time_split, S.none_split)
    expect(totalsSum(t)).toBe(S.wall_ms)
    expect(t).toEqual({ model: 120000, tool: 45000, agents: 300000, you: 30000, agentmsg: 0, limit: 30000, none: 0 })
  })

  it('a turn has no none_split: its none_ms is all "nothing running"', () => {
    const t = stateTotals({ model_ms: 10, tool_ms: 20, agents_ms: 30, none_ms: 40 })
    expect(t.none).toBe(40)
    expect([t.you, t.agentmsg, t.limit]).toEqual([0, 0, 0])
  })

  it('splits running from nothing running against the wall', () => {
    const t = stateTotals(S.time_split, S.none_split)
    expect(runningSplit(t, S.wall_ms)).toEqual({ running: 465000, nothing: 60000 })
  })
})

describe('legendStates (SES-26)', () => {
  const base = Object.fromEntries(STATE_KEYS.map((k) => [k, 0])) as ReturnType<typeof stateTotals>

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
    // The server has no before_first key in none_split; that stretch is part of `none`.
    const t = stateTotals({ model_ms: 100000, tool_ms: 0, agents_ms: 0, none_ms: 1860000 }, { you: 0, agentmsg: 0, limit: 0, none: 1860000 })
    expect(legendStates(t)).toEqual(['none', 'model'])
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
