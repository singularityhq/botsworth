// @vitest-environment jsdom
import { act, cleanup, render } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { BladeStrip, type BladeSpec } from './BladeStrip'

const spec = (key: string): BladeSpec => ({ key, label: key, render: () => <p>{key}</p> })

class NoopObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}

beforeEach(() => {
  vi.useFakeTimers()
  vi.stubGlobal('ResizeObserver', NoopObserver)
  vi.stubGlobal('matchMedia', () => ({ matches: false }))
  vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => setTimeout(() => cb(performance.now()), 16))
  vi.stubGlobal('cancelAnimationFrame', (id: number) => clearTimeout(id))
  vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(1440)
})
afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('BladeStrip unmounted mid-transition (SYS-58)', () => {
  it('stops sliding: no frame callback runs against the removed blades', () => {
    const { rerender, unmount } = render(<BladeStrip blades={[spec('a'), spec('b')]} focusKey="b" onFocus={() => {}} />)
    rerender(<BladeStrip blades={[spec('a')]} focusKey="a" onFocus={() => {}} />)
    act(() => {
      vi.advanceTimersByTime(40)
    })
    unmount()
    expect(() => vi.advanceTimersByTime(500)).not.toThrow()
    expect(vi.getTimerCount()).toBe(0)
  })
})
