// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import type { TimeSplit } from '../../api/types'
import { Answers } from './Answers'

afterEach(cleanup)

class NoopResizeObserver {
  observe() {}
  disconnect() {}
}
globalThis.ResizeObserver ??= NoopResizeObserver as unknown as typeof ResizeObserver

const MIN = 60_000
const split: TimeSplit = {
  parts: [
    { state: 'model', substate: null, ms: 10 * MIN },
    { state: 'you', substate: 'ask', ms: 5 * MIN },
    { state: 'you', substate: 'permission', ms: 2 * MIN },
    { state: 'you', substate: 'reply', ms: 3 * MIN },
  ],
}

function mount() {
  render(<Answers cost={undefined} wallMs={20 * MIN} split={split} onJump={() => {}} />)
}

describe('Answers legend (SES-15, SES-26)', () => {
  it('shows one `you` item with the substates summed', () => {
    mount()
    const items = screen.getAllByRole('button').filter((b) => /waiting on you/.test(b.textContent ?? ''))
    expect(items).toHaveLength(1)
    expect(items[0].textContent).toContain('10m')
  })

  it('its tooltip lists the substates with their durations', async () => {
    mount()
    fireEvent.focus(screen.getByRole('button', { name: /waiting on you/ }))
    const tip = await screen.findByRole('tooltip')
    expect(tip.textContent).toContain('Go to the longest stretch of: Waiting on you')
    expect(tip.textContent).toContain('asked you a question')
    expect(tip.textContent).toContain('5m')
    expect(tip.textContent).toContain('waited for your approval')
    expect(tip.textContent).toContain('the next turn is your message')
  })

  it('lists background under tools, and shows stalled as its own item, listed first, outside nothing running (SES-15, D55, SES-26 D56)', async () => {
    const d55: TimeSplit = {
      parts: [
        { state: 'model', substate: null, ms: 10 * MIN },
        { state: 'tool', substate: null, ms: 1 * MIN },
        { state: 'tool', substate: 'background', ms: 4 * MIN },
        { state: 'none', substate: 'none', ms: 2 * MIN },
        { state: 'none', substate: 'stalled', ms: 6 * MIN },
      ],
    }
    render(<Answers cost={undefined} wallMs={23 * MIN} split={d55} onJump={() => {}} />)
    const items = screen.getAllByRole('button').map((b) => b.textContent)
    expect(items[0]).toMatch(/^stalled\s*6m$/) // first, though model (10m) is larger
    expect(items.find((t) => t!.startsWith('nothing running'))).toMatch(/2m$/) // stalled is not in it
    const stalled = screen.getByRole('button', { name: /^stalled/ })
    fireEvent.focus(stalled)
    const stalledTip = await screen.findByRole('tooltip')
    expect(stalledTip.textContent).toBe('Go to the longest stretch of: Stalled')
    fireEvent.blur(stalled)
    const none = screen.getByRole('button', { name: /nothing running/ })
    fireEvent.focus(none)
    expect((await screen.findByRole('tooltip')).textContent).toBe('Go to the longest stretch of: Nothing running') // no substate list
    fireEvent.blur(none)
    fireEvent.focus(screen.getByRole('button', { name: /tools/ }))
    const toolTip = await screen.findByRole('tooltip')
    expect(toolTip.textContent).toContain('a background command ran')
    expect(toolTip.textContent).toContain('4m')
  })
})

describe('Answers time line and split bar (SES-26 [D56])', () => {
  const withStall: TimeSplit = {
    parts: [
      { state: 'model', substate: null, ms: 10 * MIN },
      { state: 'you', substate: 'reply', ms: 3 * MIN },
      { state: 'limit', substate: null, ms: 1 * MIN },
      { state: 'none', substate: 'none', ms: 2 * MIN },
      { state: 'none', substate: 'stalled', ms: 6 * MIN },
    ],
  }

  it('reads "B not running", not "nothing running", on line 1', () => {
    render(<Answers cost={undefined} wallMs={22 * MIN} split={withStall} lostMs={6 * MIN} onOpenReview={() => {}} />)
    expect(screen.getByText(/not running/).textContent).toContain('10m running · 12m not running')
    expect(screen.queryByText(/\bnothing running\b.*\blost\b/)).toBeNull()
    expect(screen.getByRole('button', { name: '6m lost' })).toBeTruthy() // REV-2 follows "not running"
  })

  it('draws stalled as its own segment directly before none, filled with the REV-1 stalled fill', () => {
    render(<Answers cost={undefined} wallMs={22 * MIN} split={withStall} onJump={() => {}} />)
    const segs = Array.from(screen.getByRole('img', { name: 'Time split by state' }).querySelectorAll('i')) as HTMLElement[]
    expect(segs).toHaveLength(5) // model, you, limit, stalled, none
    const stalled = segs[3]
    expect(stalled.style.background).toContain('var(--color-error)') // hatched error fill (REV-1)
    expect(stalled.style.background).toContain('repeating-linear-gradient')
    expect(stalled.style.boxShadow).toBe('inset 0 0 0 1px var(--color-error)')
    expect(segs[4].style.background).toContain('var(--st-none)') // none follows, in its own fill
  })

  it('words the stalled segment tooltip "Stalled <dur>"', async () => {
    render(<Answers cost={undefined} wallMs={22 * MIN} split={withStall} onJump={() => {}} />)
    const seg = Array.from(screen.getByRole('img', { name: 'Time split by state' }).querySelectorAll('i'))[3]
    fireEvent.focus(seg.parentElement!)
    expect((await screen.findByRole('tooltip')).textContent).toBe('Stalled 6m')
  })

  it('jumps with the stalled key when the stalled legend item is clicked', () => {
    const keys: string[] = []
    render(<Answers cost={undefined} wallMs={22 * MIN} split={withStall} onJump={(k) => keys.push(k)} />)
    fireEvent.click(screen.getByRole('button', { name: /^stalled/ }))
    expect(keys).toEqual(['stalled'])
  })
})
