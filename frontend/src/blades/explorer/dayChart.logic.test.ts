import { describe, expect, it } from 'vitest'
import type { ColorSlot, DayBar } from '../../api/types'
import { chartWindow, resolvePeriod, type Period } from '../../state/period'
import {
  barHeight, buildColumns, chartKey, columnLabel, columnName, columnsRange, footprint, footprintBox, isClick, peakIndex, slotOrder,
  tooltipModel, windowMax,
} from './dayChart.logic'

const TODAY = '2026-10-12' // Monday
const last7: Period = { kind: 'preset', preset: 'last7' }

function bar(day: string, parts: [string, ColorSlot | null, number][], over: Partial<DayBar> = {}): DayBar {
  const cost = parts.reduce((a, p) => a + p[2], 0)
  return {
    day, from_day: day, to_day: day, cost_usd: cost, cost_lower_bound: false, n_unpriced: 0, tok_total: cost * 1000, n_sessions: parts.length,
    by_project: parts.map(([name, slot, v]) => ({ project: `/p/${name}`, project_name: name, color_slot: slot, cost_usd: v, tok_total: v * 1000 })),
    ...over,
  }
}

const cols = (rows: DayBar[], period: Period = last7, measure: 'cost' | 'tokens' = 'cost') =>
  buildColumns(chartWindow(period, TODAY), rows, resolvePeriod(period, TODAY), measure)

describe('buildColumns (EXP-32 to EXP-35)', () => {
  it('shows 21 day columns ending today for Last 7 days, with the 7 period days inside the band', () => {
    const c = cols([])
    expect(c).toHaveLength(21)
    expect(c[0].from).toBe('2026-09-22')
    expect(c[20].from).toBe('2026-10-12')
    expect(c.filter((x) => x.inPeriod).map((x) => x.from)).toEqual([
      '2026-10-06', '2026-10-07', '2026-10-08', '2026-10-09', '2026-10-10', '2026-10-11', '2026-10-12',
    ])
  })
  it('puts an 8 px gap before each Monday and only before Mondays', () => {
    const c = cols([])
    expect(c.filter((x) => x.monday).map((x) => x.from)).toEqual(['2026-09-28', '2026-10-05', '2026-10-12'])
  })
  it('stacks segments by colour slot, slot 1 at the bottom and "other" on top, whatever order the server sent', () => {
    const c = cols([bar('2026-10-07', [['x', null, 5], ['b', 3, 20], ['a', 1, 100]])])
    const col = c.find((x) => x.from === '2026-10-07')!
    expect(col.segments.map((s) => s.slot)).toEqual([1, 3, null])
    expect(col.total).toBe(125)
  })
  it('merges projects that share the "other" slot into one segment', () => {
    const col = cols([bar('2026-10-07', [['x', null, 5], ['y', null, 7]])]).find((x) => x.from === '2026-10-07')!
    expect(col.segments).toEqual([{ slot: null, value: 12 }])
    expect(slotOrder(null)).toBeGreaterThan(slotOrder(6))
  })
  it('reads tokens instead of cost when the measure is Tokens', () => {
    const col = cols([bar('2026-10-07', [['a', 1, 2]])], last7, 'tokens').find((x) => x.from === '2026-10-07')!
    expect(col.total).toBe(2000)
  })
  it('keeps days outside the period visible but out of the band', () => {
    const c = cols([bar('2026-09-30', [['a', 1, 9]])])
    const out = c.find((x) => x.from === '2026-09-30')!
    expect(out.inPeriod).toBe(false)
    expect(out.total).toBe(9)
  })
  it('lists a period over 21 days as one column per day, and a longer one than 92 days as week columns clipped to the window', () => {
    const p60: Period = { kind: 'custom', from: '2026-08-01', to: '2026-09-29' }
    expect(cols([], p60)).toHaveLength(60)
    const p120: Period = { kind: 'custom', from: '2026-06-03', to: '2026-10-01' } // Wed to Thu
    const weeks = cols([], p120)
    expect(weeks[0].bucket).toBe('week')
    expect(weeks[0].from).toBe('2026-06-03')
    expect(weeks[0].to).toBe('2026-06-07') // clipped: Wed to Sun
    expect(weeks[1].from).toBe('2026-06-08')
    expect(weeks[1].to).toBe('2026-06-14')
    expect(weeks[weeks.length - 1].to).toBe('2026-10-01') // clipped at the end
    expect(weeks.every((w) => !w.monday)).toBe(true)
  })
  it('matches a week row by its first covered date and names the dates in its title', () => {
    const p120: Period = { kind: 'custom', from: '2026-06-03', to: '2026-10-01' }
    const row = bar('2026-06-03', [['a', 1, 40]], { from_day: '2026-06-03', to_day: '2026-06-07' })
    const w = cols([row], p120)[0]
    expect(w.total).toBe(40)
    expect(columnName(w, 'cost')).toBe('Jun 3 – Jun 7, $40.00')
  })
  it('treats every column of an all-time window as in the period', () => {
    const win = chartWindow({ kind: 'preset', preset: 'all' }, TODAY, { first_day: '2026-09-22', last_day: '2026-10-12' })
    const c = buildColumns(win, [], { from: null, to: null }, 'cost')
    expect(c).toHaveLength(21)
    expect(c.every((x) => x.inPeriod)).toBe(true)
  })
})

