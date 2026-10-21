import { describe, expect, it } from 'vitest'
import type { TurnRow } from '../../api/types'
import { buildNodes } from './rowList.logic'
import { agent, call, gap, tool } from './testRows'
import {
  clockSec, handoffToast, histogramShown, isBig, ledgerGrid, longestInTurn, overTimeToggleShown, sessionEdgeToast, skillCounter, skillCounterText,
  stepToTurn, turnAxisSpan, waterfallGrid, type NumberedStop,
} from './turn.logic'

describe('ledgerGrid (TRN-11)', () => {
  it('is the eight-column grid from 860 px', () => {
    expect(ledgerGrid(860)).toEqual({
      ids: ['when', 'glyph', 'name', 'flags', 'bar', 'cost', 'time', 'tokens'],
      template: '76px 20px minmax(0,1fr) 96px 104px 72px 72px 56px',
    })
  })
  it('drops the bar and narrows the columns under 860 px', () => {
    expect(ledgerGrid(859)).toEqual({ ids: ['when', 'glyph', 'name', 'flags', 'cost', 'time', 'tokens'], template: '72px 20px minmax(0,1fr) 56px 64px 64px 64px' })
  })
  it('has its own Waterfall grids', () => {
    expect(waterfallGrid(1004)).toBe('76px 20px minmax(0,300px) minmax(0,1fr) 64px')
    expect(waterfallGrid(700)).toBe('64px 20px minmax(0,200px) minmax(0,1fr) 56px')
  })
})

describe('isBig (TRN-31)', () => {
  it('is 25 % of the largest and up', () => {
    expect(isBig(25, 100)).toBe(true)
    expect(isBig(24.9, 100)).toBe(false)
    expect(isBig(null, 100)).toBe(false)
    expect(isBig(5, 0)).toBe(false)
  })
})

describe('clockSec', () => {
  it('prints HH:MM:SS in the store zone', () => {
    expect(clockSec(1_791_790_200_000, 'Asia/Kolkata')).toBe('13:00:00')
    expect(clockSec(1_791_790_259_000, 'UTC')).toBe('07:30:59')
  })
})

describe('histogramShown (TRN-20)', () => {
  it('shows with an agent row or a wall of 30 min, else hides', () => {
    expect(histogramShown(1, 1000, null)).toBe(true)
    expect(histogramShown(0, 30 * 60_000, null)).toBe(true)
    expect(histogramShown(0, 30 * 60_000 - 1, null)).toBe(false)
    expect(histogramShown(0, null, null)).toBe(false)
  })
  it('lets thist override the rule either way', () => {
    expect(histogramShown(3, 1, false)).toBe(false)
    expect(histogramShown(0, 1, true)).toBe(true)
  })
  it('puts the Over time toggle in the meta line only when the rule hides the chart or thist is set', () => {
    expect(overTimeToggleShown(2, 1, null)).toBe(false)
    expect(overTimeToggleShown(0, 1, null)).toBe(true)
    expect(overTimeToggleShown(2, 1, false)).toBe(true)
    expect(overTimeToggleShown(2, 1, true)).toBe(true)
  })
})

describe('turnAxisSpan (D41 N1)', () => {
  const t = (end: number, runs: number[]): Pick<TurnRow, 'start_ms' | 'end_ms' | 'agent_runs'> => ({
    start_ms: 1000, end_ms: end, agent_runs: runs.map((r, i) => ({ engagement: `e${i}`, agent_name: null, start_ms: 1000, end_ms: r })),
  })
  it('ends at the later of the turn end and the latest agent run end, with a tail zone', () => {
    expect(turnAxisSpan(t(5000, [4000, 9000, 7000]))).toEqual({ a: 1000, b: 9000, tailFrom: 5000 })
  })
  it('has no tail when the agents ended with the turn', () => {
    expect(turnAxisSpan(t(5000, [4000]))).toEqual({ a: 1000, b: 5000, tailFrom: null })
  })
  it('has no axis without a start or end', () => {
    expect(turnAxisSpan({ start_ms: null, end_ms: null, agent_runs: [] })).toBeNull()
  })
})

describe('stepToTurn (TRN-50, TRN-53)', () => {
  const row = (n: number, o: Partial<TurnRow> = {}) => ({ n_llm_calls: 2, n_agents: 0, n_skill_loads: 0, n_failed: 0, label: `turn ${n} text`, ...o }) as TurnRow
  const stops: NumberedStop[] = [
    { ix: 1, row: row(1, { turn: 't1' }) },
    { ix: 2, row: row(2, { n_llm_calls: 0, turn: 't2' }) },
    { ix: 3, row: row(3, { turn: 't3' }) },
  ]
  it('skips a turn with no rows in either direction', () => {
    expect(stepToTurn(stops, 1, 1, null)?.ix).toBe(3)
    expect(stepToTurn(stops, 3, -1, null)?.ix).toBe(1)
  })
  it('is null at the session’s edge', () => {
    expect(stepToTurn(stops, 3, 1, null)).toBeNull()
    expect(stepToTurn(stops, 1, -1, null)).toBeNull()
  })
  it('with a search keeps only the turns that match', () => {
    expect(stepToTurn(stops, 1, 1, new Set(['t2']))?.ix).toBe(2)
    expect(stepToTurn(stops, 1, 1, new Set())).toBeNull()
  })
  it('words the toasts', () => {
    expect(handoffToast(1, stops[2])).toEqual(['Turn 1 → turn 3: turn 3 text'])
    expect(handoffToast(1, { ix: 2, row: row(2, { label: 'x'.repeat(80) }) })[0]).toBe(`Turn 1 → turn 2: ${'x'.repeat(69)}…`)
    expect(sessionEdgeToast(1)).toEqual(['Last row of the session.'])
    expect(sessionEdgeToast(-1)).toEqual(['First row of the session.'])
  })
})

