import { describe, expect, it } from 'vitest'
import { chartWindow, editRange, parseDateRows, parseDay, resolvePeriod, todayIn, type Period, type Preset } from './period'

const preset = (p: Preset): Period => ({ kind: 'preset', preset: p })
const custom = (from: string, to: string): Period => ({ kind: 'custom', from, to })

// 2026-10-12 is a Monday; 2026-10-11 is a Sunday.
const MONDAY = '2026-10-12'
const SUNDAY = '2026-10-11'

describe('todayIn', () => {
  it('reads the date in the store zone, not in UTC', () => {
    // 20:00 UTC on Sun Oct 11 is 01:30 on Mon Oct 12 in Kolkata.
    const now = Date.UTC(2026, 9, 11, 20, 0)
    expect(todayIn('Asia/Kolkata', now)).toBe('2026-10-12')
    expect(todayIn('UTC', now)).toBe('2026-10-11')
    expect(todayIn('America/Los_Angeles', now)).toBe('2026-10-11')
  })

  it('rejects an unknown zone', () => {
    expect(() => todayIn('Not/AZone', 0)).toThrow()
  })
})

describe('resolvePeriod (EXP-13, EXP-14)', () => {
  const cases: [Preset, string, [string, string] | null, [string, string] | null][] = [
    // preset, label, on a Monday, on a Sunday
    ['today', 'today', [MONDAY, MONDAY], [SUNDAY, SUNDAY]],
    ['yesterday', 'yesterday', ['2026-10-11', '2026-10-11'], ['2026-10-10', '2026-10-10']],
    ['last7', 'last 7 days', ['2026-10-06', MONDAY], ['2026-10-05', SUNDAY]],
    ['thisweek', 'Monday to today', [MONDAY, MONDAY], ['2026-10-05', SUNDAY]],
    ['lastweek', 'Monday to Sunday', ['2026-10-05', '2026-10-11'], ['2026-09-28', '2026-10-04']],
    ['all', 'all time', null, null],
  ]
  for (const [p, label, mon, sun] of cases) {
    it(`${p} (${label}) on a Monday and on a Sunday`, () => {
      const expected = (r: [string, string] | null) => (r ? { from: r[0], to: r[1] } : { from: null, to: null })
      expect(resolvePeriod(preset(p), MONDAY)).toEqual(expected(mon))
      expect(resolvePeriod(preset(p), SUNDAY)).toEqual(expected(sun))
    })
  }

  it('crosses a year boundary', () => {
    expect(resolvePeriod(preset('last7'), '2026-01-01')).toEqual({ from: '2025-12-26', to: '2026-01-01' })
    expect(resolvePeriod(preset('lastweek'), '2026-01-01')).toEqual({ from: '2025-12-22', to: '2025-12-28' })
  })

  it('returns a custom period as given', () => {
    expect(resolvePeriod(custom('2026-10-01', '2026-10-03'), MONDAY)).toEqual({ from: '2026-10-01', to: '2026-10-03' })
  })
})