describe('max rule and value label (EXP-36)', () => {
  const rows = [
    bar('2026-09-25', [['a', 1, 900]]), // outside the period, the window max
    bar('2026-10-07', [['a', 1, 549]]),
    bar('2026-10-09', [['a', 1, 549]]), // a tie: the first wins
    bar('2026-10-10', [['a', 1, 30]]),
  ]
  const c = cols(rows)
  it('takes the window maximum over every column, in or out of the period', () => {
    expect(windowMax(c)).toBe(900)
  })
  it('labels only the highest day inside the period, first on a tie', () => {
    expect(c[peakIndex(c)!].from).toBe('2026-10-07')
  })
  it('has no label when no day in the period has a value', () => {
    expect(peakIndex(cols([bar('2026-09-25', [['a', 1, 9]])]))).toBeNull()
  })
  it('scales heights linearly to the max, with 2 px as the floor for a non-zero bar', () => {
    expect(barHeight(900, 900)).toBe(200)
    expect(barHeight(450, 900)).toBe(100)
    expect(barHeight(0.1, 900)).toBe(2)
    expect(barHeight(0, 900)).toBe(0)
    expect(barHeight(5, 0)).toBe(0)
  })
})

describe('labels and tooltip (EXP-34, EXP-37)', () => {
  const c = cols([bar('2026-10-07', [['a', 1, 300], ['b', 2, 200], ['c', 3, 100], ['d', 4, 50], ['e', 5, 20], ['f', 6, 10]])])
  const col = c.find((x) => x.from === '2026-10-07')!
  it('labels a day column with its date number only', () => {
    expect(columnLabel(col, null)).toBe('7')
  })
  it('names a column with its weekday, date and total', () => {
    expect(columnName(col, 'cost')).toBe('Wed Oct 7, $680.00')
  })
  it('lists the top 4 projects and counts the rest', () => {
    const tip = tooltipModel(col, 'cost')
    expect(tip.title).toBe('Calls on Wed Oct 7')
    expect(tip.total).toBe('$680.00')
    expect(tip.lines.map((l) => l.name)).toEqual(['a', 'b', 'c', 'd'])
    expect(tip.more).toBe(2)
  })
  it('appends + to the day total when the day has unpriced calls', () => {
    const lb = cols([bar('2026-10-07', [['a', 1, 5]], { cost_lower_bound: true, n_unpriced: 2 })]).find((x) => x.from === '2026-10-07')!
    expect(tooltipModel(lb, 'cost').total).toBe('$5.00+')
  })
  it('puts the month on the first week column of each month', () => {
    const weeks = cols([], { kind: 'custom', from: '2026-06-03', to: '2026-10-01' })
    expect(columnLabel(weeks[0], null)).toBe('Jun')
    expect(columnLabel(weeks[1], weeks[0])).toBe('')
    const firstJuly = weeks.findIndex((w) => w.from.startsWith('2026-07'))
    expect(columnLabel(weeks[firstJuly], weeks[firstJuly - 1])).toBe('Jul')
  })
})

