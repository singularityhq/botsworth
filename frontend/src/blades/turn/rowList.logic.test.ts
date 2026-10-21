import { describe, expect, it } from 'vitest'
import { agent, at, call, gap, ids, tool } from './testRows'
import type { AnyRow } from '../../api/types'
import {
  buildNodes, flatRows, foldHolding, foldKey, moveInList, positionOf, settleCursor, sortedByOrder, visibleRows, type AgentPage, type BandState, type Item,
  type ViewOpts,
} from './rowList.logic'

const base: ViewOpts = { openAgents: new Set(), zoom: null, search: null, order: null, showAll: false, unfolded: new Set() }
const view = (rows: AnyRow[], o: Partial<ViewOpts> = {}, pages: Map<string, AgentPage> = new Map()) => visibleRows(buildNodes(rows, pages), { ...base, ...o })
const rowIds = (items: Item[]) => items.map((it) => (it.type === 'row' ? ids([it.node.row])[0] : `${it.type}:${it.key.slice(0, 40)}`))

describe('buildNodes', () => {
  it('drops gaps under 2 min (SES-16)', () => {
    expect(buildNodes([call('a'), gap(10, 119), gap(200, 120)]).map((n) => n.row.type)).toEqual(['call', 'gap'])
  })
  it('splices a loaded page after its agent row and records the containing agents', () => {
    const ag = agent('ag')
    const kid = call('k', { depth: 1, sec: 5 })
    const nodes = buildNodes([call('a'), ag, call('b', { sec: 100 })], new Map([[ag.id, { rows: [kid] }]]))
    expect(ids(nodes.map((n) => n.row))).toEqual(['llm/a', 'engagement/ag', 'llm/k', 'llm/b'])
    expect(nodes[2]).toMatchObject({ depth: 1, ancestors: [ag.id] })
    expect(nodes[3].ancestors).toEqual([])
  })
  it('nests agents: a page row that is an agent takes its own page, up to three levels', () => {
    const a1 = agent('a1')
    const a2 = agent('a2', { depth: 1 })
    const a3 = agent('a3', { depth: 2 })
    const leaf = tool('leaf', { depth: 3 })
    const pages = new Map<string, AgentPage>([
      [a1.id, { rows: [a2] }],
      [a2.id, { rows: [a3] }],
      [a3.id, { rows: [leaf] }],
    ])
    const nodes = buildNodes([a1], pages)
    expect(nodes.map((n) => n.depth)).toEqual([0, 1, 2, 3])
    expect(nodes[3].ancestors).toEqual([a1.id, a2.id, a3.id])
  })
  it('reads descendants that come inline in one page by depth', () => {
    const a1 = agent('a1')
    const a2 = agent('a2', { depth: 1 })
    const inner = call('i', { depth: 2 })
    const after = call('after', { depth: 1 })
    const nodes = buildNodes([a1], new Map([[a1.id, { rows: [a2, inner, after] }]]))
    expect(nodes.map((n) => n.ancestors.length)).toEqual([0, 1, 2, 1])
  })
})

describe('visibleRows: As it ran (TRN-13)', () => {
  const ag = agent('ag')
  const kids = [call('k1', { depth: 1, sec: 1 }), tool('k2', { depth: 1, sec: 2, summary: 'Read it' })]
  const pages = new Map<string, AgentPage>([[ag.id, { rows: kids }]])
  const top = [call('a'), ag, call('b', { sec: 100 })]

  it('keeps an agent folded by default (TRN-35)', () => {
    expect(rowIds(view(top, {}, pages))).toEqual(['row:llm/a'.slice(4), 'engagement/ag', 'llm/b'].map((s) => (s.startsWith('llm/a') ? 'llm/a' : s)))
  })
  it('shows an open agent’s rows right below it', () => {
    expect(ids(flatRows(view(top, { openAgents: new Set([ag.id]) }, pages)).map((n) => n.row))).toEqual(['llm/a', 'engagement/ag', 'llm/k1', 'tool/k2', 'llm/b'])
  })
  it('shows nested rows only when every containing agent is open', () => {
    const a2 = agent('a2', { depth: 1 })
    const deep = call('deep', { depth: 2 })
    const p = new Map<string, AgentPage>([[ag.id, { rows: [a2] }], [a2.id, { rows: [deep] }]])
    expect(ids(flatRows(view([ag], { openAgents: new Set([ag.id]) }, p)).map((n) => n.row))).toEqual(['engagement/ag', 'engagement/a2'])
    expect(ids(flatRows(view([ag], { openAgents: new Set([ag.id, a2.id]) }, p)).map((n) => n.row))).toEqual(['engagement/ag', 'engagement/a2', 'llm/deep'])
  })
})

