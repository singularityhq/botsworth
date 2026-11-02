// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fixtures } from '../../api/__fixtures__'
import { responseCache } from '../../api/useApi'
import { CrumbProvider } from '../../shell/CrumbProvider'
import { SessionBlade } from './index'

const S = fixtures.sessionDetail.session.session
const TURN2 = 'claude_code/a11ce5e5-1111-4222-8333-944455556666/turn/2'

function reply(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })
}

/** A fetch that serves the wire fixtures, for the three endpoints F3a calls. */
function serve(overrides: Partial<{ detail: Response; search: (text: string) => unknown }> = {}) {
  const calls: string[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => {
      calls.push(url)
      const u = new URL(url, 'http://x')
      if (u.pathname.endsWith('/turns')) return reply(200, fixtures.sessionTurns)
      if (u.pathname.endsWith('/find')) {
        const text = u.searchParams.get('text') ?? ''
        const body = overrides.search ? overrides.search(text) : { ...fixtures.sessionFind, kind: 'search', total: 1, per_turn: { [TURN2]: 3 } }
        return reply(200, body)
      }
      return overrides.detail ?? reply(200, fixtures.sessionDetail)
    }),
  )
  return calls
}

function mount(query: string, props: { width?: number; compact?: boolean } = {}) {
  window.history.replaceState(null, '', `/?${query}`)
  return render(
    <CrumbProvider>
      <SessionBlade width={props.width ?? 1004} compact={props.compact ?? false} />
    </CrumbProvider>,
  )
}

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
})