describe('longestInTurn (TRN-4 legend jump)', () => {
  it('finds the longest main-thread call and tool, and the longest gap of each state', () => {
    const rows = [
      call('a', { dur_ms: 3000 }), call('b', { dur_ms: 9000, sec: 10 }), call('inner', { dur_ms: 99_000, depth: 1, sec: 11 }),
      tool('t1', { dur_ms: 4000, sec: 20 }), tool('w', { dur_ms: 900_000, user_wait: 'ask', sec: 30 }),
      gap(100, 200, { state: 'you', substate: 'reply' }), gap(400, 400, { state: 'you', substate: 'reply' }), gap(900, 100), gap(1100, 300, { state: 'agents', substate: null }),
    ]
    const l = longestInTurn(rows, 'T')
    expect(l.model).toMatchObject({ dur_ms: 9000, turn: 'T' })
    expect(l.model!.row.id.endsWith('llm/b')).toBe(true)
    expect(l.tool!.dur_ms).toBe(4000)
    expect(l.you!.dur_ms).toBe(900_000) // the AskUserQuestion row outlasts the 400 s gap
    expect(l.you!.row.type).toBe('tool')
    expect(l.tool!.dur_ms).toBe(4000)
    expect(l.agents!.dur_ms).toBe(300_000)
    expect(l.none).toBeNull() // 100 s is under 2 min
    expect(l.limit).toBeNull()
  })
})

describe('skillCounter (TRN-52)', () => {
  const load = (id: string, sec: number, skill = 'skill-writer') => tool(id, { skill_load: skill, skill_via: 'tool', sec })
  const plain = (id: string, sec: number) => call(id, { sec })
  const list = (...rows: (ReturnType<typeof plain> | ReturnType<typeof load> | ReturnType<typeof gap>)[]) => buildNodes(rows)

  it('says "loaded here" on the load row', () => {
    const rows = list(plain('a', 0), load('l', 5))
    const c = skillCounter(rows, rows[1].id, null)
    expect(c).toEqual({ kind: 'here', skill: 'skill-writer' })
    expect(skillCounterText(c!)).toEqual({ skill: 'skill-writer', before: '', after: ' loaded here' })
  })
  it('counts rows after the load, gap rows excluded, with the elapsed time', () => {
    const rows = list(load('l', 0), plain('a', 60), gap(70, 200), plain('b', 600), tool('c', { sec: 601 }))
    const c = skillCounter(rows, rows[4].id, null)
    expect(c).toEqual({ kind: 'after', skill: 'skill-writer', n: 3, inTurn: null, plusMs: 601_000 })
    expect(skillCounterText(c!)).toEqual({ skill: 'skill-writer', before: 'row 3 after ', after: ' loaded · +10m' })
  })
  it('uses the latest load at or before the cursor', () => {
    const rows = list(load('l1', 0, 'one'), plain('a', 1), load('l2', 2, 'two'), plain('b', 3))
    expect(skillCounter(rows, rows[3].id, null)).toMatchObject({ skill: 'two', n: 1 })
  })
  it('is null before any load', () => {
    const rows = list(plain('a', 0), load('l', 5))
    expect(skillCounter(rows, rows[0].id, null)).toBeNull()
  })
  it('counts only rows at the cursor’s own level: inside an agent, the same agent', () => {
    const ag = agent('ag', { sec: 1 })
    const nodes = buildNodes([load('main', 0), ag], new Map([[ag.id, { rows: [call('k1', { depth: 1, sec: 2 }), call('k2', { depth: 1, sec: 3 })] }]]))
    expect(skillCounter(nodes, nodes[3].id, null)).toBeNull() // the main-thread load is another level
    const own = buildNodes([ag], new Map([[ag.id, { rows: [tool('ownload', { depth: 1, skill_load: 'inner', sec: 2 }), call('k2', { depth: 1, sec: 3 })] }]]))
    expect(skillCounter(own, own[2].id, null)).toMatchObject({ skill: 'inner', n: 1 })
  })
  it('reaches into the previous numbered turn for a cursor on the main thread, counting its rows too', () => {
    const prev = list(plain('p0', 0), load('pl', 10), plain('p1', 20), plain('p2', 30))
    const cur = list(plain('c0', 100), plain('c1', 110))
    const c = skillCounter(cur, cur[1].id, { ix: 1, nodes: prev })
    expect(c).toEqual({ kind: 'after', skill: 'skill-writer', n: 4, inTurn: 1, plusMs: 110_000 - 10_000 })
    expect(skillCounterText(c!).after).toBe(' loaded in turn 1 · +2m')
  })
  it('does not reach back when the cursor is inside an agent', () => {
    const ag = agent('ag')
    const nodes = buildNodes([ag], new Map([[ag.id, { rows: [call('k', { depth: 1 })] }]]))
    expect(skillCounter(nodes, nodes[1].id, { ix: 1, nodes: list(load('pl', 0)) })).toBeNull()
  })
  it('is null with no cursor row', () => {
    expect(skillCounter(list(plain('a', 0)), null, null)).toBeNull()
  })
})
