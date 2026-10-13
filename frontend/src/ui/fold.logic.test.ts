import { describe, expect, it } from 'vitest'
import { foldRows } from './fold.logic'

const rows = Array.from({ length: 12 }, (_, i) => ({ id: i + 1, cost: (i + 1) * 1.5 }))

describe('foldRows (SYS-38)', () => {
  it('shows 5 of 12 rows with "Show 7 more" and the sum of rows 6 to 12', () => {
    const f = foldRows(rows, 5, false, (r) => r.cost)
    expect(f.visible.map((r) => r.id)).toEqual([1, 2, 3, 4, 5])
    expect(f.label).toBe('Show 7 more')
    expect(f.sum).toBe(rows.slice(5).reduce((a, r) => a + r.cost, 0))
    expect(f.sum).toBe(94.5)
  })
  it('reads "Show top 5" with no sum once expanded', () => {
    const f = foldRows(rows, 5, true, (r) => r.cost)
    expect(f.visible).toHaveLength(12)
    expect(f.label).toBe('Show top 5')
    expect(f.sum).toBeNull()
  })
  it('has no fold row when the list fits', () => {
    const f = foldRows(rows.slice(0, 5), 5, false, (r) => r.cost)
    expect(f.label).toBe('')
    expect(f.hidden).toEqual([])
  })
  it('skips rows without a figure and returns null when none has one', () => {
    const f = foldRows([1, 2, 3], 1, false, (n) => (n === 2 ? 4 : null))
    expect(f.sum).toBe(4)
    expect(foldRows([1, 2, 3], 1, false, () => null).sum).toBeNull()
  })
})
