import { describe, expect, it } from 'vitest'
import { presentProviders, visibleFacets } from './filterPopover.logic'

const rows = [{ value: 'opus', n: 150 }, { value: 'gpt', n: 12 }, { value: 'haiku', n: 0 }, { value: 'old', n: 0 }]

describe('visibleFacets (EXP-22, EXP-23)', () => {
  it('hides zero-count values behind "Show all (n)" and counts them', () => {
    const l = visibleFacets(rows, [], false)
    expect(l.rows.map((r) => r.value)).toEqual(['opus', 'gpt'])
    expect(l.hiddenCount).toBe(2)
  })
  it('shows every value once Show all is on', () => {
    expect(visibleFacets(rows, [], true).rows).toHaveLength(4)
  })
  it('keeps a selected zero-count value visible so it can be unselected', () => {
    const l = visibleFacets(rows, ['old'], false)
    expect(l.rows.map((r) => r.value)).toEqual(['opus', 'gpt', 'old'])
    expect(l.hiddenCount).toBe(1)
  })
  it('type-ahead narrows by substring, case-insensitive, and counts only hidden matches', () => {
    const l = visibleFacets(rows, [], false, 'O')
    expect(l.rows.map((r) => r.value)).toEqual(['opus'])
    expect(l.hiddenCount).toBe(1)
  })
})

describe('presentProviders (EXP-21)', () => {
  const f = { providers: [{ value: 'claude_code', n: 180 }, { value: 'codex', n: 20 }, { value: 'omp', n: 0 }] } as never
  it('lists the providers with sessions in the period', () => {
    expect(presentProviders(f, []).map((p) => p.value)).toEqual(['claude_code', 'codex'])
  })
  it('keeps a selected provider that has none', () => {
    expect(presentProviders(f, ['omp']).map((p) => p.value)).toEqual(['claude_code', 'codex', 'omp'])
  })
})
