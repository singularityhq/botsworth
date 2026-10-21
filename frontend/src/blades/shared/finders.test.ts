import { describe, expect, it } from 'vitest'
import type { AnyRow, FindResult, ToolRow } from '../../api/types'
import { turn } from '../session/testRows'
import {
  biggestRowToast, biggestTurnToast, excludedFromTimeRanking, finderToast, goPlan, hitPool, matchingRows, noHitToast, orderViolation, ordinal,
  rankRows, rankStep, rankTurns, stepHit, turnPositions, type From, type Hit,
} from './finders'

const T = (n: number) => `s/turn/${n}`
const positions = turnPositions([1, 2, 3, 4].map((n) => ({ turn: T(n) })))
const hit = (turnN: number, id: string, o: Partial<Hit> = {}): Hit => ({
  row: { type: 'tool', id }, turn: T(turnN), display_id: `T${turnN}`, depth: 0, label: id, value: null, start_ms: null, ...o,
})
const result = (hits: Hit[]): FindResult => ({ freshness: {} as FindResult['freshness'], kind: 'failures', total: hits.length, hits, per_turn: {} })

// Session order: turn 1: a1; turn 2: b1, b2 (inside an agent); turn 4: d1.
const pool = hitPool(result([hit(1, 'a1'), hit(2, 'b1'), hit(2, 'b2', { depth: 1 }), hit(4, 'd1')]), positions, null)
const ids = (r: { hit: Hit } | null) => r?.hit.row.id

describe('hit pool (SES-51)', () => {
  it('skips hits in turns that are not numbered turns', () => {
    const r = result([hit(1, 'a1'), { ...hit(1, 'h1'), turn: 's/turn/harness' }])
    expect(hitPool(r, positions, null).map((h) => h.row.id)).toEqual(['a1'])
  })

  it('with a search active keeps only the rows the search matches', () => {
    const search = result([hit(2, 'b2'), hit(4, 'd1')])
    expect(hitPool(result(pool), positions, matchingRows(search)).map((h) => h.row.id)).toEqual(['b2', 'd1'])
  })

  it('with the search still loading (no result) does not narrow', () => {
    expect(matchingRows(undefined)).toBeNull()
  })

  it('reports the first hit that breaks session order, and trusts the order otherwise', () => {
    expect(orderViolation(pool, positions)).toBeNull()
    const bad = [hit(2, 'b1'), hit(1, 'a1')]
    expect(orderViolation(bad, positions)).toMatchObject({ index: 1, hit: { row: { id: 'a1' } } })
  })
})

describe('stepHit from the session blade: a turn start (SES-51)', () => {
  const at = (n: number): From => ({ at: 'turn', turn: T(n) })

  it('next starts at the cursor turn, so a hit inside the cursor turn is found', () => {
    expect(ids(stepHit(pool, positions, at(2), 1))).toBe('b1')
  })

  it('next from a turn with no hit finds the first hit after it', () => {
    expect(ids(stepHit(pool, positions, at(3), 1))).toBe('d1')
  })

  it('previous is the last hit before the cursor turn', () => {
    expect(ids(stepHit(pool, positions, at(2), -1))).toBe('a1')
    expect(ids(stepHit(pool, positions, at(4), -1))).toBe('b2')
  })

  it('wraps: next past the last hit is the first, previous before the first is the last', () => {
    expect(stepHit(pool, positions, at(1), -1)).toMatchObject({ hit: { row: { id: 'd1' } }, index: 3 })
    const lastTurnOnly = hitPool(result([hit(1, 'a1'), hit(2, 'b1')]), positions, null)
    expect(ids(stepHit(lastTurnOnly, positions, at(3), 1))).toBe('a1')
  })

  it('with no cursor, next is the first hit and previous the last', () => {
    expect(ids(stepHit(pool, positions, null, 1))).toBe('a1')
    expect(ids(stepHit(pool, positions, null, -1))).toBe('d1')
  })

  it('returns the hit index, for "N of M"', () => {
    expect(stepHit(pool, positions, at(2), 1)!.index).toBe(1)
  })

  it('is null with no hits', () => {
    expect(stepHit([], positions, at(1), 1)).toBeNull()
  })
})

describe('stepHit from the session rail with a turn open on a hit', () => {
  it('continues from that hit: next is the one after it, previous the one before it, wrapping', () => {
    const at = (row: string): From => ({ at: 'hit', turn: T(2), row })
    expect(ids(stepHit(pool, positions, at('b1'), 1))).toBe('b2')
    expect(ids(stepHit(pool, positions, at('b1'), -1))).toBe('a1')
    expect(ids(stepHit(pool, positions, at('d1'), 1))).toBe('a1')
    expect(ids(stepHit(pool, positions, at('a1'), -1))).toBe('d1')
  })

  it('a row that is not in this finder set falls back to the start of its turn', () => {
    expect(ids(stepHit(pool, positions, { at: 'hit', turn: T(3), row: 'gap-elsewhere' }, 1))).toBe('d1')
  })
})

