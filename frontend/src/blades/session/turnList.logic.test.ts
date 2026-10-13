import { describe, expect, it } from 'vitest'
import { fixtures } from '../../api/__fixtures__'
import type { TurnRow } from '../../api/types'
import { gap, turn } from './testRows'
import {
  buildEntries, cursorStops, displayIx, foldCost, foldLabels, foldTitle, gapBarPct, initialCursor, kindsVary, listColumns, listHead,
  maxGap, maxMeasure, measureValue, moveCursor, orderItems, revealInBox, scrollTarget, tokensText, turnFlags, waitingShown, withinOneDay,
} from './turnList.logic'

const types = (items: ReturnType<typeof buildEntries>) => items.map((e) => (e.type === 'turn' ? `T${e.ix}` : e.type === 'gap' ? `gap:${e.gap.label}` : `fold:${e.rows.length}`))

describe('displayIx', () => {
  it('reads the number from T<n>', () => {
    expect(displayIx('T12')).toBe(12)
    expect(displayIx(null)).toBeNull()
    expect(displayIx('x')).toBeNull()
  })
})

describe('buildEntries order (SES-36)', () => {
  it('lists turns, gaps and folds in time order whatever order the wire gives', () => {
    const a = turn(1)
    const g = gap(1_200_000, 300_000, { turn: a.turn, label: 'you' }) // ends at 1_500_000, where turn 2 starts
    const b = turn(2, { start_ms: 1_500_000, end_ms: 1_600_000 })
    expect(types(buildEntries([b, g, a]))).toEqual(['T1', 'gap:you', 'T2'])
  })

  it('a gap sits directly above the turn it ends at', () => {
    const a = turn(1, { start_ms: 0, end_ms: 100 })
    const b = turn(2, { start_ms: 600_000, end_ms: 700_000 })
    const g = gap(100_000, 500_000)
    expect(types(buildEntries([a, b, g]))).toEqual(['T1', 'gap:you', 'T2'])
  })

  it('a gap before a harness fold sits above the fold', () => {
    const a = turn(1, { start_ms: 0, end_ms: 100 })
    const h = turn(null, { start_ms: 600_000 })
    const g = gap(100_000, 500_000)
    expect(types(buildEntries([a, h, g]))).toEqual(['T1', 'gap:you', 'fold:1'])
  })

  it('turns with no start sort by their place in the wire order (SES-85)', () => {
    const a = turn(1, { start_ms: 100, end_ms: 200 })
    const h1 = turn(null)
    const b = turn(2, { start_ms: 500, end_ms: 600 })
    const h2 = turn(null)
    expect(orderItems([a, h1, b, h2])).toEqual([a, h1, b, h2])
    expect(orderItems([h1, a])).toEqual([h1, a])
  })
})

describe('harness folds (SES-35)', () => {
  it('consecutive harness turns fold into one row with their summed cost', () => {
    const h1 = turn(null, { label: '/clear', cost_usd: 0.004 })
    const h2 = turn(null, { label: 'Request interrupted', cost_usd: 0.007 })
    const entries = buildEntries([turn(1), h1, h2, turn(2)])
    expect(types(entries)).toEqual(['T1', 'fold:2', 'T2'])
    const fold = entries[1]
    if (fold.type !== 'fold') throw new Error('fold expected')
    expect(foldTitle(fold.rows)).toBe('2 session events')
    expect(foldLabels(fold.rows)).toBe('/clear, Request interrupted')
    expect(foldCost(fold.rows)).toBeCloseTo(0.011)
  })

  it('shows the fold cost only from $0.01 and singular for one event', () => {
    expect(foldCost([turn(null, { cost_usd: 0.009 })])).toBeNull()
    expect(foldCost([turn(null, { cost_usd: null })])).toBeNull()
    expect(foldCost([turn(null, { cost_usd: 0.01 })])).toBe(0.01)
    expect(foldTitle([turn(null)])).toBe('1 session event')
  })

  it('a gap between two harness turns splits the run', () => {
    const h1 = turn(null, { start_ms: 0 })
    const h2 = turn(null, { start_ms: 1_000_000 })
    expect(types(buildEntries([h1, gap(100_000, 900_000), h2]))).toEqual(['fold:1', 'gap:you', 'fold:1'])
  })
})

