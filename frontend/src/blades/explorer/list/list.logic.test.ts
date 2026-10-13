import { describe, expect, it } from 'vitest'
import type { SessionGroup, SessionRow } from '../../../api/types'
import { DEFAULT_URL_STATE } from '../../../state/url'
import {
  allTimeMatchText, compareRows, costWeight, displayGroups, flagsFor, groupFold, groupOpen, groupTitle, listColumns, listHeads,
  moveCursor, nextSort, noResultKind, openIndex, pageConsistent, PAGE, railPosition, shouldFade, SMALL_KEY, swapSessionPatch, wantedPages,
} from './list.logic'

const KOL = 'Asia/Kolkata'
const T0 = Date.UTC(2026, 9, 12, 4, 0) // Mon Oct 12 09:30 IST

function row(o: Partial<SessionRow> & { session: string }): SessionRow {
  return {
    project: '/p/a', project_name: 'a', color_slot: 1, cost_usd: 1, cost_lower_bound: false, n_unpriced: 0,
    session_id: o.session, provider: 'claude_code', name: o.session, title_source: 'first_ask', start_ms: T0, end_ms: T0 + 3600_000,
    ends_after_days: 0, tok_total: 1000, wall_ms: 3600_000, active_ms: 1800_000, is_automated: false, n_agents: 0, git_branch: null,
    activity: { bin_ms: 3600_000, tok: [1] }, day_split: [], ...o,
  }
}

function group(key: string, o: Partial<SessionGroup> & { rows?: SessionRow[] } = {}): SessionGroup {
  const rows = o.rows ?? [row({ session: `${key}-1`, project: key, project_name: key, cost_usd: o.cost_usd ?? 1 })]
  return {
    key, name: key, color_slot: 2, n_sessions: rows.length, cost_usd: rows.reduce((s, r) => s + (r.cost_usd ?? 0), 0), cost_lower_bound: false,
    n_unpriced: 0, tok_total: 100, wall_ms: 1000, active_ms: 500, period_activity: { bin_ms: 3600_000, start_ms: T0, tok: [1, 2, 3] },
    ...o, rows,
  }
}

describe('columns (EXP-49, EXP-56)', () => {
  it('flat: When 88, Project 136, Session 1fr, Flags 128, Activity 200, Cost 184, Wall 64, Active 64, Tokens 56', () => {
    expect(listColumns(null)).toBe('88px 136px minmax(0,1fr) 128px 200px 184px 64px 64px 56px')
  })
  it('grouped by project the Project column is gone, in the grid and in the heads', () => {
    expect(listColumns('project')).toBe('88px minmax(0,1fr) 128px 200px 184px 64px 64px 56px')
    expect(listHeads('project').map((h) => h.id)).toEqual(['start', 'name', 'flags', 'activity', 'cost', 'wall', 'active', 'tokens'])
  })
  it('grouped by day keeps the Project column', () => {
    expect(listColumns('day')).toBe(listColumns(null))
    expect(listHeads('day').map((h) => h.id)).toContain('project')
  })
  it('the grid has one column per head', () => {
    for (const g of [null, 'project', 'day'] as const) expect(listColumns(g).split(' ').length).toBe(listHeads(g).length)
  })
  it('only Started, Cost, Wall, Active and Tokens sort', () => {
    expect(listHeads(null).filter((h) => h.sort).map((h) => h.sort)).toEqual(['start', 'cost', 'wall', 'active', 'tokens'])
  })
})

