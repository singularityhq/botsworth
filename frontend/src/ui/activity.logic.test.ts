import { describe, expect, it } from 'vitest'
import { binHeight, stripGeometry, stripLength } from './activity.logic'

const MIN = 60_000
const H = 3600_000

describe('stripLength (SYS-40)', () => {
  it('is 16 px at 1 minute and below', () => {
    expect(stripLength(MIN, 200)).toBe(16)
    expect(stripLength(5_000, 200)).toBe(16)
    expect(stripLength(0, 200)).toBe(16)
  })
  it('is the full width at 48 h and above', () => {
    expect(stripLength(48 * H, 200)).toBe(200)
    expect(stripLength(200 * H, 200)).toBe(200)
  })
  it('is linear in log10 between: the geometric midpoint is the middle', () => {
    const mid = MIN * Math.sqrt(48 * 60)
    expect(stripLength(mid, 200)).toBeCloseTo(108, 6)
  })
  it('grows with wall time and scales with the slot width', () => {
    expect(stripLength(30 * MIN, 200)).toBeGreaterThan(stripLength(10 * MIN, 200))
    expect(stripLength(2 * H, 248)).toBeGreaterThan(stripLength(2 * H, 200))
    expect(stripLength(48 * H, 88)).toBe(88)
  })
})

describe('binHeight (SYS-40)', () => {
  it('is the full height for the largest bin', () => {
    expect(binHeight(1000, 1000, 26)).toBeCloseTo(26, 9)
  })
  it('has a 2 px minimum for any non-empty bin', () => {
    expect(binHeight(1, 1_000_000, 26)).toBe(2)
  })
  it('draws nothing for an empty bin', () => {
    expect(binHeight(0, 1000, 26)).toBe(0)
  })
  it('scales by log(1 + tokens)', () => {
    expect(binHeight(99, 9999, 24)).toBeCloseTo((Math.log(100) / Math.log(10000)) * 24, 9)
  })
})

describe('stripGeometry', () => {
  const base = { maxBin: 1000, tz: 'Asia/Kolkata', width: 200, height: 28 }
  it('skips empty bins and spans the log-scaled length', () => {
    const g = stripGeometry({ ...base, bins: [0, 500, 0, 1000], wallMs: 48 * H, firstMs: 0, lastMs: 48 * H })
    expect(g.length).toBe(200)
    expect(g.bars).toHaveLength(2)
    expect(g.bars[0].x).toBe(50)
    expect(g.bars[1].h).toBe(26)
  })
  it('places a tick at each local midnight, proportionally on the first-to-last span', () => {
    const first = Date.UTC(2026, 9, 11, 16, 0) // Oct 11 21:30 IST
    const last = first + 24 * H // Oct 12 21:30 IST; one midnight, 2.5 h in
    const g = stripGeometry({ ...base, bins: [1], wallMs: 24 * H, firstMs: first, lastMs: last })
    expect(g.ticks).toHaveLength(1)
    expect(g.ticks[0]).toBeCloseTo((2.5 / 24) * g.length, 6)
  })
  it('draws no ticks for a span inside one day, and nothing for an empty bin list', () => {
    const g = stripGeometry({ ...base, bins: [], wallMs: H, firstMs: 0, lastMs: H })
    expect(g.bars).toEqual([])
    expect(g.ticks).toEqual([])
  })
})
