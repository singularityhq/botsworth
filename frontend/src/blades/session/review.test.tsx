// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fixtures } from '../../api/__fixtures__'
import type { ReviewReport } from '../../api/types'
import { responseCache } from '../../api/useApi'
import { CrumbProvider } from '../../shell/CrumbProvider'
import { useGlobalKeys } from '../../shell/keys'
import { StatusHost, StatusProvider } from '../shared/status'
import { SessionBlade } from './index'

const S = fixtures.sessionDetail.session.session
const R = fixtures.sessionReview as ReviewReport
const TURN2_GAP = 'gap-1791786480000'

function reply(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })
}

function serve(review: () => Response | Promise<Response> = () => reply(200, R)) {
  const calls: string[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => {
      calls.push(url)
      const u = new URL(url, 'http://x')
      if (u.pathname.endsWith('/review')) return review()
      if (u.pathname.endsWith('/turns')) return reply(200, fixtures.sessionTurns)
      if (u.pathname.endsWith('/histogram')) return reply(200, fixtures.sessionHistogram)
      if (u.pathname.endsWith('/find')) return reply(200, fixtures.sessionFind)
      return reply(200, fixtures.sessionDetail)
    }),
  )
  return calls
}

function Harness({ width }: { width: number }) {
  useGlobalKeys({ bladeKeys: ['session'], focusKey: 'session', setFocusIndex: () => {}, closeRightmost: () => {}, togglePalette: () => {} })
  return (
    <>
      <SessionBlade width={width} compact={false} />
      <StatusHost sessionOpen />
    </>
  )
}

function mount(query = '') {
  window.history.replaceState(null, '', `/?s=${encodeURIComponent(S)}${query}`)
  return render(
    <CrumbProvider>
      <StatusProvider>
        <Harness width={1004} />
      </StatusProvider>
    </CrumbProvider>,
  )
}

const params = () => new URLSearchParams(window.location.search)
const press = (key: string) => fireEvent.keyDown(document.body, { key })

class NoopObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}

