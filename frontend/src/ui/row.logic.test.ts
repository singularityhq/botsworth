import { describe, expect, it } from 'vitest'
import { groupColumns, rowColumns, weekdayLabel } from './row.logic'

const KOL = 'Asia/Kolkata'
const mon = Date.UTC(2026, 9, 12, 4, 0) // Mon Oct 12 09:30 IST
const monLater = mon + 3 * 3600_000
const tue = mon + 24 * 3600_000

describe('weekdayLabel (SYS-34)', () => {
  it('shows the weekday on the first row', () => {
    expect(weekdayLabel(mon, null, KOL, false)).toBe('Mon')
  })
  it('hides it when the row above is on the same local day', () => {
    expect(weekdayLabel(monLater, mon, KOL, false)).toBe('')
  })
  it('shows it when the day changes', () => {
    expect(weekdayLabel(tue, monLater, KOL, false)).toBe('Tue')
  })
  it('shows it on every row of an unsorted list where days alternate', () => {
    expect(weekdayLabel(mon, tue, KOL, false)).toBe('Mon')
    expect(weekdayLabel(tue, mon, KOL, false)).toBe('Tue')
  })
  it('never shows it under a single-day period', () => {
    expect(weekdayLabel(mon, null, KOL, true)).toBe('')
  })
  it('compares days in the store zone: 23:30 UTC and 00:30 UTC are one IST morning', () => {
    const a = Date.UTC(2026, 9, 11, 19, 0) // 00:30 Mon IST
    const b = Date.UTC(2026, 9, 11, 23, 0) // 04:30 Mon IST
    expect(weekdayLabel(b, a, KOL, false)).toBe('')
  })
})

describe('rowColumns (SYS-33)', () => {
  it('lays out the six slots in order', () => {
    expect(rowColumns()).toBe('88px minmax(0,1fr) 128px 200px 184px 64px 64px 56px')
  })
  it('inserts the context column right after When', () => {
    expect(rowColumns({ context: true }).split(' ').slice(0, 3)).toEqual(['88px', '136px', 'minmax(0,1fr)'])
  })
  it('drops secondaries from the right and never reorders', () => {
    expect(rowColumns({ secondaries: 2 })).toBe('88px minmax(0,1fr) 128px 200px 184px 64px 64px')
    expect(rowColumns({ secondaries: 0 })).toBe('88px minmax(0,1fr) 128px 200px 184px')
  })
  it('accepts the 248 magnitude slot and rejects widths outside 200-248', () => {
    expect(rowColumns({ magnitude: 248, secondaries: 0 })).toContain('248px')
    expect(() => rowColumns({ magnitude: 300 })).toThrow()
  })
})

describe('groupColumns (SYS-37)', () => {
  it('keeps the totals in the same columns as the rows beneath', () => {
    for (const o of [{}, { magnitude: 248 }, { secondaries: 1 as const }, { context: true }]) {
      const row = rowColumns(o).split(' ')
      const head = groupColumns(o).split(' ')
      expect(head.slice(1)).toEqual(row.slice(row.length - (head.length - 1)))
    }
  })
})