describe('stepHit from the turn blade: after the cursor row (SES-51)', () => {
  const order = ['b0', 'b1', 'b1x', 'b2', 'b3']
  const row = (id: string): From => ({ at: 'row', turn: T(2), row: id, order })

  it('next is the first hit after the cursor row, rows inside an agent after their agent row', () => {
    expect(ids(stepHit(pool, positions, row('b0'), 1))).toBe('b1')
    expect(ids(stepHit(pool, positions, row('b1'), 1))).toBe('b2')
  })

  it('a hit on the cursor row itself is not "after" it', () => {
    expect(ids(stepHit(pool, positions, row('b2'), 1))).toBe('d1')
  })

  it('moves on to the next turn, then wraps to the first turn', () => {
    expect(ids(stepHit(pool, positions, row('b3'), 1))).toBe('d1')
    const last: From = { at: 'row', turn: T(4), row: 'd1', order: ['d0', 'd1'] }
    expect(ids(stepHit(pool, positions, last, 1))).toBe('a1')
  })

  it('previous is the last hit before the cursor row, crossing to the earlier turn', () => {
    expect(ids(stepHit(pool, positions, row('b2'), -1))).toBe('b1')
    expect(ids(stepHit(pool, positions, row('b1'), -1))).toBe('a1')
  })

  it('does not throw on a same-turn hit that is not in the row order: it lands before the cursor row when no agent owns it', () => {
    expect(ids(stepHit(pool, positions, { at: 'row', turn: T(2), row: 'b0', order: ['b0'] }, 1))).toBe('d1')
    expect(ids(stepHit(pool, positions, { at: 'row', turn: T(2), row: 'b0', order: ['b0'] }, -1))).toBe('b2')
  })

  it('refuses a cursor turn outside the session order', () => {
    expect(() => stepHit(pool, positions, { at: 'turn', turn: 'nope' }, 1)).toThrow(/not a numbered turn/)
  })
})

describe('toasts (SES-51, SES-52)', () => {
  it('names the hit: "<What> N of M: row name · turn K"', () => {
    expect(finderToast('failures', 0, 3, hit(12, 'x', { label: 'Bash: npm test' }))).toEqual(['Failure 1 of 3: ', { b: 'Bash: npm test', plain: true }, ' · turn 12'])
  })

  it('appends the duration for a wait', () => {
    expect(finderToast('waits', 1, 4, hit(3, 'g', { label: 'Waiting on you', value: 3_720_000 }))).toEqual([
      'Wait 2 of 4: ', { b: 'Waiting on you', plain: true }, ' · turn 3', ' · ', { b: '1h02m' },
    ])
  })

  it('cuts the row name at 60 characters', () => {
    const t = finderToast('agents', 0, 1, hit(1, 'x', { label: 'y'.repeat(80) }))
    expect((t[1] as { b: string }).b).toHaveLength(60)
    expect(t[1]).toMatchObject({ plain: true })
    expect((t[1] as { b: string }).b.endsWith('…')).toBe(true)
  })

  it('says so when nothing is hit', () => {
    expect(noHitToast('waits')).toEqual(['No wait in this session.'])
    expect(noHitToast('skills')).toEqual(['No skill load in this session.'])
  })

  it('b in the session blade: "Biggest turn by cost: $24.69 · turn 23", then 2nd, 3rd, 11th', () => {
    expect(biggestTurnToast(0, 'cost', 24.69, 23)).toEqual(['Biggest turn by cost: ', { b: '$24.69' }, ' · turn 23'])
    expect(biggestTurnToast(1, 'time', 135_000, 4)).toEqual(['2nd biggest turn by time: ', { b: '2m' }, ' · turn 4'])
    expect(ordinal(3)).toBe('3rd')
    expect(ordinal(11)).toBe('11th')
    expect(ordinal(22)).toBe('22nd')
  })

  it('b in the turn blade names the ranking size, the figure, the turn and the row (50 chars)', () => {
    expect(biggestRowToast(0, 412, 'cost', hit(2, 'c', { label: 'claude-opus call', value: 1.77 }))).toEqual([
      'Biggest row by cost of 412: ', { b: '$1.77' }, ' · turn 2 · claude-opus call',
    ])
  })
})