describe('chartWindow (EXP-32, D41 M10)', () => {
  const today = MONDAY // 2026-10-12

  it('shows 21 days ending today when the period ends today', () => {
    expect(chartWindow(preset('last7'), today)).toEqual({ from: '2026-09-22', to: '2026-10-12', bucket: 'day' })
    expect(chartWindow(preset('today'), today)).toEqual({ from: '2026-09-22', to: '2026-10-12', bucket: 'day' })
  })

  it('ends at today when the period ended within the last 14 days (today plus 13 days back)', () => {
    // lastweek ends Sun Oct 4, which is 8 days ago.
    expect(chartWindow(preset('lastweek'), today)).toEqual({ from: '2026-09-22', to: '2026-10-12', bucket: 'day' })
    expect(chartWindow(custom('2026-09-25', '2026-09-29'), today).to).toBe('2026-10-12') // ended 13 days ago
  })

  it('ends at the period end when the period ended 14 days ago or more', () => {
    expect(chartWindow(custom('2026-09-25', '2026-09-28'), today)).toEqual({ from: '2026-09-08', to: '2026-09-28', bucket: 'day' })
    expect(chartWindow(custom('2026-09-01', '2026-09-07'), today)).toEqual({ from: '2026-08-18', to: '2026-09-07', bucket: 'day' })
  })

  it('never cuts the period off: a 21-day period ending recently shows exactly that period', () => {
    expect(chartWindow(custom('2026-09-15', '2026-10-05'), today)).toEqual({ from: '2026-09-15', to: '2026-10-05', bucket: 'day' })
  })

  it('shows the period itself, one column per day, for 22 to 92 days', () => {
    expect(chartWindow(custom('2026-09-13', '2026-10-12'), today)).toEqual({ from: '2026-09-13', to: '2026-10-12', bucket: 'day' }) // 30 days
    expect(chartWindow(custom('2026-09-20', '2026-10-12'), today)).toEqual({ from: '2026-09-20', to: '2026-10-12', bucket: 'day' }) // 23 days
    const ninetyTwo = chartWindow(custom('2026-07-13', '2026-10-12'), today)
    expect(ninetyTwo).toEqual({ from: '2026-07-13', to: '2026-10-12', bucket: 'day' })
  })

  it('switches to week columns above 92 days', () => {
    expect(chartWindow(custom('2026-07-12', '2026-10-12'), today)).toEqual({ from: '2026-07-12', to: '2026-10-12', bucket: 'week' }) // 93 days
    expect(chartWindow(custom('2026-07-05', '2026-10-12'), today).bucket).toBe('week') // 100 days
  })

  it('uses the data extent for all time', () => {
    const extent = { first_day: '2026-09-22', last_day: '2026-10-12' }
    expect(chartWindow(preset('all'), today, extent)).toEqual({ from: '2026-09-22', to: '2026-10-12', bucket: 'day' })
    expect(chartWindow(preset('all'), today, { first_day: '2026-01-01', last_day: '2026-10-12' }).bucket).toBe('week')
  })

  it('refuses all time without an extent', () => {
    expect(() => chartWindow(preset('all'), today)).toThrow()
    expect(() => chartWindow(preset('all'), today, { first_day: null, last_day: null })).toThrow()
  })

  it('keeps a period that ends in the future', () => {
    expect(chartWindow(custom('2026-10-10', '2026-10-20'), today)).toEqual({ from: '2026-09-30', to: '2026-10-20', bucket: 'day' })
  })
})

describe('parseDay (EXP-15)', () => {
  const today = MONDAY

  it('reads month-day forms with a month name', () => {
    expect(parseDay('oct 6', today)).toBe('2026-10-06')
    expect(parseDay('Oct 6', today)).toBe('2026-10-06')
    expect(parseDay('6 oct', today)).toBe('2026-10-06')
    expect(parseDay('6oct', today)).toBe('2026-10-06')
    expect(parseDay('  october   6 ', today)).toBe('2026-10-06')
    expect(parseDay('sept 3', today)).toBe('2026-09-03')
  })

  it('reads numeric month-day forms as month first', () => {
    expect(parseDay('10-06', today)).toBe('2026-10-06')
    expect(parseDay('10/06', today)).toBe('2026-10-06')
    expect(parseDay('9/30', today)).toBe('2026-09-30')
  })

  it('reads a full date', () => {
    expect(parseDay('2026-10-06', today)).toBe('2026-10-06')
    expect(parseDay('2025-12-31', today)).toBe('2025-12-31')
  })

  it('reads today, yesterday and weekday names (the latest such day up to today)', () => {
    expect(parseDay('today', today)).toBe('2026-10-12')
    expect(parseDay('yesterday', today)).toBe('2026-10-11')
    expect(parseDay('monday', today)).toBe('2026-10-12') // today counts
    expect(parseDay('sunday', today)).toBe('2026-10-11')
    expect(parseDay('fri', today)).toBe('2026-10-09')
    expect(parseDay('Tuesday', today)).toBe('2026-10-06')
  })

  it('takes the latest such date up to today for a date without a year', () => {
    expect(parseDay('oct 12', today)).toBe('2026-10-12')
    expect(parseDay('oct 13', today)).toBe('2025-10-13')
    expect(parseDay('dec 25', today)).toBe('2025-12-25')
    expect(parseDay('feb 29', today)).toBe('2024-02-29')
  })

  it('returns null for text that is not a date', () => {
    for (const bad of ['', '  ', 'foo', 'oct', 'oct 32', 'oct 0', 'feb 30', '13/01', '2026-13-01', '2026-02-30', '2026-1-1', 'mo', 'x 6']) {
      expect(parseDay(bad, today), bad).toBeNull()
    }
  })
})