describe('gap rows (SES-36, SES-19)', () => {
  it('keeps none-state stretches of 2 min or more and drops agents-only and shorter ones', () => {
    const items = [
      turn(1, { start_ms: 0, end_ms: 100_000 }), gap(200_000, 120_000), gap(400_000, 119_999), gap(600_000, 300_000, { state: 'agents', label: 'agents' }), turn(2, { start_ms: 900_000, end_ms: 950_000 }),
    ]
    expect(types(buildEntries(items))).toEqual(['T1', 'gap:you', 'T2'])
  })

  it('resolves the turn a gap belongs to and builds its reason', () => {
    const t1 = turn(1)
    const g = gap(150_000, 200_000, { turn: t1.turn, label: 'before_first' })
    const entries = buildEntries([g, t1])
    const e = entries[0]
    if (e.type !== 'gap') throw new Error('gap expected')
    expect(e.turnIx).toBe(1)
    expect(e.state).toBe('none')
    expect(e.reason).toBe('the session was open before your first message')
  })

  it('"before its first message" when the first numbered turn is not a human turn', () => {
    const t1 = turn(1, { kind: 'message', origin: 'peer' })
    const e = buildEntries([gap(0, 200_000, { label: 'before_first' }), t1])[0]
    if (e.type !== 'gap') throw new Error('gap expected')
    expect(e.reason).toBe('the session was open before its first message')
  })

  it('bar length is proportional to the longest gap, at least 2 %', () => {
    expect(gapBarPct(300_000, 600_000)).toBe(50)
    expect(gapBarPct(1000, 600_000)).toBe(2)
    expect(gapBarPct(600_000, 600_000)).toBe(100)
    const entries = buildEntries([turn(1), gap(100_000, 200_000), gap(2_000_000, 700_000), turn(3)])
    expect(maxGap(entries)).toBe(700_000)
  })
})

describe('cursor (SES-38)', () => {
  const items = [turn(null), turn(1, { n_llm_calls: 0 }), gap(1_100_000, 200_000), turn(2), turn(3)]
  const entries = buildEntries(items)

  it('gap rows and harness rows are not cursor stops', () => {
    expect(cursorStops(entries).map((s) => s.ix)).toEqual([1, 2, 3])
  })

  it('starts on the first numbered turn that has rows', () => {
    expect(initialCursor(entries)).toBe(items[3].type === 'turn' ? items[3].turn : '')
  })

  it('starts on the first numbered turn when none has rows', () => {
    const e = buildEntries([turn(1, { n_llm_calls: 0 }), turn(2, { n_llm_calls: 0 })])
    expect(initialCursor(e)).toBe(cursorStops(e)[0].key)
    expect(initialCursor([])).toBeNull()
  })

  it('j and k move one turn and clamp at the ends', () => {
    const stops = cursorStops(entries)
    const [a, b, c] = stops.map((s) => s.key)
    expect(moveCursor(stops, a, 1)).toBe(b)
    expect(moveCursor(stops, c, 1)).toBe(c)
    expect(moveCursor(stops, a, -1)).toBe(a)
    expect(moveCursor(stops, b, -1)).toBe(a)
  })

  it('a cursor that is not in the list lands on the first stop (j) or the last (k)', () => {
    const stops = cursorStops(entries)
    expect(moveCursor(stops, 'gone', 1)).toBe(stops[0].key)
    expect(moveCursor(stops, null, -1)).toBe(stops[2].key)
    expect(moveCursor([], 'x', 1)).toBeNull()
  })
})