describe('sort (EXP-51)', () => {
  it('clicking Cost on the default list sorts by cost, highest first; clicking again lowest first', () => {
    const a = nextSort({ sort: 'start', dir: 'desc' }, 'cost')
    expect(a).toEqual({ sort: 'cost', dir: 'desc' })
    expect(nextSort(a, 'cost')).toEqual({ sort: 'cost', dir: 'asc' })
    expect(nextSort({ sort: 'cost', dir: 'asc' }, 'cost')).toEqual({ sort: 'cost', dir: 'desc' })
  })
  it('clicking another head sorts by it, descending, even after an ascending sort', () => {
    expect(nextSort({ sort: 'cost', dir: 'asc' }, 'wall')).toEqual({ sort: 'wall', dir: 'desc' })
  })
  it('ties break by start time, newest first, in both directions', () => {
    const old = row({ session: 'old', cost_usd: 5, start_ms: T0 })
    const fresh = row({ session: 'fresh', cost_usd: 5, start_ms: T0 + 1000 })
    const low = row({ session: 'low', cost_usd: 1, start_ms: T0 + 2000 })
    const desc = [old, low, fresh].sort(compareRows('cost', 'desc')).map((r) => r.session)
    const asc = [old, low, fresh].sort(compareRows('cost', 'asc')).map((r) => r.session)
    expect(desc).toEqual(['fresh', 'old', 'low'])
    expect(asc).toEqual(['low', 'fresh', 'old'])
  })
  it('a null cost sorts as zero', () => {
    const none = row({ session: 'none', cost_usd: null })
    const some = row({ session: 'some', cost_usd: 0.01 })
    expect([none, some].sort(compareRows('cost', 'desc')).map((r) => r.session)).toEqual(['some', 'none'])
  })
})

describe('cost weight (EXP-49, D38 L7)', () => {
  it('600 at or above the 90th percentile, 500 below', () => {
    expect(costWeight(80, 80)).toBe(600)
    expect(costWeight(120, 80)).toBe(600)
    expect(costWeight(79.99, 80)).toBe(500)
  })
  it('500 with no percentile, no cost, or a zero cost under a zero percentile', () => {
    expect(costWeight(5, null)).toBe(500)
    expect(costWeight(null, 80)).toBe(500)
    expect(costWeight(0, 0)).toBe(500)
  })
})

describe('flags lane (EXP-52)', () => {
  it('a normal session shows its provider mark with the provider name', () => {
    expect(flagsFor(row({ session: 'a', provider: 'codex' }), KOL)).toEqual([{ kind: 'provider', provider: 'codex', tooltip: 'Codex' }])
  })
  it('an automated session shows repeat instead of the mark, with the provider in the tooltip', () => {
    expect(flagsFor(row({ session: 'a', is_automated: true }), KOL)).toEqual([{ kind: 'automated', tooltip: 'Automated · Claude Code' }])
  })
  it('bot shows only at 2 or more sub-agents', () => {
    expect(flagsFor(row({ session: 'a', n_agents: 1 }), KOL).map((f) => f.kind)).toEqual(['provider'])
    const two = flagsFor(row({ session: 'a', n_agents: 2 }), KOL)
    expect(two.map((f) => f.kind)).toEqual(['provider', 'agents'])
    expect(two[1]).toMatchObject({ n: 2, tooltip: '2 sub-agents' })
  })
  it('+Nd shows when the session ends after local midnights, with the end time in the store zone', () => {
    // 2026-10-11 18:00 UTC is Mon Oct 12 23:30 IST ... one day later 06:11 UTC is Tue 11:41 IST
    const end = Date.UTC(2026, 9, 13, 6, 11)
    const flags = flagsFor(row({ session: 'a', ends_after_days: 1, end_ms: end }), KOL)
    expect(flags.map((f) => f.kind)).toEqual(['provider', 'nights'])
    expect(flags[1]).toMatchObject({ n: 1, tooltip: 'Ends Tue 11:41' })
  })
  it('keeps the fixed order and hides each flag that does not apply', () => {
    const all = flagsFor(row({ session: 'a', n_agents: 6, ends_after_days: 2 }), KOL).map((f) => f.kind)
    expect(all).toEqual(['provider', 'agents', 'nights'])
    expect(flagsFor(row({ session: 'a', ends_after_days: 0 }), KOL).map((f) => f.kind)).toEqual(['provider'])
  })
})

