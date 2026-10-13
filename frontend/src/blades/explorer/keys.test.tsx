// @vitest-environment jsdom
import { cleanup, renderHook } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useExplorerEscape, useExplorerKeys, useExplorerRouter } from './keys'

const captured = vi.hoisted(() => ({ key: null as null | ((e: KeyboardEvent) => boolean | void), esc: null as null | (() => boolean) }))
vi.mock('../../shell/keys', () => ({
  useBladeKeys: (_k: string, h: (e: KeyboardEvent) => boolean | void) => void (captured.key = h),
  useEscape: (_k: string, f: () => boolean) => void (captured.esc = f),
}))

const ev = (key: string) => new KeyboardEvent('keydown', { key })

afterEach(cleanup)

describe('explorer key router', () => {
  it('runs the page keys first, then the parts in registration order, and stops at the first true', () => {
    const calls: string[] = []
    renderHook(() => {
      useExplorerRouter({ key: (e) => (calls.push('page'), e.key === 'p'), escape: () => false })
      useExplorerKeys(() => (calls.push('list'), false))
      useExplorerKeys((e) => (calls.push('rail'), e.key === 'j'))
    })
    expect(captured.key!(ev('p'))).toBe(true)
    expect(calls).toEqual(['page'])
    calls.length = 0
    expect(captured.key!(ev('j'))).toBe(true)
    expect(calls).toEqual(['page', 'list', 'rail'])
    calls.length = 0
    expect(captured.key!(ev('x'))).toBe(false)
  })
  it('gives Esc to the page first, then to the parts', () => {
    const order: string[] = []
    renderHook(() => {
      useExplorerRouter({ key: () => false, escape: () => (order.push('page'), false) })
      useExplorerEscape(() => (order.push('list'), true))
    })
    expect(captured.esc!()).toBe(true)
    expect(order).toEqual(['page', 'list'])
  })
  it('drops a part when it unmounts', () => {
    const part = vi.fn(() => true)
    renderHook(() => useExplorerRouter({ key: () => false, escape: () => false }))
    const h = renderHook(() => useExplorerKeys(part))
    expect(captured.key!(ev('j'))).toBe(true)
    h.unmount()
    expect(captured.key!(ev('j'))).toBe(false)
  })
})