describe('visibleRows: band states (TRN-35, D38 M8)', () => {
  const ag = agent('ag')
  const kids = [call('k1', { depth: 1, sec: 1 }), call('k2', { depth: 1, sec: 2 })]
  const pages = new Map<string, AgentPage>([[ag.id, { rows: kids }]])
  const bands = (b: BandState) => new Map<string, BandState>([[ag.id, b]])
  const open = new Set([ag.id])
  it('puts a skeleton item below an agent whose page is loading', () => {
    const items = view([ag], { openAgents: open, bands: bands({ status: 'loading' }) })
    expect(items.map((i) => i.type)).toEqual(['row', 'band'])
    expect(items[1]).toMatchObject({ status: 'loading' })
  })
  it('puts the error card item below an agent whose page failed', () => {
    expect(view([ag], { openAgents: open, bands: bands({ status: 'error', message: 'boom' }) })[1]).toMatchObject({ type: 'band', status: 'error', message: 'boom' })
  })
  it('adds "Show N more" after the last loaded row when the page is not complete', () => {
    const items = view([ag, call('after', { sec: 50 })], { openAgents: open, bands: bands({ status: 'ready', total: 502, loaded: 2 }) }, pages)
    expect(items.map((i) => i.type)).toEqual(['row', 'row', 'row', 'more', 'row'])
    expect(items[3]).toMatchObject({ kind: 'band', agent: ag.id, n: 500 })
  })
  it('adds nothing when every row is loaded', () => {
    expect(view([ag], { openAgents: open, bands: bands({ status: 'ready', total: 2, loaded: 2 }) }, pages).some((i) => i.type === 'more')).toBe(false)
  })
})

describe('visibleRows: zoom (TRN-12, TRN-24)', () => {
  const rows = [call('a', { sec: 0 }), call('b', { sec: 100 }), call('c', { sec: 200 }), gap(150, 300), gap(500, 200)]
  const zoom = { start_ms: at(90), end_ms: at(210) }
  it('keeps rows that start inside the range and gaps that overlap it', () => {
    expect(ids(flatRows(view(rows, { zoom })).map((n) => n.row))).toEqual(['llm/b', 'llm/c', `gap-${at(150)}`])
  })
  it('keeps a gap that started before the range and ends inside it', () => {
    const g = gap(0, 130)
    expect(flatRows(view([g], { zoom })).length).toBe(1)
  })
  it('excludes a row that starts exactly at the range end', () => {
    expect(flatRows(view([call('a', { sec: 100 })], { zoom: { start_ms: at(0), end_ms: at(100) } }))).toHaveLength(0)
  })
  it('places an agent row at its earliest run', () => {
    const ag = agent('ag', { sec: 120 })
    expect(flatRows(view([ag], { zoom })).length).toBe(1)
    expect(flatRows(view([agent('late', { sec: 400 })], { zoom })).length).toBe(0)
  })
})

describe('visibleRows: search (TRN-12, TRN-37)', () => {
  const quiet = [0, 1, 2, 3, 4].map((i) => tool(`q${i}`, { sec: i, dur_ms: 1000 }))
  it('keeps matching rows and turns quiet folds off', () => {
    const search = new Set(quiet.map((q) => q.id))
    const items = view(quiet, { search })
    expect(items.every((i) => i.type === 'row')).toBe(true)
    expect(items).toHaveLength(5)
    expect(view(quiet, { search: new Set([quiet[2].id]) })).toHaveLength(1)
  })
  it('folds the same rows when no search is active', () => {
    expect(view(quiet).map((i) => i.type)).toEqual(['fold'])
  })
})