describe('b ranking of turns by each measure (SES-52)', () => {
  const t1 = turn(1, { cost_usd: 1, wall_ms: 300, tok_total: 10 })
  const t2 = turn(2, { cost_usd: 5, wall_ms: 100, tok_total: 30 })
  const t3 = turn(3, { cost_usd: 3, wall_ms: 200, tok_total: 20 })

  it('ranks by cost, by wall for Time, by tokens', () => {
    expect(rankTurns([t1, t2, t3], 'cost').map((t) => t.display_id)).toEqual(['T2', 'T3', 'T1'])
    expect(rankTurns([t1, t2, t3], 'time').map((t) => t.display_id)).toEqual(['T1', 'T3', 'T2'])
    expect(rankTurns([t1, t2, t3], 'tokens').map((t) => t.display_id)).toEqual(['T2', 'T3', 'T1'])
  })

  it('leaves out a turn with no value, and keeps list order among equal values', () => {
    const none = turn(4, { cost_usd: null })
    const tie = turn(5, { cost_usd: 3 })
    expect(rankTurns([t1, none, t3, tie, t2], 'cost').map((t) => t.display_id)).toEqual(['T2', 'T3', 'T5', 'T1'])
  })

  it('the first b lands on the biggest, each further b on the next, B on the previous, clamped at both ends', () => {
    let pos = -1
    pos = rankStep(pos, 1, 3)
    expect(pos).toBe(0)
    pos = rankStep(pos, 1, 3)
    pos = rankStep(pos, 1, 3)
    expect(pos).toBe(2)
    expect(rankStep(pos, 1, 3)).toBe(2)
    expect(rankStep(pos, -1, 3)).toBe(1)
    expect(rankStep(0, -1, 3)).toBe(0)
    expect(rankStep(-1, -1, 3)).toBe(0) // B before the first b: the biggest
  })
})

describe('row ranking (SES-52: tools by time, agents as one row, gaps by time, zero left out)', () => {
  const tool = (o: Partial<ToolRow>): ToolRow =>
    ({ type: 'tool', id: 't', depth: 0, dur_ms: 1000, user_wait: null, max_silence_ms: null, ...o }) as ToolRow
  const call = (id: string, cost: number, dur: number, tok: number): AnyRow =>
    ({ type: 'call', id, depth: 0, cost_usd: cost, dur_ms: dur, tok: { total: tok } }) as AnyRow
  const agent = (id: string, cost: number, run: number, tok: number): AnyRow =>
    ({ type: 'agent', id, depth: 0, cost_usd: cost, run_ms: run, tok_total: tok }) as AnyRow

  it('by time leaves out a tool that waits on the user and one with a silence of 5 min or more', () => {
    expect(excludedFromTimeRanking(tool({ user_wait: 'permission' }))).toBe(true)
    expect(excludedFromTimeRanking(tool({ max_silence_ms: 300_000 }))).toBe(true)
    expect(excludedFromTimeRanking(tool({ max_silence_ms: 299_999 }))).toBe(false)
    const rows = [tool({ id: 'a', dur_ms: 9000, user_wait: 'permission' }), tool({ id: 'b', dur_ms: 8000, max_silence_ms: 400_000 }), tool({ id: 'c', dur_ms: 500 })]
    expect(rankRows(rows, 'time').map((r) => r.id)).toEqual(['c'])
  })

  it('tools have no cost or tokens, so only calls and agents rank there', () => {
    const rows = [call('c1', 2, 10, 5), tool({ id: 't1' }), agent('a1', 9, 9999, 50)]
    expect(rankRows(rows, 'cost').map((r) => r.id)).toEqual(['a1', 'c1'])
    expect(rankRows(rows, 'tokens').map((r) => r.id)).toEqual(['a1', 'c1'])
    expect(rankRows(rows, 'time').map((r) => r.id)).toEqual(['a1', 't1', 'c1'])
  })

  it('counts an agent row once with its whole figure, and ignores rows inside agents', () => {
    const inner = { ...call('inner', 100, 1, 1), depth: 1 } as AnyRow
    expect(rankRows([agent('a1', 3, 1, 1), inner], 'cost').map((r) => r.id)).toEqual(['a1'])
  })

  it('leaves out a row with a value of 0', () => {
    expect(rankRows([call('c0', 0, 10, 5)], 'cost')).toEqual([])
  })
})

