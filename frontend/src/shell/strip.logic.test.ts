import { describe, expect, it } from 'vitest'
import { bladeLeft, bladeWidths, easeBlade, moveFocus, scrollToNewest, scrollToReveal, stripTotal } from './strip.logic'

describe('bladeWidths (SYS-46)', () => {
  it('gives one blade the whole stage', () => {
    expect(bladeWidths(1, 1416)).toEqual([1416])
    expect(bladeWidths(1, 1000)).toEqual([1000])
  })
  it('at W = 1416, parents are 400 and the newest takes 1004 (SES-2 table)', () => {
    expect(bladeWidths(2, 1416)).toEqual([400, 1004])
    expect(bladeWidths(3, 1416)).toEqual([400, 400, 1004])
    expect(bladeWidths(4, 1416)).toEqual([400, 400, 400, 1004])
  })
  it('at W = 1000, the newest keeps 640 and the parent is partly scrolled off', () => {
    expect(bladeWidths(2, 1000)).toEqual([400, 640])
    expect(bladeWidths(3, 1000)).toEqual([400, 400, 640])
    expect(bladeWidths(4, 1000)).toEqual([400, 400, 400, 640])
  })
  it('switches to the 640 minimum below W = 1052', () => {
    expect(bladeWidths(2, 1052)[1]).toBe(640)
    expect(bladeWidths(2, 1051)[1]).toBe(640)
    expect(bladeWidths(2, 1053)[1]).toBe(641)
  })
  it('returns no widths for no blades', () => {
    expect(bladeWidths(0, 1416)).toEqual([])
  })
})

describe('scroll targets', () => {
  it('shows the newest blade fully at the right edge', () => {
    expect(scrollToNewest(bladeWidths(1, 1416), 1416)).toBe(0)
    expect(scrollToNewest(bladeWidths(2, 1416), 1416)).toBe(0)
    expect(scrollToNewest(bladeWidths(3, 1416), 1416)).toBe(412)
    expect(scrollToNewest(bladeWidths(4, 1416), 1416)).toBe(824)
    expect(scrollToNewest(bladeWidths(2, 1000), 1000)).toBe(52)
  })
  it('puts the session and turn in view for three blades (SES-2): turn right edge at the stage edge', () => {
    const w = bladeWidths(3, 1416)
    const s = scrollToNewest(w, 1416)
    expect(bladeLeft(w, 1) - s).toBe(0)
    expect(bladeLeft(w, 2) + w[2] - s).toBe(1416)
  })
  it('measures the strip with 12 px gaps', () => {
    expect(stripTotal([400, 400, 1004])).toBe(1828)
    expect(bladeLeft([400, 400, 1004], 2)).toBe(824)
  })
  it('reveals a blade with the least movement', () => {
    const w = bladeWidths(4, 1416)
    const newest = scrollToNewest(w, 1416)
    expect(scrollToReveal(w, 0, newest, 1416)).toBe(0)
    expect(scrollToReveal(w, 1, newest, 1416)).toBe(412)
    expect(scrollToReveal(w, 3, 0, 1416)).toBe(newest)
    expect(scrollToReveal(w, 2, newest, 1416)).toBe(newest)
  })
})

describe('moveFocus (SYS-50)', () => {
  it('moves by one and clamps at both ends', () => {
    expect(moveFocus(3, 1, 1)).toBe(2)
    expect(moveFocus(3, 2, 1)).toBe(2)
    expect(moveFocus(3, 0, -1)).toBe(0)
    expect(moveFocus(1, 0, 1)).toBe(0)
  })
})

describe('easeBlade (cubic-bezier .22, 1, .36, 1)', () => {
  it('runs from 0 to 1, rises monotonically, and is front-loaded', () => {
    expect(easeBlade(0)).toBe(0)
    expect(easeBlade(1)).toBe(1)
    let prev = 0
    for (let i = 1; i <= 20; i++) {
      const v = easeBlade(i / 20)
      expect(v).toBeGreaterThanOrEqual(prev)
      prev = v
    }
    expect(easeBlade(0.25)).toBeGreaterThan(0.6)
  })
})