describe('scrollTarget (SES-38: the 30 % rule)', () => {
  it('does nothing while the row is fully visible', () => {
    expect(scrollTarget(100, 44, 0, 600)).toBeNull()
    expect(scrollTarget(556, 44, 0, 600)).toBeNull()
  })

  it('puts the row about 30 % from the top when it leaves the view', () => {
    expect(scrollTarget(1000, 44, 0, 600)).toBe(820)
    expect(scrollTarget(557, 44, 0, 600)).toBe(377) // 1 px below the fold
    expect(scrollTarget(100, 44, 400, 600)).toBe(0) // above the view, clamped at 0
  })
})

describe('kind glyph (SES-34 item 2)', () => {
  it('shows only when the kinds in the list vary', () => {
    expect(kindsVary(buildEntries([turn(1), turn(2)]))).toBe(false)
    expect(kindsVary(buildEntries([turn(1), turn(2, { origin: 'slash' })]))).toBe(true)
    expect(kindsVary(buildEntries([turn(1), turn(2, { kind: 'message', origin: 'peer' })]))).toBe(true)
  })

  it('does not count harness rows', () => {
    expect(kindsVary(buildEntries([turn(1), turn(null), turn(2)]))).toBe(false)
  })
})

describe('turnFlags (SES-34 item 4)', () => {
  const m = (model: string, cost_usd: number | null) => ({ model, cost_usd })

  it('orders in the fixed order: models, agents, skill loads, failures; hides zeros', () => {
    const t = turn(1, { models: [m('claude-opus-5-5', 3)], n_agents: 2, n_skill_loads: 1, n_failed: 4 })
    expect(turnFlags(t, null).map((f) => f.type)).toEqual(['model', 'agents', 'skills', 'failed'])
    expect(turnFlags(turn(1), null)).toEqual([])
  })

  it('shows at most 2 model marks, most costly first, then +N', () => {
    const t = turn(1, { models: [m('a', 1), m('b', 5), m('c', 3), m('d', null)] })
    expect(turnFlags(t, null)).toEqual([{ type: 'model', model: 'b' }, { type: 'model', model: 'c' }, { type: 'more', n: 2 }])
    expect(turnFlags(turn(1, { models: [m('a', 1), m('b', 5)] }), null)).toEqual([{ type: 'model', model: 'b' }, { type: 'model', model: 'a' }])
  })

  it('with a search, only the match count shows', () => {
    const t = turn(1, { models: [m('a', 1)], n_agents: 2 })
    expect(turnFlags(t, 3)).toEqual([{ type: 'search', n: 3 }])
  })
})

describe('row figures', () => {
  it('Waiting shows only from 30 s', () => {
    expect(waitingShown(29_999)).toBeNull()
    expect(waitingShown(30_000)).toBe(30_000)
    expect(waitingShown(null)).toBeNull()
    expect(waitingShown(59_999, 60_000)).toBeNull()
  })

  it('null tokens read as a dash', () => {
    expect(tokensText(null)).toBe('—')
    expect(tokensText(97_000)).toBe('97k')
    expect(tokensText(0)).toBe('0')
  })

  it('bars take cost, wall or tokens by the measure; a missing value is 0', () => {
    const t = turn(1, { cost_usd: 2.5, wall_ms: 60_000, tok_total: null })
    expect(measureValue(t, 'cost')).toBe(2.5)
    expect(measureValue(t, 'time')).toBe(60_000)
    expect(measureValue(t, 'tokens')).toBe(0)
    const entries = buildEntries([t, turn(2, { cost_usd: 7 }), turn(null, { cost_usd: 99 })])
    expect(maxMeasure(entries, 'cost')).toBe(7)
  })

  it('names the list head with the search text', () => {
    expect(listHead('')).toBe('Turns')
    expect(listHead('pytest')).toBe('Turns with “pytest”')
  })

  it('single-day sessions show no weekday', () => {
    const noon = Date.UTC(2026, 9, 12, 6, 30)
    expect(withinOneDay(noon, noon + 3600_000, 'Asia/Kolkata')).toBe(true)
    expect(withinOneDay(noon, noon + 24 * 3600_000, 'Asia/Kolkata')).toBe(false)
  })
})

