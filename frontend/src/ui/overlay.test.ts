import { afterEach, describe, expect, it, vi } from 'vitest'
import { closeTopOverlay, overlayOpen, registerOverlay } from './overlay'

const undo: (() => void)[] = []
afterEach(() => {
  while (undo.length) undo.pop()!()
})

describe('overlay stack', () => {
  it('reports no overlay when empty', () => {
    expect(overlayOpen()).toBe(false)
    expect(closeTopOverlay()).toBe(false)
  })
  it('closes the newest overlay first', () => {
    const a = vi.fn()
    const b = vi.fn()
    undo.push(registerOverlay(a), registerOverlay(b))
    expect(overlayOpen()).toBe(true)
    expect(closeTopOverlay()).toBe(true)
    expect(b).toHaveBeenCalledOnce()
    expect(a).not.toHaveBeenCalled()
  })
  it('forgets an overlay once it unregisters', () => {
    const a = vi.fn()
    registerOverlay(a)()
    expect(closeTopOverlay()).toBe(false)
    expect(a).not.toHaveBeenCalled()
  })
  it('a non-blocking overlay (a toast) does not count as open for the keys', () => {
    undo.push(registerOverlay(vi.fn(), false))
    expect(overlayOpen()).toBe(false)
  })
  it('Esc closes a blocking overlay before a toast, and the toast when it is alone', () => {
    const toast = vi.fn()
    const pop = vi.fn()
    const offPop = registerOverlay(pop)
    undo.push(registerOverlay(toast, false), offPop)
    expect(closeTopOverlay()).toBe(true)
    expect(pop).toHaveBeenCalledOnce()
    expect(toast).not.toHaveBeenCalled()
    offPop() // the popover is gone
    expect(closeTopOverlay()).toBe(true)
    expect(toast).toHaveBeenCalledOnce()
  })
})
