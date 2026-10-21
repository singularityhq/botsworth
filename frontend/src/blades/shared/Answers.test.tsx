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

  it('lists background under tools and stalled under nothing running, in the tooltips of those items (SES-15, D55)', async () => {
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
    const none = screen.getByRole('button', { name: /nothing running/ })
    fireEvent.focus(none)
    const noneTip = await screen.findByRole('tooltip')
    expect(noneTip.textContent).toContain('Go to the longest stretch of: Nothing running')
    expect(noneTip.textContent).toContain('a finished task waited for an agent that was not running')
    expect(noneTip.textContent).toContain('6m')
    fireEvent.blur(none)
    fireEvent.focus(screen.getByRole('button', { name: /tools/ }))
    const toolTip = await screen.findByRole('tooltip')
    expect(toolTip.textContent).toContain('a background command ran')
    expect(toolTip.textContent).toContain('4m')
  })
})