describe('filters (SES-30, SES-32)', () => {
  const t1 = turn(1, { start_ms: 0, end_ms: 100_000 })
  const t2 = turn(2, { start_ms: 1_000_000, end_ms: 1_100_000 })
  const t3 = turn(3, { start_ms: 2_000_000, end_ms: 2_100_000 })
  const items = [t1, gap(100_000, 900_000), t2, turn(null, { start_ms: 1_100_001 }), t3]

  it('a search keeps only matching turns and drops gap and fold rows', () => {
    const e = buildEntries(items, { kept: new Set([t2.turn]), range: null })
    expect(types(e)).toEqual(['T2'])
  })

  it('a search that matches nothing leaves an empty list', () => {
    expect(buildEntries(items, { kept: new Set(), range: null })).toEqual([])
  })

  it('a range keeps turns whose span overlaps it, and the gaps and folds inside it', () => {
    const e = buildEntries(items, { kept: null, range: { start_ms: 900_000, end_ms: 1_200_000 } })
    expect(types(e)).toEqual(['gap:you', 'T2', 'fold:1'])
  })

  it('a turn with no start or end is out of a range', () => {
    const e = buildEntries([turn(1, { start_ms: null, end_ms: null })], { kept: null, range: { start_ms: 0, end_ms: 10 } })
    expect(e).toEqual([])
  })
})

describe('listColumns (SES-33)', () => {
  it('is the full grid at 1004 px', () => {
    const c = listColumns(1004)
    expect(c.template).toBe('56px 20px minmax(0,1fr) 112px 104px 72px 64px 64px 56px 16px')
    expect(c.ids).toHaveLength(10)
  })

  it('drops columns from the right end, never reorders, and keeps the name 160 px wide', () => {
    const c = listColumns(760)
    expect(c.ids.slice(0, 3)).toEqual(['when', 'kind', 'name'])
    expect(c.ids.includes('go')).toBe(false)
    expect(c.ids.includes('tokens')).toBe(false)
    expect(c.ids.includes('cost')).toBe(true)
  })

  it('at the 640 px minimum it keeps When, kind and name first', () => {
    const ids = listColumns(640).ids
    expect(ids.slice(0, 3)).toEqual(['when', 'kind', 'name'])
    expect(ids.length).toBeLessThan(10)
  })
})

describe('against the wire fixture', () => {
  it('builds a gap, a numbered turn and a fold from sessionTurns', () => {
    const items = fixtures.sessionTurns.items as unknown as (TurnRow | ReturnType<typeof gap>)[]
    const e = buildEntries(items)
    expect(types(e)).toEqual(['gap:limit', 'T2', 'fold:1'])
    const g = e[0]
    if (g.type !== 'gap') throw new Error('gap expected')
    expect(g.turnIx).toBe(2)
  })
})

describe('revealInBox (SES-4: the rail scrolls only its own list)', () => {
  const el = (top: number, bottom: number, scrollTop = 0) => ({ getBoundingClientRect: () => ({ top, bottom }), scrollTop }) as unknown as HTMLElement
  it('leaves the list alone when the row is fully inside', () => {
    const box = el(100, 500, 40)
    revealInBox(box, el(200, 256))
    expect(box.scrollTop).toBe(40)
  })
  it('scrolls up by the hidden part of a row above the list', () => {
    const box = el(100, 500, 300)
    revealInBox(box, el(60, 116))
    expect(box.scrollTop).toBe(260)
  })
  it('scrolls down by the hidden part of a row below the list', () => {
    const box = el(100, 500, 0)
    revealInBox(box, el(480, 536))
    expect(box.scrollTop).toBe(36)
  })
  it('never scrolls past the row top when the row is taller than the list', () => {
    const box = el(100, 200, 0)
    revealInBox(box, el(150, 400))
    expect(box.scrollTop).toBe(50)
  })
})