describe('drag and keyboard selection (EXP-38, EXP-38a)', () => {
  const c = cols([])
  const at = (day: string) => c.findIndex((x) => x.from === day)
  it('takes a press and release on one column as a click', () => {
    expect(isClick(3, 3)).toBe(true)
    expect(isClick(3, 4)).toBe(false)
  })
  it('turns a drag across columns into the range, whichever way it ran', () => {
    expect(columnsRange(c, at('2026-10-01'), at('2026-10-08'))).toEqual({ from: '2026-10-01', to: '2026-10-08' })
    expect(columnsRange(c, at('2026-10-08'), at('2026-10-01'))).toEqual({ from: '2026-10-01', to: '2026-10-08' })
  })
  it('spans the dates of week columns at both ends of a drag', () => {
    const weeks = cols([], { kind: 'custom', from: '2026-06-03', to: '2026-10-01' })
    expect(columnsRange(weeks, 0, 1)).toEqual({ from: '2026-06-03', to: '2026-06-14' })
  })
  it('moves focus with the arrows, clamped at both ends, without changing the period', () => {
    expect(chartKey({ focus: 5, anchor: null }, 'ArrowRight', false, c)).toEqual({ state: { focus: 6, anchor: null }, range: null })
    expect(chartKey({ focus: 0, anchor: null }, 'ArrowLeft', false, c)!.state.focus).toBe(0)
    expect(chartKey({ focus: 20, anchor: null }, 'ArrowRight', false, c)!.state.focus).toBe(20)
  })
  it('sets the period to the focused day on Enter', () => {
    expect(chartKey({ focus: at('2026-10-07'), anchor: null }, 'Enter', false, c)!.range).toEqual({ from: '2026-10-07', to: '2026-10-07' })
  })
  it('extends from the focused day with shift+arrow: Oct 7, shift+right twice gives Oct 7 to Oct 9', () => {
    let s = { focus: at('2026-10-07'), anchor: null as number | null }
    let r = chartKey(s, 'ArrowRight', true, c)!
    expect(r.range).toEqual({ from: '2026-10-07', to: '2026-10-08' })
    r = chartKey(r.state, 'ArrowRight', true, c)!
    s = r.state
    expect(r.range).toEqual({ from: '2026-10-07', to: '2026-10-09' })
    expect(s.anchor).toBe(at('2026-10-07'))
    r = chartKey(s, 'ArrowLeft', true, c)! // shrinks back toward the anchor
    expect(r.range).toEqual({ from: '2026-10-07', to: '2026-10-08' })
  })
  it('ends an extension when focus moves without shift', () => {
    const r = chartKey({ focus: 8, anchor: 4 }, 'ArrowRight', false, c)!
    expect(r.state.anchor).toBeNull()
  })
  it('ignores other keys', () => {
    expect(chartKey({ focus: 3, anchor: null }, 'a', false, c)).toBeNull()
  })
})

describe('footprint of a hovered session (EXP-40)', () => {
  const c = cols([bar('2026-10-07', [['a', 1, 300], ['b', 2, 100]]), bar('2026-10-08', [['a', 1, 150]])])
  const session = { day_split: [{ day: '2026-10-07', cost_usd: 60, tok_total: 6000 }, { day: '2026-10-08', cost_usd: 40, tok_total: 4000 }] }
  it('names every column in which the session made calls, with its share', () => {
    const f = footprint(session, c, 'cost')
    const i7 = c.findIndex((x) => x.from === '2026-10-07')
    expect([...f.keys()]).toEqual([i7, i7 + 1])
    expect(f.get(i7)!.value).toBe(60)
  })
  it('uses tokens in the Tokens measure', () => {
    const f = footprint(session, c, 'tokens')
    expect(f.get(c.findIndex((x) => x.from === '2026-10-08'))!.value).toBe(4000)
  })
  it('sums the days a week column covers', () => {
    const weeks = cols([], { kind: 'custom', from: '2026-06-03', to: '2026-10-01' })
    const f = footprint({ day_split: [{ day: '2026-06-04', cost_usd: 1, tok_total: 1 }, { day: '2026-06-05', cost_usd: 2, tok_total: 2 }] }, weeks, 'cost')
    expect(f.get(0)!.value).toBe(3)
  })
  it('places the box above the slots below it, at least 3 px tall', () => {
    const col = c.find((x) => x.from === '2026-10-07')!
    const box = footprintBox(col, 2, 60, 400)!
    expect(box.bottom).toBe((300 / 400) * 200)
    expect(box.height).toBe((60 / 400) * 200)
    expect(footprintBox(col, 2, 0.1, 400)!.height).toBe(3)
  })
  it('has no box when the session is not in the column', () => {
    expect(footprintBox(c.find((x) => x.from === '2026-10-07')!, 4, 5, 400)).toBeNull()
  })
})
