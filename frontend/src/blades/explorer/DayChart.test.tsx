// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { DayBar, SessionRow } from '../../api/types'
import { chartWindow, resolvePeriod, type Period } from '../../state/period'
import { DayChart } from './DayChart'
import { buildColumns } from './dayChart.logic'

afterEach(cleanup)

const period: Period = { kind: 'preset', preset: 'last7' }
const bar = (day: string, a: number, b: number): DayBar => ({
  day, from_day: day, to_day: day, cost_usd: a + b, cost_lower_bound: false, n_unpriced: 0, tok_total: (a + b) * 1000, n_sessions: 2,
  by_project: [
    { project: '/p/a', project_name: 'a', color_slot: 1, cost_usd: a, tok_total: a * 1000 },
    { project: '/p/b', project_name: 'b', color_slot: 2, cost_usd: b, tok_total: b * 1000 },
  ],
})
const columns = buildColumns(chartWindow(period, '2026-10-12'), [bar('2026-10-07', 300, 100), bar('2026-10-08', 150, 50)], resolvePeriod(period, '2026-10-12'), 'cost')
const session = { color_slot: 2, day_split: [{ day: '2026-10-07', cost_usd: 60, tok_total: 6000 }, { day: '2026-10-08', cost_usd: 40, tok_total: 4000 }] } as SessionRow

const props = { columns, measure: 'cost' as const, hoverSession: null, hoverSlot: undefined, draft: null, onDraft: () => {}, onSelect: () => {} }

describe('DayChart', () => {
  it('outlines each day the hovered session made calls in, and draws its share box (EXP-40)', () => {
    const { container, rerender } = render(<DayChart {...props} />)
    expect(container.querySelectorAll('.border-struct')).toHaveLength(0)
    rerender(<DayChart {...props} hoverSession={session} />)
    expect(container.querySelectorAll('.border-struct')).toHaveLength(2)
    expect(container.querySelectorAll('span.border-ink1')).toHaveLength(2)
    rerender(<DayChart {...props} hoverSession={null} />)
    expect(container.querySelectorAll('.border-struct')).toHaveLength(0)
  })

  it('dims the segments of every other project to 0.28 while a legend project is hovered (EXP-41)', () => {
    const { container } = render(<DayChart {...props} hoverSlot={1} />)
    const bars = [...container.querySelectorAll<HTMLElement>('[data-col] i.block')]
    expect(bars.length).toBeGreaterThan(0)
    const dimmed = bars.filter((b) => b.style.opacity === '0.28')
    const kept = bars.filter((b) => b.style.opacity === '1')
    expect(dimmed).toHaveLength(2) // slot 2 on two days
    expect(kept).toHaveLength(2) // slot 1 on two days
  })

  it('draws the value label on the highest day in the period only (EXP-36)', () => {
    const { container } = render(<DayChart {...props} />)
    const labels = [...container.querySelectorAll('span.font-mono.text-ink2')].map((e) => e.textContent)
    expect(labels).toEqual(['$400.00'])
  })

  it('selects the dragged range on release, and calls it a click on the same column (EXP-38)', () => {
    const onSelect = vi.fn()
    const onDraft = vi.fn()
    render(<DayChart {...props} onSelect={onSelect} onDraft={onDraft} />)
    const a = screen.getByRole('button', { name: /^Wed Oct 7,/ })
    fireEvent.pointerDown(a, { button: 0 })
    fireEvent.pointerUp(window)
    expect(onSelect).toHaveBeenLastCalledWith({ from: '2026-10-07', to: '2026-10-07' })
  })

  it('moves focus with the arrows, wrapping nothing at the ends, and Enter selects the focused day (EXP-38a)', () => {
    const onSelect = vi.fn()
    render(<DayChart {...props} onSelect={onSelect} />)
    const first = screen.getAllByRole('button').find((b) => b.getAttribute('tabindex') === '0')!
    fireEvent.keyDown(first, { key: 'ArrowRight' })
    expect(document.activeElement).not.toBe(first)
    fireEvent.keyDown(document.activeElement!, { key: 'Enter' })
    expect(onSelect).toHaveBeenCalledTimes(1)
  })

  it('keeps exactly one day column in the tab order', () => {
    render(<DayChart {...props} />)
    expect(screen.getAllByRole('button').filter((b) => b.getAttribute('tabindex') === '0')).toHaveLength(1)
  })

  it('survives its columns shrinking under a stored hover index', () => {
    const p30: Period = { kind: 'custom', from: '2026-09-13', to: '2026-10-12' }
    const wide = buildColumns(chartWindow(p30, '2026-10-12'), [bar('2026-10-07', 300, 100)], resolvePeriod(p30, '2026-10-12'), 'cost')
    const { rerender } = render(<DayChart {...props} columns={wide} />)
    const last = screen.getAllByRole('button').at(-1)!
    fireEvent.pointerEnter(last)
    expect(screen.getByRole('tooltip')).toBeTruthy()
    rerender(<DayChart {...props} columns={columns.slice(0, 2)} />)
    expect(screen.queryByRole('tooltip')).toBeNull()
    expect(screen.getAllByRole('button').filter((b) => b.getAttribute('tabindex') === '0')).toHaveLength(1)
  })
})
