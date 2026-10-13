import { describe, expect, it } from 'vitest'
import { addDays, dayNum, diffDays, isDay, mondayOf, weekdayOf } from './day'

describe('Day arithmetic', () => {
  it('steps across month and year ends', () => {
    expect(addDays('2026-10-31', 1)).toBe('2026-11-01')
    expect(addDays('2026-01-01', -1)).toBe('2025-12-31')
    expect(addDays('2024-02-28', 1)).toBe('2024-02-29')
    expect(addDays('2026-02-28', 1)).toBe('2026-03-01')
  })

  it('counts days between two Days', () => {
    expect(diffDays('2026-10-06', '2026-10-12')).toBe(6)
    expect(diffDays('2026-10-12', '2026-10-06')).toBe(-6)
  })

  it('finds the weekday and the Monday on or before', () => {
    expect(weekdayOf('2026-10-12')).toBe(1)
    expect(weekdayOf('2026-10-11')).toBe(0)
    expect(mondayOf('2026-10-12')).toBe('2026-10-12')
    expect(mondayOf('2026-10-11')).toBe('2026-10-05')
    expect(mondayOf('2026-10-14')).toBe('2026-10-12')
  })

  it('rejects text that is not a calendar date', () => {
    expect(isDay('2026-10-12')).toBe(true)
    expect(isDay('2026-02-30')).toBe(false)
    expect(isDay('2026-13-01')).toBe(false)
    expect(isDay('2026-1-1')).toBe(false)
    expect(() => dayNum('oct 6')).toThrow()
  })
})
