import { describe, expect, it } from 'vitest'
import { barWidth } from './bar.logic'

describe('barWidth (SYS-39)', () => {
  it('is linear from 0 to the maximum', () => {
    expect(barWidth(50, 100, 96)).toBe(48)
    expect(barWidth(100, 100, 96)).toBe(96)
    expect(barWidth(25, 100, 64)).toBe(16)
  })
  it('draws no bar under 1 px, and draws one at exactly 1 px', () => {
    expect(barWidth(0.5, 100, 96)).toBe(0)
    expect(barWidth(1 / 96 - 1e-9, 1, 96)).toBe(0)
    expect(barWidth(1, 96, 96)).toBe(1)
  })
  it('draws nothing for zero, null, or an empty list', () => {
    expect(barWidth(0, 100, 96)).toBe(0)
    expect(barWidth(null, 100, 96)).toBe(0)
    expect(barWidth(5, 0, 96)).toBe(0)
  })
  it('clamps a value above the maximum to the track', () => {
    expect(barWidth(200, 100, 96)).toBe(96)
  })
})
