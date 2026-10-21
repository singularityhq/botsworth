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
})