describe('visibleRows: Cost and Duration orders (TRN-14)', () => {
  const ag = agent('ag', { cost_usd: 5 })
  const kid = call('kid', { depth: 1, cost_usd: 99, sec: 3 })
  const rows: AnyRow[] = [
    call('c1', { cost_usd: 0.5, dur_ms: 1000 }),
    tool('t1', { dur_ms: 90_000, sec: 10 }),
    ag,
    call('c2', { cost_usd: 2, dur_ms: 500, sec: 20 }),
    gap(300, 400),
    call('c3', { cost_usd: null, dur_ms: 7000, sec: 40 }),
  ]
  const pages = new Map<string, AgentPage>([[ag.id, { rows: [kid] }]])
  const open = new Set([ag.id])

  it('uses main-thread rows only, even with an agent open', () => {
    expect(flatRows(view(rows, { order: 'cost', openAgents: open }, pages)).every((n) => n.depth === 0)).toBe(true)
  })
  it('sorts Cost descending with tools and gaps (and unpriced calls) last in list order', () => {
    expect(ids(flatRows(view(rows, { order: 'cost' })).map((n) => n.row))).toEqual(['engagement/ag', 'llm/c2', 'llm/c1', 'tool/t1', `gap-${at(300)}`, 'llm/c3'])
  })
  it('sorts Duration descending over calls, tools, agents and gaps', () => {
    expect(ids(flatRows(view(rows, { order: 'duration' })).map((n) => n.row))).toEqual([`gap-${at(300)}`, 'tool/t1', 'engagement/ag', 'llm/c3', 'llm/c1', 'llm/c2'])
  })
  it('sorts tools that wait on the user or sat silent last in Duration', () => {
    const wait = tool('w', { dur_ms: 3_000_000, user_wait: 'ask' })
    const silent = tool('s', { dur_ms: 2_000_000, max_silence_ms: 400_000, sec: 5 })
    const out = sortedByOrder(buildNodes([wait, silent, call('c', { dur_ms: 10 })]), 'duration')
    expect(ids(out.map((n) => n.row))).toEqual(['llm/c', 'tool/w', 'tool/s'])
  })
  it('uses the in-range cost of an agent in a zoom', () => {
    const ra = new Map([[ag.id, { cost_usd: 0.1, tok_total: 1, n_calls_in_range: 1, n_calls: 3 }]])
    const out = sortedByOrder(buildNodes([ag, call('c2', { cost_usd: 2 })]), 'cost', ra)
    expect(ids(out.map((n) => n.row))).toEqual(['llm/c2', 'engagement/ag'])
  })
  it('shows the first 15, then a fold with the hidden cost; show all lists the rest', () => {
    const many = Array.from({ length: 20 }, (_, i) => call(`m${i}`, { sec: i, cost_usd: 20 - i }))
    const items = view(many, { order: 'cost' })
    expect(items).toHaveLength(16)
    // hidden rows cost 5+4+3+2+1
    expect(items[15]).toEqual({ type: 'more', key: 'more-order', kind: 'order', n: 5, cost: 15 })
    expect(view(many, { order: 'cost', showAll: true })).toHaveLength(20)
  })
  it('has no quiet folds', () => {
    const quiet = [0, 1, 2, 3, 4].map((i) => tool(`q${i}`, { sec: i, dur_ms: 1000 }))
    expect(view(quiet, { order: 'cost' }).every((i) => i.type === 'row')).toBe(true)
  })
})

describe('moving the cursor when its row is gone', () => {
  it('keeps the cursor if its row is still listed, else the first row of the new list', () => {
    const rows = [call('a', { cost_usd: 1 }), call('b', { cost_usd: 9, sec: 5 })]
    const asRan = view(rows)
    const byCost = view(rows, { order: 'cost' })
    expect(settleCursor(byCost, rows[0].id)).toBe(rows[0].id)
    const zoomed = view(rows, { zoom: { start_ms: at(4), end_ms: at(10) } })
    expect(settleCursor(zoomed, rows[0].id)).toBe(rows[1].id)
    expect(settleCursor(asRan, null)).toBe(rows[0].id)
    expect(settleCursor([], rows[0].id)).toBeNull()
  })
})