describe('groups (EXP-56 to EXP-59)', () => {
  it('keeps the server order of ordinary groups (EXP-57: the server orders by the sort measure)', () => {
    const gs = [group('x', { cost_usd: 300, n_sessions: 3, rows: [row({ session: 'x1', cost_usd: 100 }), row({ session: 'x2', cost_usd: 100 }), row({ session: 'x3', cost_usd: 100 })] }),
      group('y', { cost_usd: 50, n_sessions: 2, rows: [row({ session: 'y1', cost_usd: 25 }), row({ session: 'y2', cost_usd: 25 })] })]
    expect(displayGroups(gs, 'project', 'cost', 'desc').map((g) => g.key)).toEqual(['x', 'y'])
    expect(displayGroups(gs, 'project', 'cost', 'asc').map((g) => g.key)).toEqual(['x', 'y'])
  })
  it('folds projects with exactly one session under $1.00 into "Small projects", last', () => {
    const big = group('big', { rows: [row({ session: 'b1', cost_usd: 40 }), row({ session: 'b2', cost_usd: 2 })] })
    const tiny1 = group('tiny1', { rows: [row({ session: 't1', cost_usd: 0.4, start_ms: T0 })] })
    const exact = group('exact', { rows: [row({ session: 'e1', cost_usd: 1, start_ms: T0 })] })
    const twoSmall = group('two', { rows: [row({ session: 'w1', cost_usd: 0.1 }), row({ session: 'w2', cost_usd: 0.1 })] })
    const tiny2 = group('tiny2', { rows: [row({ session: 't2', cost_usd: 0.9, start_ms: T0 + 5000 })] })
    const out = displayGroups([tiny1, big, exact, twoSmall, tiny2], 'project', 'start', 'desc')
    expect(out.map((g) => g.key)).toEqual(['big', 'exact', 'two', SMALL_KEY])
    const small = out[3]
    expect(small).toMatchObject({ name: 'Small projects', small: true, n_sessions: 2 })
    expect(small.cost_usd).toBeCloseTo(1.3)
    expect(small.rows.map((r) => r.session)).toEqual(['t2', 't1']) // newest first under the sort
    expect(out.slice(0, 3).every((g) => !g.small)).toBe(true)
  })
  it('a one-session project with no cost is small (null is zero)', () => {
    const none = group('none', { cost_usd: null, rows: [row({ session: 'n1', cost_usd: null })] })
    expect(displayGroups([none], 'project', 'start', 'desc').map((g) => g.key)).toEqual([SMALL_KEY])
  })
  it('no small project: no Small projects group', () => {
    expect(displayGroups([group('big', { rows: [row({ session: 'b1', cost_usd: 9 })] })], 'project', 'start', 'desc').some((g) => g.small)).toBe(false)
  })
  it('sums the merged group and its activity axis', () => {
    const a = group('a', { rows: [row({ session: 'a1', cost_usd: 0.5 })], tok_total: 10, wall_ms: 100, active_ms: 50, n_unpriced: 1, cost_lower_bound: true })
    const b = group('b', { rows: [row({ session: 'b1', cost_usd: 0.25 })], tok_total: 5, wall_ms: 200, active_ms: 70 })
    const [s] = displayGroups([a, b], 'project', 'start', 'desc')
    expect(s).toMatchObject({ tok_total: 15, wall_ms: 300, active_ms: 120, n_unpriced: 1, cost_lower_bound: true })
    expect(s.period_activity.tok).toEqual([2, 4, 6])
  })
  it('refuses groups on different period axes', () => {
    const a = group('a', { cost_usd: 0.5 })
    const b = group('b', { cost_usd: 0.5, period_activity: { bin_ms: 3600_000, start_ms: T0 + 1, tok: [1, 2, 3] } })
    expect(() => displayGroups([a, b], 'project', 'start', 'desc')).toThrow(/period axis/)
  })
  it('day groups are never merged', () => {
    const d = group('2026-10-12', { rows: [row({ session: 'd1', cost_usd: 0.1 })] })
    expect(displayGroups([d], 'day', 'start', 'desc').map((g) => g.small)).toEqual([false])
  })
  it('a day group is named by its local day, a project group by its name', () => {
    expect(groupTitle({ key: '2026-10-12', name: 'ignored' }, 'day')).toBe('Mon Oct 12')
    expect(groupTitle({ key: '/p/bizdemo', name: 'bizdemo' }, 'project')).toBe('bizdemo')
  })
  it('groups start expanded except "Small projects"; a click flips either', () => {
    const none = new Set<string>()
    expect(groupOpen({ key: 'a', small: false }, none)).toBe(true)
    expect(groupOpen({ key: SMALL_KEY, small: true }, none)).toBe(false)
    const flipped = new Set(['a', SMALL_KEY])
    expect(groupOpen({ key: 'a', small: false }, flipped)).toBe(false)
    expect(groupOpen({ key: SMALL_KEY, small: true }, flipped)).toBe(true)
  })
})