describe('editRange (EXP-15 From > To rule)', () => {
  const r = { from: '2026-10-06', to: '2026-10-12' }

  it('keeps both fields when the edit leaves From <= To', () => {
    expect(editRange(r, 'from', '2026-10-08')).toEqual({ from: '2026-10-08', to: '2026-10-12' })
    expect(editRange(r, 'to', '2026-10-07')).toEqual({ from: '2026-10-06', to: '2026-10-07' })
  })

  it('makes To follow when From moves past it', () => {
    expect(editRange(r, 'from', '2026-10-20')).toEqual({ from: '2026-10-20', to: '2026-10-20' })
  })

  it('makes From follow when To moves before it', () => {
    expect(editRange(r, 'to', '2026-10-01')).toEqual({ from: '2026-10-01', to: '2026-10-01' })
  })

  it('accepts a one-day range', () => {
    expect(editRange(r, 'to', '2026-10-06')).toEqual({ from: '2026-10-06', to: '2026-10-06' })
  })
})

describe('parseDateRows (EXP-69 item 2)', () => {
  const today = MONDAY

  it('returns nothing for empty input and for text that is not a date', () => {
    expect(parseDateRows('', today)).toEqual([])
    expect(parseDateRows('rbac', today)).toEqual([])
  })

  it('returns one day row for a date form', () => {
    expect(parseDateRows('oct 7', today)).toEqual([{ kind: 'day', from: '2026-10-07', to: '2026-10-07', preset: null }])
    expect(parseDateRows('wed', today)).toEqual([{ kind: 'day', from: '2026-10-07', to: '2026-10-07', preset: null }])
  })

  it('matches today and yesterday by a 2-letter prefix while typing', () => {
    expect(parseDateRows('to', today)[0]).toMatchObject({ from: '2026-10-12' })
    expect(parseDateRows('yes', today)[0]).toMatchObject({ from: '2026-10-11' })
    expect(parseDateRows('t', today)).toEqual([])
  })

  it('returns range rows for the named ranges', () => {
    expect(parseDateRows('this week', today)).toEqual([{ kind: 'range', from: '2026-10-12', to: '2026-10-12', preset: 'thisweek' }])
    expect(parseDateRows('last week', today)).toEqual([{ kind: 'range', from: '2026-10-05', to: '2026-10-11', preset: 'lastweek' }])
    expect(parseDateRows('7d', today)).toEqual([{ kind: 'range', from: '2026-10-06', to: '2026-10-12', preset: 'last7' }])
    expect(parseDateRows('last 7', today)[0]).toMatchObject({ preset: 'last7' })
    expect(parseDateRows('all', today)).toEqual([{ kind: 'range', from: null, to: null, preset: 'all' }])
  })

  it('lists both last-week and last-7 rows for the prefix "last"', () => {
    expect(parseDateRows('last', today).map((r) => r.preset)).toEqual(['lastweek', 'last7'])
  })

  it('returns only a day row for a weekday prefix', () => {
    const rows = parseDateRows('sat', today)
    expect(rows.map((r) => r.kind)).toEqual(['day'])
  })
})