describe('quiet folds (TRN-37)', () => {
  const q = (i: number, o: Partial<Parameters<typeof tool>[1]> = {}) => tool(`q${i}`, { sec: i, dur_ms: 1000, ...o })
  const run = (n: number) => Array.from({ length: n }, (_, i) => q(i))

  it('folds a run of 4 or more', () => {
    const items = view(run(4))
    expect(items).toHaveLength(1)
    expect(items[0]).toMatchObject({ type: 'fold', key: foldKey(run(4)[0].id), tools: 4, calls: 0 })
  })
  it('does not fold a run of 3', () => {
    expect(view(run(3)).map((i) => i.type)).toEqual(['row', 'row', 'row'])
  })
  it('leaves a run open when it holds the cursor, or when the user unfolded it', () => {
    const rows = run(5)
    expect(view(rows, { cursor: rows[2].id }).every((i) => i.type === 'row')).toBe(true)
    expect(view(rows, { unfolded: new Set([foldKey(rows[0].id)]) }).every((i) => i.type === 'row')).toBe(true)
  })
  it('breaks a run at an agent row or a gap row', () => {
    const rows = [q(0), q(1), q(2), agent('ag', { sec: 3 }), q(4), q(5), q(6), gap(7, 200), q(8)]
    expect(view(rows).map((i) => i.type)).toEqual(['row', 'row', 'row', 'row', 'row', 'row', 'row', 'row', 'row'])
    const more = [...run(4), agent('ag', { sec: 9 }), ...[10, 11, 12, 13].map((i) => q(i))]
    expect(view(more).map((i) => i.type)).toEqual(['fold', 'row', 'fold'])
  })
  it('does not treat a skill load, a failed tool, a tool of a minute or more as quiet', () => {
    const rows = [q(0), q(1, { skill_load: 'writing-specs' }), q(2), q(3), q(4), q(5), q(6, { failed: true, exit_code: 1 }), q(7), q(8, { dur_ms: 60_000 }), q(9)]
    expect(view(rows).map((i) => i.type)).toEqual(['row', 'row', 'fold', 'row', 'row', 'row', 'row'])
  })
  it('treats a call as quiet only under 2 % of the turn’s cost and under a minute', () => {
    const calls = [0, 1, 2, 3].map((i) => call(`c${i}`, { sec: i, cost_usd: 0.01 }))
    expect(view(calls, { turnCost: 1 }).map((i) => i.type)).toEqual(['fold'])
    expect(view(calls, { turnCost: 0.5 }).map((i) => i.type)).toEqual(['row', 'row', 'row', 'row']) // 0.01 is 2 % of 0.5
    expect(view([...calls.slice(0, 3), call('slow', { sec: 9, cost_usd: 0.001, dur_ms: 60_000 })], { turnCost: 1 }).map((i) => i.type)).toEqual(['row', 'row', 'row', 'row'])
  })
  it('does not fold rows inside an agent band', () => {
    const ag = agent('ag')
    const kids = [0, 1, 2, 3].map((i) => tool(`k${i}`, { depth: 1, sec: i + 1, dur_ms: 1000 }))
    const items = view([ag], { openAgents: new Set([ag.id]) }, new Map([[ag.id, { rows: kids }]]))
    expect(items.every((i) => i.type === 'row')).toBe(true)
  })
  it('sums the run: calls, tools, cost, and the first and last start', () => {
    const rows = [q(0), call('c', { sec: 1, cost_usd: 0.004 }), q(2), q(3)]
    const fold = view(rows, { turnCost: 10 })[0]
    expect(fold).toMatchObject({ type: 'fold', calls: 1, tools: 3, cost: 0.004, firstMs: at(0), lastMs: at(3) })
  })
  it('finds the fold that holds a row', () => {
    const rows = run(4)
    expect(foldHolding(view(rows), rows[2].id)).toBe(foldKey(rows[0].id))
    expect(foldHolding(view(rows), 'nope')).toBeNull()
  })
})

describe('positionOf (TRN-12)', () => {
  it('counts the rows of the current list, the rows inside folds included', () => {
    const rows = [call('a', { cost_usd: 5 }), ...[1, 2, 3, 4].map((i) => tool(`q${i}`, { sec: i, dur_ms: 1000 })), call('z', { sec: 50, cost_usd: 5 })]
    const items = view(rows, { turnCost: 10 })
    expect(items.map((i) => i.type)).toEqual(['row', 'fold', 'row'])
    expect(positionOf(rows[0].id, items)).toEqual({ r: 1, m: 6 })
    expect(positionOf(rows[3].id, items)).toEqual({ r: 4, m: 6 })
    expect(positionOf(rows[5].id, items)).toEqual({ r: 6, m: 6 })
  })
  it('counts the list in its current order and says 0 for a row that is not in it', () => {
    const rows = [call('a', { cost_usd: 1 }), call('b', { cost_usd: 9, sec: 5 })]
    const items = view(rows, { order: 'cost' })
    expect(positionOf(rows[1].id, items)).toEqual({ r: 1, m: 2 })
    expect(positionOf(rows[0].id, items)).toEqual({ r: 2, m: 2 })
    expect(positionOf('x', items)).toEqual({ r: 0, m: 2 })
    expect(positionOf(null, items)).toEqual({ r: 0, m: 2 })
  })
})

describe('moveInList (TRN-50, TRN-51)', () => {
  const nodes = buildNodes(Array.from({ length: 25 }, (_, i) => call(`r${i}`, { sec: i })))
  const id = (i: number) => nodes[i].id
  it('steps one row and reports the edge past either end', () => {
    expect(moveInList(nodes, id(3), 1)).toEqual({ kind: 'to', id: id(4) })
    expect(moveInList(nodes, id(24), 1)).toEqual({ kind: 'edge', dir: 1 })
    expect(moveInList(nodes, id(0), -1)).toEqual({ kind: 'edge', dir: -1 })
  })
  it('J and K move 10 and stop at the turn’s edge', () => {
    expect(moveInList(nodes, id(3), 10)).toEqual({ kind: 'to', id: id(13) })
    expect(moveInList(nodes, id(20), 10)).toEqual({ kind: 'to', id: id(24) })
    expect(moveInList(nodes, id(24), 10)).toEqual({ kind: 'none' })
    expect(moveInList(nodes, id(4), -10)).toEqual({ kind: 'to', id: id(0) })
  })
  it('lands on the first row (or the last going back) when the cursor is not in the list', () => {
    expect(moveInList(nodes, null, 1)).toEqual({ kind: 'to', id: id(0) })
    expect(moveInList(nodes, 'x', -1)).toEqual({ kind: 'to', id: id(24) })
    expect(moveInList([], null, 1)).toEqual({ kind: 'none' })
  })
})