describe('SessionBlade with the F1a fixtures', () => {
  it('shows the meta line, title, cost and the time answer', async () => {
    serve()
    mount(`s=${encodeURIComponent(S)}`)
    expect(await screen.findByRole('heading', { name: 'Fix rbac sync' })).toBeTruthy()
    expect(screen.getByText('bizdemo')).toBeTruthy()
    expect(screen.getByText('$12.40')).toBeTruthy()
    expect(screen.getByText('9m')).toBeTruthy() // 525000 ms wall
    // legend: states of 30 s or more, largest first
    const legend = screen.getAllByRole('button').filter((b) => /^(agents|model|tools|waiting on you|usage limit)/.test(b.textContent ?? ''))
    expect(legend.map((b) => b.textContent)).toEqual(['agents5m', 'model2m', 'tools45s', 'waiting on you30s', 'usage limit30s'].slice(0, 4))
  })

  it('lists a gap row above its turn and a harness fold, in time order', async () => {
    serve()
    mount(`s=${encodeURIComponent(S)}`)
    await screen.findByText('Usage limit (inferred)')
    const gap = screen.getByText('Usage limit (inferred)').closest('[data-gap-key]') as HTMLElement
    const turn = screen.getByText('I hit my usage limit. Please continue').closest('[data-turn-key]') as HTMLElement
    expect(within(gap).getAllByText('1h02m')).toHaveLength(2) // the name and the Wall column
    expect(within(gap).getByText('the next turn resumes after a usage limit')).toBeTruthy()
    expect(gap.compareDocumentPosition(turn) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(screen.getByText('1 session event')).toBeTruthy()
  })

  it('clicking a gap row opens its turn with the gap as the row and the detail open', async () => {
    serve()
    mount(`s=${encodeURIComponent(S)}`)
    const gap = (await screen.findByText('Usage limit (inferred)')).closest('[data-gap-key]') as HTMLElement
    fireEvent.click(gap)
    const q = new URLSearchParams(window.location.search)
    expect(q.get('t')).toBe('2')
    expect(q.get('step')).toBe('gap-1791786480000')
    expect(q.get('detail')).toBe('1')
  })

  it('a legend click opens the turn that holds the longest stretch and toasts it', async () => {
    // The fixture's two waits tie at 30 s and only four states fit; make the limit the larger so it is listed.
    const detail = structuredClone(fixtures.sessionDetail)
    detail.session.time_split = {
      parts: [
        { state: 'model', substate: null, ms: 120000 }, { state: 'tool', substate: null, ms: 45000 }, { state: 'agents', substate: null, ms: 300000 },
        { state: 'you', substate: 'reply', ms: 20000 }, { state: 'limit', substate: null, ms: 40000 },
      ],
    }
    serve({ detail: reply(200, detail) })
    mount(`s=${encodeURIComponent(S)}`)
    const btn = await screen.findByRole('button', { name: /^usage limit/ })
    await waitFor(() => expect((btn as HTMLButtonElement).disabled).toBe(false)) // enabled once the turn list has loaded
    fireEvent.click(btn)
    const q = new URLSearchParams(window.location.search)
    expect([q.get('t'), q.get('step'), q.get('detail')]).toEqual(['2', 'gap-1791786480000', '1'])
    expect((await screen.findByRole('status')).textContent).toBe('Longest stretch of usage limit: 1h02m at 11:58 · turn 2')
  })

  it('a legend click on a state with no 2 min stretch says so and navigates nowhere', async () => {
    serve()
    mount(`s=${encodeURIComponent(S)}`)
    const btn = await screen.findByRole('button', { name: /^waiting on you/ })
    await waitFor(() => expect((btn as HTMLButtonElement).disabled).toBe(false))
    fireEvent.click(btn)
    expect((await screen.findByRole('status')).textContent).toBe('No single stretch of waiting on you is 2 min or longer; the total is many short pauses.')
    expect(new URLSearchParams(window.location.search).get('t')).toBeNull()
  })

  it('search keeps matching turns, writes the URL on each keystroke, and shows the no-match state', async () => {
    serve({ search: (text) => (text === 'zzz' ? { ...fixtures.sessionFind, kind: 'search', total: 0, per_turn: {} } : { ...fixtures.sessionFind, kind: 'search', total: 3, per_turn: { [TURN2]: 3 } }) })
    mount(`s=${encodeURIComponent(S)}&find=pytest`)
    expect(await screen.findByText('Turns with “pytest”')).toBeTruthy()
    // gap rows and folds leave the list; the turn shows its match count
    await waitFor(() => expect(screen.queryByText('Usage limit (inferred)')).toBeNull())
    const row = screen.getByText('I hit my usage limit. Please continue').closest('[data-turn-key]') as HTMLElement
    expect(within(row).getByText('3')).toBeTruthy()
    const input = screen.getByRole('textbox', { name: 'Find a step' })
    fireEvent.change(input, { target: { value: 'zzz' } })
    expect(new URLSearchParams(window.location.search).get('find')).toBe('zzz')
    expect(await screen.findByText('No turn has a step matching “zzz”.')).toBeTruthy()
  })

  it('Enter in the search field moves the cursor to the first kept turn', async () => {
    serve()
    mount(`s=${encodeURIComponent(S)}&find=a`)
    const input = await screen.findByRole('textbox', { name: 'Find a step' })
    await screen.findByText('I hit my usage limit. Please continue')
    fireEvent.keyDown(input, { key: 'Enter' })
    const row = screen.getByText('I hit my usage limit. Please continue').closest('[data-turn-key]') as HTMLElement
    expect(row.getAttribute('data-cursor')).toBe('true')
  })

  it('opens Details with the model table, the info line and no unattributed line when it is $0', async () => {
    serve()
    mount(`s=${encodeURIComponent(S)}&info=1`)
    expect(await screen.findByText('Model time for Claude Code is taken from log record times, so speed is approximate.')).toBeTruthy()
    expect(screen.getByText('55 tok/s')).toBeTruthy()
    expect(screen.queryByText(/Agent work with no turn/)).toBeNull()
  })

  it('renders the rail at 400 px with the open turn marked', async () => {
    serve()
    mount(`s=${encodeURIComponent(S)}&t=2`, { width: 400, compact: true })
    const row = (await screen.findByText('I hit my usage limit. Please continue')).closest('[data-turn-key]') as HTMLElement
    expect(row.getAttribute('data-open')).toBe('true')
    expect(screen.queryByText('Usage limit (inferred)')).toBeNull() // gap rows are hidden in the rail
    expect(screen.queryByText('1 session event')).toBeNull()
  })

  it('the rail rows carry the measure bar (SYS-36 mark, SYS-39 bar), scaled to the costliest turn', async () => {
    serve()
    mount(`s=${encodeURIComponent(S)}&t=2`, { width: 400, compact: true })
    await screen.findByText('I hit my usage limit. Please continue')
    const bars = Array.from(screen.getByLabelText('Turns').querySelectorAll('i')) as HTMLElement[]
    expect(bars.length).toBeGreaterThan(0)
    const widths = bars.map((b) => parseFloat(b.style.width))
    expect(Math.max(...widths)).toBe(64) // the largest turn fills the 64 px track
  })

  it('the rail follows the open turn by scrolling its own list, never an ancestor (the blade strip)', async () => {
    serve()
    const spy = vi.fn()
    Element.prototype.scrollIntoView = spy
    mount(`s=${encodeURIComponent(S)}&t=2`, { width: 400, compact: true })
    await screen.findByText('I hit my usage limit. Please continue')
    await act(async () => {})
    expect(spy).not.toHaveBeenCalled()
  })

  it('the rail re-reveals the open row when layout changes after the first reveal, until the user scrolls it', async () => {
    serve()
    const callbacks: Array<() => void> = []
    class CaptureObserver {
      constructor(cb: () => void) {
        callbacks.push(cb)
      }
      observe() {}
      unobserve() {}
      disconnect() {}
    }
    vi.stubGlobal('ResizeObserver', CaptureObserver)
    mount(`s=${encodeURIComponent(S)}&t=2`, { width: 400, compact: true })
    const row = (await screen.findByText('I hit my usage limit. Please continue')).closest('[data-turn-key]') as HTMLElement
    const box = screen.getByLabelText('Turns')
    // The open row sits 40 px below the box after a late layout change (font swap, strip transition).
    const rect = (top: number, bottom: number) => ({ top, bottom, left: 0, right: 0, width: 0, height: bottom - top, x: 0, y: top, toJSON() {} }) as DOMRect
    vi.spyOn(box, 'getBoundingClientRect').mockReturnValue(rect(0, 100))
    vi.spyOn(row, 'getBoundingClientRect').mockReturnValue(rect(80, 140))
    box.scrollTop = 0
    act(() => callbacks.forEach((cb) => cb()))
    expect(box.scrollTop).toBe(40)
    // After a user wheel scroll the rail stops following layout changes.
    fireEvent.wheel(box)
    box.scrollTop = 0
    act(() => callbacks.forEach((cb) => cb()))
    expect(box.scrollTop).toBe(0)
  })

  it('a session that does not exist shows the not-found state', async () => {
    serve({ detail: reply(404, { error: 'no such session', code: 'not_found' }) })
    mount('s=nope')
    expect(await screen.findByText(/Couldn't find session nope/)).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Back to sessions' })).toBeTruthy()
  })

  it('a failed turn list shows an error card with Retry and leaves the header', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => (url.endsWith('/turns') ? reply(500, { error: 'boom', code: 'internal' }) : reply(200, fixtures.sessionDetail))),
    )
    mount(`s=${encodeURIComponent(S)}`)
    expect(await screen.findByText("Couldn't load turns")).toBeTruthy()
    expect(screen.getByRole('heading', { name: 'Fix rbac sync' })).toBeTruthy()
    await act(async () => {})
  })
})
