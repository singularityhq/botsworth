import { describe, expect, it } from 'vitest'
import { crumbList, truncateCrumb } from './breadcrumb.logic'

describe('truncateCrumb (SYS-49)', () => {
  it('keeps text up to 44 characters', () => {
    const s = 'a'.repeat(44)
    expect(truncateCrumb(s)).toBe(s)
  })
  it('cuts longer text to 44 characters with an ellipsis', () => {
    const out = truncateCrumb('b'.repeat(60))
    expect(out).toHaveLength(44)
    expect(out.endsWith('…')).toBe(true)
  })
})

describe('crumbList (SES-7)', () => {
  const blades = [
    { key: 'explorer', label: 'Sessions' },
    { key: 'session', label: 'Session' },
    { key: 'turn', label: 'Turn' },
  ]
  it('reads Sessions alone when only the explorer is open', () => {
    expect(crumbList(blades.slice(0, 1), {})).toEqual([{ key: 'explorer', text: 'Sessions', current: true }])
  })
  it('prefers the label a blade registered, falls back to the strip label, and marks the last crumb current', () => {
    const l = crumbList(blades, { session: 'Fix the parser', turn: 'Turn 4' })
    expect(l.map((c) => c.text)).toEqual(['Sessions', 'Fix the parser', 'Turn 4'])
    expect(l.map((c) => c.current)).toEqual([false, false, true])
    expect(crumbList(blades, {})[1].text).toBe('Session')
  })
})
