import { describe, expect, it } from 'vitest'
import { localDay, localMidnight, localMidnightsBetween, weekdayShort } from './tz'

const KOL = 'Asia/Kolkata'

describe('tz', () => {
  it('uses the store zone, not UTC, for the local day', () => {
    const ms = Date.UTC(2026, 9, 11, 20, 0) // 01:30 Mon Oct 12 in Kolkata
    expect(localDay(ms, KOL)).toBe('2026-10-12')
    expect(localDay(ms, 'UTC')).toBe('2026-10-11')
    expect(weekdayShort(ms, KOL)).toBe('Mon')
  })

  it('finds the instant a local day starts', () => {
    expect(localMidnight('2026-10-12', KOL)).toBe(Date.UTC(2026, 9, 11, 18, 30))
    expect(localMidnight('2026-10-12', 'America/New_York')).toBe(Date.UTC(2026, 9, 12, 4, 0))
  })

  it('lists the midnights a span crosses, excluding the endpoints', () => {
    const a = Date.UTC(2026, 9, 11, 16, 0) // Oct 11 21:30 IST
    const b = a + 38 * 3600_000 // Oct 13 11:30 IST
    expect(localMidnightsBetween(a, b, KOL)).toEqual([localMidnight('2026-10-12', KOL), localMidnight('2026-10-13', KOL)])
    expect(localMidnightsBetween(a, a + 3600_000, KOL)).toEqual([])
    expect(localMidnightsBetween(b, a, KOL)).toEqual([])
    const m = localMidnight('2026-10-12', KOL)
    expect(localMidnightsBetween(m, m + 3600_000, KOL)).toEqual([])
  })
})