describe('goPlan (SES-50, SES-57)', () => {
  const none = { t: null, zoom: null, order: null, detail: false }

  it('opens the hit turn at the hit row', () => {
    expect(goPlan(hit(2, 'b1'), 'failures', 2, { current: none }).patch).toEqual({ t: 2, step: 'b1', detail: false })
  })

  it('only w opens the detail blade; the others keep its state', () => {
    expect(goPlan(hit(2, 'g'), 'waits', 2, { current: none }).patch.detail).toBe(true)
    expect(goPlan(hit(2, 'b1'), 'agents', 2, { current: { ...none, t: 2, detail: true } }).patch.detail).toBe(true)
    expect(goPlan(hit(2, 'b1'), 'agents', 2, { current: { ...none, t: 2, detail: false } }).patch.detail).toBe(false)
  })

  it('a hit on a turn itself opens the turn at its first row', () => {
    expect(goPlan({ ...hit(2, 'T2'), row: { type: 'turn', id: 'T2' } }, 'skills', 2, { current: none }).patch.step).toBeNull()
  })

  it('resets a zoom when the turn changes, or when the row starts outside the zoom, and keeps it otherwise', () => {
    const zoom = { start_ms: 1000, end_ms: 2000 }
    const cur = { ...none, t: 2, zoom }
    expect(goPlan(hit(3, 'x', { start_ms: 1500 }), 'failures', 3, { current: cur }).patch.zoom).toBeNull()
    expect(goPlan(hit(2, 'x', { start_ms: 5000 }), 'failures', 2, { current: cur }).patch.zoom).toBeNull()
    expect(goPlan(hit(2, 'x', { start_ms: 1500 }), 'failures', 2, { current: cur }).patch).not.toHaveProperty('zoom')
  })

  it('sets Order back to As it ran when the row is not in the Cost or Duration list', () => {
    const cur = { ...none, t: 2, order: 'cost' as const }
    expect(goPlan(hit(2, 'x'), 'failures', 2, { current: cur }).patch.order).toBeNull()
    expect(goPlan(hit(2, 'x'), 'failures', 2, { current: cur, rowInOrderedList: true }).patch).not.toHaveProperty('order')
  })

  it('tells the turn blade to show the agent rows when the hit is inside an agent, and to open a fold that hides it', () => {
    expect(goPlan(hit(2, 'x', { depth: 1 }), 'failures', 2, { current: none })).toMatchObject({ showAgents: true, openFold: true })
    expect(goPlan(hit(2, 'x'), 'failures', 2, { current: none }).showAgents).toBe(false)
  })
})

describe('stepHit from a row of the open turn (SES-51)', () => {
  const rhit = (turn: string, id: string, depth = 0, start: number | null = null): Hit => hit(Number(turn), id, { depth, start_ms: start })
  const order = ['r1', 'r2', 'r3', 'r4']
  const rpool = [rhit('1', 'r2'), rhit('1', 'r4'), rhit('2', 'b1')]
  const row = (turn: number, cursor: string, o: readonly string[], agentStarts?: { id: string; start_ms: number }[]): From => ({ at: 'row', turn: T(turn), row: cursor, order: o, agentStarts })

  it('goes to the next hit after the cursor row, crossing turns, then wraps', () => {
    expect(ids(stepHit(rpool, positions, row(1, 'r1', order), 1))).toBe('r2')
    expect(ids(stepHit(rpool, positions, row(1, 'r2', order), 1))).toBe('r4')
    expect(ids(stepHit(rpool, positions, row(1, 'r4', order), 1))).toBe('b1')
    expect(ids(stepHit(rpool, positions, row(2, 'x', ['x']), 1))).toBe('r2')
  })

  it('goes back and wraps to the last', () => {
    expect(ids(stepHit(rpool, positions, row(1, 'r4', order), -1))).toBe('r2')
    expect(ids(stepHit(rpool, positions, row(1, 'r2', order), -1))).toBe('b1')
  })

  it('does not throw on a same-turn hit whose row is not loaded: it sits just after its agent row, by start time', () => {
    const o = ['r1', 'ag', 'r3', 'r4']
    const starts = [{ id: 'ag', start_ms: 1000 }]
    const p = [rhit('1', 'r2'), rhit('1', 'deep', 1, 1500), rhit('1', 'r4')]
    expect(ids(stepHit(p, positions, row(1, 'ag', o, starts), 1))).toBe('deep')
    expect(ids(stepHit(p, positions, row(1, 'r3', o, starts), -1))).toBe('deep')
    expect(ids(stepHit(p, positions, row(1, 'r3', o, starts), 1))).toBe('r4')
  })

  it('does not throw when the cursor row is not in the order: the walk starts at the turn start', () => {
    expect(ids(stepHit(rpool, positions, row(1, 'gone', order), 1))).toBe('r2')
  })
})
