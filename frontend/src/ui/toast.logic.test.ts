import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { PauseTimer } from './toast.logic'

describe('PauseTimer', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })
  afterEach(() => {
    vi.useRealTimers()
  })
  it('fires after the timeout', () => {
    const done = vi.fn()
    new PauseTimer(8000, done)
    vi.advanceTimersByTime(7999)
    expect(done).not.toHaveBeenCalled()
    vi.advanceTimersByTime(1)
    expect(done).toHaveBeenCalledOnce()
  })
  it('does not fire while paused and resumes with the time left', () => {
    const done = vi.fn()
    const t = new PauseTimer(8000, done)
    vi.advanceTimersByTime(5000)
    t.pause()
    vi.advanceTimersByTime(60_000)
    expect(done).not.toHaveBeenCalled()
    t.resume()
    vi.advanceTimersByTime(2999)
    expect(done).not.toHaveBeenCalled()
    vi.advanceTimersByTime(1)
    expect(done).toHaveBeenCalledOnce()
  })
  it('never fires after cancel', () => {
    const done = vi.fn()
    const t = new PauseTimer(100, done)
    t.cancel()
    t.resume()
    vi.advanceTimersByTime(1000)
    expect(done).not.toHaveBeenCalled()
  })
})