beforeEach(() => {
  responseCache.clear()
  vi.stubGlobal('ResizeObserver', NoopObserver)
  Element.prototype.scrollIntoView = () => {}
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

const review = () => screen.getByLabelText('Review')

describe('REV-2 header claim', () => {
  it('shows the lost time from the review, with the tooltip target, when it is 60 s or more', async () => {
    serve()
    mount()
    const btn = await screen.findByRole('button', { name: '1h35m lost' })
    expect(btn.textContent).toBe('1h35m lost')
    fireEvent.click(btn)
    expect(params().get('sview')).toBe('review')
  })

  it('shows nothing below 60 s', async () => {
    const calls = serve(() => reply(200, { ...R, lost_ms: 59_000 }))
    mount()
    await screen.findByText('nothing running', { exact: false })
    await waitFor(() => expect(calls.some((c) => c.endsWith('/review'))).toBe(true))
    await act(async () => {}) // let the review response land
    expect(screen.queryByText('lost')).toBeNull()
  })

  it('is omitted when the review request fails', async () => {
    const calls = serve(() => reply(500, { error: 'boom', code: 'internal' }))
    mount()
    await screen.findByText('nothing running', { exact: false })
    await waitFor(() => expect(calls.some((c) => c.endsWith('/review'))).toBe(true))
    await act(async () => {})
    expect(screen.queryByText('lost')).toBeNull()
  })
})

describe('REV-3 view, keys and URL', () => {
  it('offers Ledger | Waterfall | Review in the view segment', async () => {
    serve()
    mount()
    const view = await screen.findByRole('group', { name: 'View' })
    expect(within(view).getAllByRole('button').map((b) => b.textContent)).toEqual(['Ledger', 'Waterfall', 'Review'])
    fireEvent.click(within(view).getByRole('button', { name: 'Review' }))
    expect(params().get('sview')).toBe('review')
  })

  it('r opens Review and r closes it back to Ledger', async () => {
    serve()
    mount()
    await screen.findByRole('button', { name: '1h35m lost' })
    press('r')
    expect(params().get('sview')).toBe('review')
    press('r')
    expect(params().get('sview')).toBeNull()
  })

  it('returns to Waterfall, not Ledger, when Review was opened from Waterfall (r, v and Esc)', async () => {
    serve()
    mount('&sview=waterfall')
    await screen.findByRole('button', { name: '1h35m lost' })
    press('r')
    expect(params().get('sview')).toBe('review')
    press('r')
    expect(params().get('sview')).toBe('waterfall')
    press('r')
    press('v')
    expect(params().get('sview')).toBe('waterfall')
    press('r')
    fireEvent.keyDown(document.body, { key: 'Escape' })
    expect(params().get('sview')).toBe('waterfall')
    expect(params().get('s')).toBe(S)
  })

  it('keeps v toggling Ledger and Waterfall when Review is closed', async () => {
    serve()
    mount()
    await screen.findByRole('button', { name: '1h35m lost' })
    press('v')
    expect(params().get('sview')).toBe('waterfall')
    press('v')
    expect(params().get('sview')).toBeNull()
  })

  it('closes Review on Esc before the session blade (SES-6 step 7a) and leaves the session open', async () => {
    serve()
    mount('&sview=review')
    await screen.findByText('Without the stalls')
    fireEvent.keyDown(document.body, { key: 'Escape' })
    expect(params().get('sview')).toBeNull()
    expect(params().get('s')).toBe(S)
  })

  it('does nothing on j, k, Enter and the arrows: no turn opens, no cursor moves', async () => {
    serve()
    mount('&sview=review')
    await screen.findByText('Without the stalls')
    const before = window.location.search
    for (const key of ['j', 'k', 't', 'T', 'Enter', 'ArrowDown', 'ArrowUp']) press(key)
    expect(window.location.search).toBe(before)
  })

  it('sets the view to Ledger and jumps when a finder key is pressed in Review', async () => {
    serve()
    mount('&sview=review')
    await screen.findByText('Without the stalls')
    await new Promise((r) => setTimeout(r, 50)) // the finder pools load
    press('e')
    await waitFor(() => expect(params().get('t')).not.toBeNull())
    expect(params().get('sview')).toBeNull()
  })

  it('opens from the URL sview=review under the header and the finder line', async () => {
    serve()
    mount('&sview=review')
    expect((await screen.findByText('3h37m wall. 1h35m was lost to stalls. The largest cause was stalled (1h35m).')).tagName).toBe('H2')
    expect(screen.getByRole('textbox')).toBeTruthy() // the finder line stays
    expect(screen.queryByText('Wait for it')).toBeNull()
  })
})

describe('REV-6 cards and the evidence link', () => {
  it('lists the cards in findings order', async () => {
    serve()
    mount('&sview=review')
    await screen.findByText('Without the stalls')
    const order = Array.from(review().querySelectorAll('[data-finding]')).map((e) => e.getAttribute('data-finding'))
    expect(order).toEqual(['stalled', 'tests', 'you', 'polling'])
  })

  it('shows the you card split by substate from the session time split, with no change line and no evidence line when longest is null', async () => {
    serve()
    mount('&sview=review')
    await screen.findByText('Without the stalls')
    const card = review().querySelector('[data-finding="you"]') as HTMLElement
    expect(within(card).getByText(/Waited 15m on you/)).toBeTruthy()
    expect(card.textContent).not.toContain('Longest')
    expect(card.textContent).not.toContain('Change')
  })

  it('opens the evidence row as a finder jump does and returns the view to Ledger', async () => {
    serve()
    mount('&sview=review')
    const link = await screen.findByRole('button', { name: /^Longest: .*1h02m · turn 2$/ })
    await waitFor(() => expect((link as HTMLButtonElement).disabled).toBe(false))
    fireEvent.click(link)
    expect(params().get('sview')).toBeNull()
    expect(params().get('t')).toBe('2')
    expect(params().get('step')).toBe(TURN2_GAP)
    expect(params().get('detail')).toBe('1')
  })

  it('sets the session view to Ledger as its own history step, so going back from the opened turn lands on Ledger, not Review', async () => {
    serve()
    mount('&sview=review')
    const link = await screen.findByRole('button', { name: /^Longest: .*1h02m · turn 2$/ })
    await waitFor(() => expect((link as HTMLButtonElement).disabled).toBe(false))
    fireEvent.click(link)
    expect(params().get('t')).toBe('2')
    window.history.back() // what closing the turn blade does after an opening step (SYS-50)
    await waitFor(() => expect(params().get('t')).toBeNull())
    expect(params().get('sview')).toBeNull()
    expect(params().get('s')).toBe(S)
  })

  it('swaps the open turn in place when a turn is already open', async () => {
    serve()
    mount('&sview=review&t=1')
    const link = await screen.findByRole('button', { name: /^Longest: .*1h02m · turn 2$/ })
    await waitFor(() => expect((link as HTMLButtonElement).disabled).toBe(false))
    fireEvent.click(link)
    expect(params().get('t')).toBe('2')
    expect(params().get('sview')).toBeNull()
  })
})

describe('REV-7 states', () => {
  it('shows skeletons while loading, then the claim', async () => {
    let release: (r: Response) => void = () => {}
    const gate = new Promise<Response>((r) => (release = r))
    serve(() => gate)
    mount('&sview=review')
    expect(await screen.findByRole('status', { name: 'Loading' })).toBeTruthy()
    expect(screen.queryByRole('heading', { level: 2 })).toBeNull()
    release(reply(200, R))
    expect(await screen.findByText('Without the stalls')).toBeTruthy()
    expect(screen.queryByRole('status', { name: 'Loading' })).toBeNull()
  })

  it('shows the error card in the Review area with a working retry', async () => {
    let fail = true
    serve(() => (fail ? reply(500, { error: 'boom', code: 'internal' }) : reply(200, R)))
    mount('&sview=review')
    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain("Couldn't load the review")
    fail = false
    fireEvent.click(within(alert).getByRole('button', { name: 'Retry' }))
    expect(await screen.findByText('Without the stalls')).toBeTruthy()
  })

  it('shows the empty state when there is no finding, and no stalled card for a session without stalls', async () => {
    const calm: ReviewReport = {
      ...R, lost_ms: 0, n_unmeasured: 1, findings: [],
      causes: R.causes.map((c) => (c.cause === 'stalled' ? { ...c, ms: 0 } : c)),
    }
    serve(() => reply(200, calm))
    mount('&sview=review')
    expect(await screen.findByText('No cause other than work took 2 min or more.')).toBeTruthy()
    expect(review().querySelector('[data-finding]')).toBeNull()
    expect(screen.getByText(/Stalls are not measured for Codex and omp sessions/)).toBeTruthy()
    expect(screen.queryByText('Without the stalls')).toBeNull()
    expect(screen.getByText(/Nothing was lost to a stall\./)).toBeTruthy()
  })

  it('draws no stalled card when the review has none, even with lost time elsewhere', async () => {
    serve(() => reply(200, { ...R, findings: R.findings.filter((f) => f.cause !== 'stalled') }))
    mount('&sview=review')
    await screen.findByText('Without the stalls')
    expect(review().querySelector('[data-finding="stalled"]')).toBeNull()
  })
})

describe('REV-5 rendering', () => {
  it('puts the labels of segments of 6 % or more in a line above the bar, in order, and none inside the fill', async () => {
    serve()
    mount('&sview=review')
    await screen.findByText('Without the stalls')
    const first = review().querySelector('[data-bar="As it ran"]') as HTMLElement
    const line = first.querySelector('[data-labels]') as HTMLElement
    expect(Array.from(line.querySelectorAll('[data-label]')).map((e) => e.textContent)).toEqual(['stalled', 'tests', 'waiting on you', 'working'])
    const fill = first.querySelector('[role="img"]') as HTMLElement
    expect(fill.textContent).toBe('')
  })

  it('draws the second bar at (wall - lost) / wall of the width, with segments that sum to it', async () => {
    serve()
    mount('&sview=review')
    await screen.findByText('Without the stalls')
    const ran = review().querySelector('[data-bar="As it ran"] [role="img"]') as HTMLElement
    const without = review().querySelector('[data-bar="Without the stalls"] [role="img"]') as HTMLElement
    expect(ran.style.width).toBe('100%')
    expect(parseFloat(without.style.width)).toBeCloseTo((R.wall_ms - R.lost_ms) / R.wall_ms * 100, 6)
    const sum = (el: HTMLElement) => Array.from(el.querySelectorAll('[data-ms]')).reduce((a, e) => a + Number(e.getAttribute('data-ms')), 0)
    expect(sum(ran)).toBe(R.wall_ms)
    expect(sum(without)).toBe(R.wall_ms - R.lost_ms)
  })

  it('puts the figure in a 120 px column (REV-6)', async () => {
    serve()
    mount('&sview=review')
    await screen.findByText('Without the stalls')
    expect((review().querySelector('[data-finding="stalled"]') as HTMLElement).className).toContain('grid-cols-[120px_minmax(0,1fr)]')
  })
})