describe('fold (SYS-38, EXP-58)', () => {
  const costs = [50, 40, 30, 20, 10, 5, 4, 3, 2, 1, 0.5, 0.25]
  const rows = costs.map((c, i) => row({ session: `r${i}`, cost_usd: c }))
  const g = { n_sessions: 12, cost_usd: costs.reduce((a, b) => a + b, 0), rows: rows.slice(0, 5) }
  it('a group of 12 shows 5 rows and "Show 7 more" with the sum of rows 6 to 12', () => {
    const f = groupFold(g, false)
    expect(f.hidden).toBe(7)
    expect(f.label).toBe('Show 7 more')
    expect(f.sum).toBeCloseTo(costs.slice(5).reduce((a, b) => a + b, 0))
  })
  it('once expanded it reads "Show top 5" and drops the sum', () => {
    expect(groupFold(g, true)).toEqual({ hidden: 7, label: 'Show top 5', sum: null })
  })
  it('a group of 5 or fewer has no fold', () => {
    expect(groupFold({ n_sessions: 5, cost_usd: 1, rows: rows.slice(0, 5) }, false).label).toBe('')
  })
  it('a group with no cost has a label and no sum', () => {
    expect(groupFold({ n_sessions: 8, cost_usd: null, rows: rows.slice(0, 5) }, false)).toEqual({ hidden: 3, label: 'Show 3 more', sum: null })
  })
})

describe('paging (API-12)', () => {
  it('names the pages under the window and the cursor target', () => {
    expect(wantedPages(0, 20, 600)).toEqual([0])
    expect(wantedPages(190, 215, 600)).toEqual([0, 1])
    expect(wantedPages(0, 20, 600, [450])).toEqual([0, 2])
  })
  it('clamps to the total and ignores an empty window', () => {
    expect(wantedPages(590, 700, 600)).toEqual([2])
    expect(wantedPages(0, 0, 600)).toEqual([])
    expect(wantedPages(0, 10, 0)).toEqual([])
    expect(PAGE).toBe(200)
  })
  it('a page with another total or another last_ingest_ms than page 0 is inconsistent', () => {
    const p0 = { total: 214, freshness: { last_ingest_ms: 5 } }
    expect(pageConsistent(p0, { total: 214, freshness: { last_ingest_ms: 5 } })).toBe(true)
    expect(pageConsistent(p0, { total: 215, freshness: { last_ingest_ms: 5 } })).toBe(false)
    expect(pageConsistent(p0, { total: 214, freshness: { last_ingest_ms: 6 } })).toBe(false)
  })
})

