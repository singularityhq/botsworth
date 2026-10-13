import { describe, expect, it } from 'vitest'
import type { SessionRow } from '../../../api/types'
import { displayGroups, SMALL_KEY, type DisplayGroup } from './list.logic'
import { flatLayout, groupedItems, groupedLayout, LIST_METRICS, RAIL_METRICS, visibleRange } from './virtual.logic'

const T0 = Date.UTC(2026, 9, 12, 4, 0)
const row = (session: string, cost = 10): SessionRow => ({
  project: '/p', project_name: 'p', color_slot: 1, cost_usd: cost, cost_lower_bound: false, n_unpriced: 0, session, session_id: session,
  provider: 'claude_code', name: session, title_source: 'first_ask', start_ms: T0, end_ms: T0 + 1000, ends_after_days: 0, tok_total: 1,
  wall_ms: 1000, active_ms: 500, is_automated: false, n_agents: 0, git_branch: null, activity: { bin_ms: 1, tok: [1] }, day_split: [],
})
const mkGroup = (key: string, n: number, shown = 5): DisplayGroup => {
  const rows = Array.from({ length: Math.min(n, shown) }, (_, i) => row(`${key}${i + 1}`))
  return {
    key, name: key, color_slot: 1, small: false, n_sessions: n, cost_usd: n * 10, cost_lower_bound: false, n_unpriced: 0, tok_total: 1,
    wall_ms: 1, active_ms: 1, period_activity: { bin_ms: 1, start_ms: T0, tok: [1] }, rows,
  }
}
const none = new Set<string>()

describe('flat layout', () => {
  const rows = Array.from({ length: 500 }, (_, i) => row(`s${i}`))
  const L = flatLayout(500, (i) => rows[i] ?? null, LIST_METRICS)
  it('is 44 px per row and draws only what is in view', () => {
    expect(L.height).toBe(500 * 44)
    expect(L.offset(10)).toBe(440)
    expect(visibleRange(L, 0, 900, 0)).toEqual([0, 21])
    expect(visibleRange(L, 4400, 5300, 3)).toEqual([97, 124])
  })
  it('clamps at both ends', () => {
    expect(visibleRange(L, 21_000, 21_900, 5)).toEqual([472, 500])
    expect(visibleRange(L, -900, 100, 0)).toEqual([0, 3])
  })
  it('draws nothing when the list is out of view or empty', () => {
    expect(visibleRange(L, -2000, -10, 5)).toEqual([0, 0])
    expect(visibleRange(L, 30_000, 31_000, 5)).toEqual([0, 0])
    expect(visibleRange(flatLayout(0, () => null, LIST_METRICS), 0, 900, 5)).toEqual([0, 0])
  })
  it('numbers rows by position and gives the row above for the weekday rule', () => {
    const it = L.item(3)
    expect(it).toMatchObject({ kind: 'row', nav: 3, body: 'none' })
    expect(it.kind === 'row' && it.prev?.session).toBe('s2')
    expect(L.itemOfNav(7)).toBe(7)
    expect(L.itemOfNav(500)).toBe(-1)
  })
  it('an unloaded row is a skeleton item', () => {
    const sparse = flatLayout(300, (i) => (i < 200 ? rows[i] : null), LIST_METRICS)
    expect(sparse.item(250)).toMatchObject({ kind: 'row', row: null })
  })
  it('the rail row is 61 px', () => {
    expect(flatLayout(10, () => null, RAIL_METRICS).height).toBe(610)
  })
})

