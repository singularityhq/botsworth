import { describe, expect, it, vi } from 'vitest'
import { classifyKey, runEscape, type EscapeCtx, type KeyInfo } from './keys.logic'

const k = (key: string, o: Partial<KeyInfo> = {}): KeyInfo => ({ key, metaKey: false, ctrlKey: false, altKey: false, ...o })

describe('classifyKey (SYS-50)', () => {
  it('toggles the palette on Cmd+K and Ctrl+K, even while typing or with an overlay open', () => {
    expect(classifyKey(k('k', { metaKey: true }), false, false)).toBe('palette-toggle')
    expect(classifyKey(k('K', { ctrlKey: true }), true, true)).toBe('palette-toggle')
  })
  it('routes Esc always', () => {
    expect(classifyKey(k('Escape'), true, true)).toBe('escape')
  })
  it('ignores every other key while a text field has focus', () => {
    for (const key of ['/', 'p', 'f', 'j', 'ArrowLeft', 'ArrowRight', 'Enter']) expect(classifyKey(k(key), true, false)).toBe('ignore')
  })
  it('leaves other keys to an open overlay', () => {
    expect(classifyKey(k('j'), false, true)).toBe('ignore')
    expect(classifyKey(k('ArrowRight'), false, true)).toBe('ignore')
  })
  it('moves blade focus with the arrows and passes the rest to the focused blade', () => {
    expect(classifyKey(k('ArrowLeft'), false, false)).toBe('focus-left')
    expect(classifyKey(k('ArrowRight'), false, false)).toBe('focus-right')
    for (const key of ['/', 'p', 'f', 'j', 'k', 'Enter', 's', 'b']) expect(classifyKey(k(key), false, false)).toBe('blade')
  })
  it('ignores a plain key pressed with a modifier', () => {
    expect(classifyKey(k('p', { metaKey: true }), false, false)).toBe('ignore')
    expect(classifyKey(k('ArrowLeft', { altKey: true }), false, false)).toBe('ignore')
  })
})

function ctx(over: Partial<EscapeCtx> = {}) {
  const calls: string[] = []
  const c: EscapeCtx = {
    closeOverlay: vi.fn(() => (calls.push('overlay'), false)),
    isTyping: vi.fn(() => false),
    blurField: vi.fn(() => void calls.push('blur')),
    bladeCount: 3,
    rightmostEscape: vi.fn(() => (calls.push('blade'), false)),
    closeRightmost: vi.fn(() => void calls.push('close')),
    ...over,
  }
  return { c, calls }
}

describe('runEscape (SES-6 order)', () => {
  it('closes an overlay first and does nothing else', () => {
    const { c, calls } = ctx({ closeOverlay: vi.fn(() => true), isTyping: () => true })
    expect(runEscape(c)).toBe('overlay')
    expect(calls).toEqual([])
  })
  it('blurs a focused field before the blade sees Esc', () => {
    const { c, calls } = ctx({ isTyping: () => true })
    expect(runEscape(c)).toBe('blur')
    expect(calls).toEqual(['overlay', 'blur'])
  })
  it('lets the rightmost blade consume Esc before it is closed', () => {
    const { c, calls } = ctx({ rightmostEscape: vi.fn(() => true) })
    expect(runEscape(c)).toBe('blade')
    expect(c.closeRightmost).not.toHaveBeenCalled()
    expect(calls).toEqual(['overlay'])
  })
  it('closes the rightmost blade when nothing else consumed Esc', () => {
    const { c, calls } = ctx()
    expect(runEscape(c)).toBe('close')
    expect(calls).toEqual(['overlay', 'blade', 'close'])
  })
  it('never closes the explorer when it is alone (EXP-46 stays with its handler)', () => {
    const { c } = ctx({ bladeCount: 1 })
    expect(runEscape(c)).toBe('none')
    expect(c.closeRightmost).not.toHaveBeenCalled()
    expect(c.rightmostEscape).toHaveBeenCalled()
  })
})