describe('cursor (EXP-54, EXP-63 to EXP-65)', () => {
  it('j or k from no selection selects row 1', () => {
    expect(moveCursor(null, 1, 10)).toBe(0)
    expect(moveCursor(null, -1, 10)).toBe(0)
  })
  it('steps one row and stops at the ends', () => {
    expect(moveCursor(0, 1, 10)).toBe(1)
    expect(moveCursor(9, 1, 10)).toBe(9)
    expect(moveCursor(0, -1, 10)).toBe(0)
  })
  it('an empty list has no cursor', () => {
    expect(moveCursor(null, 1, 0)).toBeNull()
  })
  it('pressing j twice from no selection selects the second row (EXP-54 scenario)', () => {
    expect(moveCursor(moveCursor(null, 1, 5), 1, 5)).toBe(1)
  })
  it('EXP-63: with session 3 of 214 open, j opens session 4 and the header reads 4 / 214', () => {
    const rows = Array.from({ length: 214 }, (_, i) => row({ session: `s${i + 1}` }))
    const at = (i: number) => rows[i] ?? null
    const here = openIndex('s3', at, 214)
    expect(railPosition(here, 214)).toBe('3 / 214')
    const next = moveCursor(here, 1, 214)!
    expect(rows[next].session).toBe('s4')
    expect(railPosition(next, 214)).toBe('4 / 214')
  })
  it('EXP-65: an open session that left the scope has no row, no position, and j starts at row 1', () => {
    const rows = [row({ session: 'a' }), row({ session: 'b' })]
    const here = openIndex('gone', (i) => rows[i] ?? null, 2)
    expect(here).toBeNull()
    expect(railPosition(here, 2)).toBe('– / 2')
    expect(moveCursor(here, 1, 2)).toBe(0)
    expect(openIndex(null, (i) => rows[i] ?? null, 2)).toBeNull()
  })
  it('an unloaded row (null) is skipped when looking for the open session', () => {
    expect(openIndex('b', (i) => (i === 1 ? row({ session: 'b' }) : null), 3)).toBe(1)
  })
  it('swapping the open session sets s and returns the blade parameters to their defaults', () => {
    const p = swapSessionPatch('claude_code/x/session/-') as Record<string, unknown>
    expect(p.s).toBe('claude_code/x/session/-')
    for (const k of ['find', 'range', 'over', 'sview', 'sreal', 'info', 't', 'step', 'view', 'order', 'real', 'zoom', 'thist', 'tinfo', 'detail']) {
      expect(p[k]).toEqual((DEFAULT_URL_STATE as unknown as Record<string, unknown>)[k])
    }
    // the scope and the measure the blades share are not touched
    for (const k of ['period', 'q', 'sort', 'group', 'bars']) expect(k in p).toBe(false)
  })
})

describe('no-result state (EXP-79, EXP-80)', () => {
  const none = { q: '', providers: [], models: [], branches: [], projects: [] }
  it('search text or any filter makes it a filtered no-result; none is an empty period', () => {
    expect(noResultKind(none)).toBe('empty-period')
    expect(noResultKind({ ...none, q: 'rbac' })).toBe('filtered')
    expect(noResultKind({ ...none, models: ['x'] })).toBe('filtered')
    expect(noResultKind({ ...none, projects: ['/p'] })).toBe('filtered')
  })
  it('words the all-time count', () => {
    expect(allTimeMatchText('rbacxyz', 3)).toBe('"rbacxyz" matches 3 sessions in all time')
    expect(allTimeMatchText('rbacxyz', 1)).toBe('"rbacxyz" matches 1 session in all time')
    expect(allTimeMatchText('rbacxyz', 0)).toBe('"rbacxyz" matches 0 sessions in all time')
  })
})

describe('arrival fade (EXP-78)', () => {
  it('fades only when stale data is replaced, never on first load, never under reduced motion', () => {
    expect(shouldFade(true, false, false)).toBe(true)
    expect(shouldFade(false, false, false)).toBe(false)
    expect(shouldFade(true, true, false)).toBe(false)
    expect(shouldFade(true, false, true)).toBe(false)
  })
})