describe('grouped items (EXP-56 to EXP-59)', () => {
  const input = (g: DisplayGroup[], o: Partial<Parameters<typeof groupedItems>[0]> = {}) =>
    groupedItems({ groups: g, toggled: none, expanded: none, groupRows: () => undefined, ...o })
  it('a header, the top 5 rows and a fold row for a group of 12', () => {
    const { items, navRows } = input([mkGroup('a', 12)])
    expect(items.map((i) => i.kind)).toEqual(['group', 'row', 'row', 'row', 'row', 'row', 'fold'])
    expect(navRows).toHaveLength(5)
    const fold = items[6]
    expect(fold.kind === 'fold' && fold.fold.label).toBe('Show 7 more')
  })
  it('the last item of a body closes it; the others are mid', () => {
    const { items } = input([mkGroup('a', 12)])
    expect(items.map((i) => (i.kind === 'group' ? 'head' : i.body))).toEqual(['head', 'mid', 'mid', 'mid', 'mid', 'mid', 'last'])
    const small = input([mkGroup('b', 3)]).items
    expect(small[small.length - 1]).toMatchObject({ kind: 'row', body: 'last' })
  })
  it('a collapsed group is its header alone', () => {
    const { items, navRows } = input([mkGroup('a', 12)], { toggled: new Set(['a']) })
    expect(items.map((i) => i.kind)).toEqual(['group'])
    expect(navRows).toHaveLength(0)
  })
  it('an expanded group shows skeleton rows while its rows load, then all of them', () => {
    const g = mkGroup('a', 12)
    const loading = input([g], { expanded: new Set(['a']) })
    expect(loading.items.filter((i) => i.kind === 'row' && i.row === null)).toHaveLength(5)
    expect(loading.navRows).toHaveLength(5)
    const all = Array.from({ length: 12 }, (_, i) => row(`a${i + 1}`))
    const done = input([g], { expanded: new Set(['a']), groupRows: () => ({ rows: all }) })
    expect(done.navRows).toHaveLength(12)
    const fold = done.items[done.items.length - 1]
    expect(fold.kind === 'fold' && fold.fold.label).toBe('Show top 5')
  })
  it('a failed fetch leaves the top rows and one error row', () => {
    const { items } = input([mkGroup('a', 12)], { expanded: new Set(['a']), groupRows: () => ({ error: true }) })
    expect(items.map((i) => i.kind)).toContain('error')
  })
  it('"Small projects" starts collapsed and holds all its rows without a fetch', () => {
    const smalls = displayGroups(
      Array.from({ length: 7 }, (_, i) => ({ ...mkGroup(`t${i}`, 1), cost_usd: 0.1, rows: [{ ...row(`t${i}`, 0.1), start_ms: T0 + i }] })),
      'project', 'start', 'desc',
    )
    expect(smalls[0].key).toBe(SMALL_KEY)
    expect(input(smalls).items.map((i) => i.kind)).toEqual(['group'])
    const open = input(smalls, { toggled: new Set([SMALL_KEY]) })
    expect(open.navRows).toHaveLength(5)
    const all = input(smalls, { toggled: new Set([SMALL_KEY]), expanded: new Set([SMALL_KEY]) })
    expect(all.navRows).toHaveLength(7)
    expect(all.items.some((i) => i.kind === 'row' && i.row === null)).toBe(false)
  })
  it('numbers rows in reading order across groups; the first row of each group has no row above', () => {
    const { items, navRows } = input([mkGroup('a', 2), mkGroup('b', 2)])
    expect(navRows.map((r) => r.session)).toEqual(['a1', 'a2', 'b1', 'b2'])
    const rows = items.filter((i) => i.kind === 'row')
    expect(rows.map((r) => r.kind === 'row' && r.nav)).toEqual([0, 1, 2, 3])
    expect(rows[2].kind === 'row' && rows[2].prev).toBeNull()
    expect(rows[1].kind === 'row' && rows[1].prev?.session).toBe('a1')
  })
})

describe('grouped layout', () => {
  const { items } = groupedItems({ groups: [mkGroup('a', 12), mkGroup('b', 3), mkGroup('c', 2)], toggled: none, expanded: none, groupRows: () => undefined })
  const L = groupedLayout(items, LIST_METRICS)
  // a: 52 + 5*44 + 36 = 308; b: 52 + 3*44 = 184; c: 52 + 2*44 = 140
  it('adds the band, row and fold heights', () => {
    expect(L.height).toBe(308 + 184 + 140)
    expect(L.offset(1)).toBe(52)
    expect(L.offset(items.findIndex((i) => i.kind === 'group' && i.group.key === 'b'))).toBe(308)
  })
  it('finds the item under a y by binary search', () => {
    expect(L.indexAt(0)).toBe(0)
    expect(L.indexAt(51)).toBe(0)
    expect(L.indexAt(52)).toBe(1)
    expect(L.indexAt(307)).toBe(6) // the fold row
    expect(L.indexAt(308)).toBe(7) // header of b
    expect(L.indexAt(99_999)).toBe(items.length - 1)
  })
  it('maps a cursor row to its item', () => {
    expect(L.itemOfNav(0)).toBe(1)
    expect(L.itemOfNav(5)).toBe(8) // first row of b, after a's fold
    expect(L.itemOfNav(99)).toBe(-1)
  })
  it('holds the header of the group under the line', () => {
    expect(L.pin(0)).toBeNull() // nothing has scrolled past
    expect(L.pin(52)).toEqual({ index: 0, top: 52 })
    expect(L.pin(200)).toEqual({ index: 0, top: 200 })
  })
  it('the next header pushes the held one up as it arrives', () => {
    // b starts at 308; a's header is 52 tall, so it starts to leave at line 256
    expect(L.pin(255)).toEqual({ index: 0, top: 255 })
    expect(L.pin(290)).toEqual({ index: 0, top: 256 })
    expect(L.pin(307)).toEqual({ index: 0, top: 256 })
    // at b's top edge b sits in its own place and nothing is held; past it, b is the held group
    expect(L.pin(308)).toBeNull()
    expect(L.pin(309)?.index).toBe(L.indexAt(308))
  })
  it('drops the hold after the last group ends', () => {
    expect(L.pin(L.height)).toBeNull()
    expect(L.pin(L.height - 1)?.index).toBe(L.indexAt(492))
  })
  it('the rail band is 40 px', () => {
    expect(groupedLayout(items, RAIL_METRICS).offset(1)).toBe(40)
  })
})
