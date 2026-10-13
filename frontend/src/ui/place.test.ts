import { describe, expect, it } from 'vitest'
import { placeBox } from './place'

const vp = { w: 1000, h: 800 }
const size = { w: 200, h: 40 }

describe('placeBox', () => {
  it('centres above the anchor when there is room', () => {
    const p = placeBox({ left: 400, top: 300, right: 440, bottom: 328 }, size, vp, { side: 'top', align: 'center' })
    expect(p).toEqual({ left: 320, top: 252, side: 'top' })
  })
  it('flips below when there is no room above', () => {
    const p = placeBox({ left: 400, top: 20, right: 440, bottom: 48 }, size, vp, { side: 'top', align: 'center' })
    expect(p.side).toBe('bottom')
    expect(p.top).toBe(56)
  })
  it('clamps to the viewport edges', () => {
    const l = placeBox({ left: 0, top: 300, right: 20, bottom: 328 }, size, vp, { side: 'top', align: 'center' })
    expect(l.left).toBe(8)
    const r = placeBox({ left: 980, top: 300, right: 1000, bottom: 328 }, size, vp, { side: 'top', align: 'center' })
    expect(r.left).toBe(792)
  })
  it('aligns a popover to its anchor end', () => {
    const p = placeBox({ left: 700, top: 10, right: 800, bottom: 40 }, { w: 320, h: 200 }, vp, { side: 'bottom', align: 'end' })
    expect(p.left).toBe(480)
  })
})
