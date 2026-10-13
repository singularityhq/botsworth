// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fixtures } from '../../api/__fixtures__'
import type { FindResult, Histogram, TurnList } from '../../api/types'
import { responseCache } from '../../api/useApi'
import { CrumbProvider } from '../../shell/CrumbProvider'
import { useGlobalKeys } from '../../shell/keys'
import { closeTopOverlay } from '../../ui/overlay'
import { StatusHost, StatusProvider } from '../shared/status'
import { SessionBlade } from './index'
import { gap, turn } from './testRows'

const S = fixtures.sessionDetail.session.session
const T0 = fixtures.sessionDetail.session.start_ms // the turns lie inside the fixture session, so the Waterfall axis covers them
const MIN = 60_000
const T = (n: number) => `s/turn/${n}`

// Three numbered turns with a gap before turn 2 (nothing running, 1h) and one before turn 3 (you, 5 min).
const turns: TurnList = {
  freshness: fixtures.sessionTurns.freshness as TurnList['freshness'],
  items: [
    turn(1, { start_ms: T0, end_ms: T0 + 5 * MIN, wall_ms: 5 * MIN, cost_usd: 1, tok_total: 100, turn: T(1), label: 'first ask' }),
    gap(T0 + 5 * MIN, 60 * MIN, { turn: T(2), state: 'none', label: 'none' }),
    turn(2, { start_ms: T0 + 65 * MIN, end_ms: T0 + 66 * MIN, wall_ms: 1 * MIN, cost_usd: 9, tok_total: 50, turn: T(2), label: 'second ask' }),
    gap(T0 + 66 * MIN, 5 * MIN, { turn: T(3), state: 'none', label: 'you' }),
    turn(3, { start_ms: T0 + 71 * MIN, end_ms: T0 + 100 * MIN, wall_ms: 29 * MIN, cost_usd: 4, tok_total: 900, turn: T(3), label: 'third ask' }),
  ],
}

type H = FindResult['hits'][number]
const hit = (turnN: number, id: string, o: Partial<H> = {}): H => ({
  row: { type: 'tool', id }, turn: T(turnN), display_id: `T${turnN}`, depth: 0, label: id, value: null, start_ms: T0 + turnN * MIN, ...o,
})
const find = (kind: string, hits: H[]): FindResult => ({ freshness: fixtures.sessionFind.freshness as FindResult['freshness'], kind, total: hits.length, hits, per_turn: {} })

const FIND: Record<string, FindResult> = {
  waits: find('waits', [
    hit(2, 'gap-a', { row: { type: 'gap', id: 'gap-a' }, label: 'Nothing running', value: 60 * MIN }),
    hit(3, 'gap-b', { row: { type: 'gap', id: 'gap-b' }, label: 'Waiting on you', value: 5 * MIN }),
  ]),
  failures: find('failures', [hit(2, 'tool-x', { label: 'Bash: npm test' })]),
  skills: find('skills', []),
}

function reply(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })
}

function serve(o: { agents?: Response; histogram?: Histogram } = {}) {
  const calls: string[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => {
      calls.push(url)
      const u = new URL(url, 'http://x')
      if (u.pathname.endsWith('/turns')) return reply(200, turns)
      if (u.pathname.endsWith('/histogram')) return reply(200, o.histogram ?? fixtures.sessionHistogram)
      if (u.pathname.endsWith('/find')) {
        const kind = u.searchParams.get('kind')!
        if (kind === 'agents') return o.agents ?? reply(500, { error: 'boom', code: 'internal' })
        return reply(200, FIND[kind])
      }
      return reply(200, fixtures.sessionDetail)
    }),
  )
  return calls
}

/** The shell's key router around the blade, so keys reach the blade the way they do in the app. */
function Harness({ width = 1004 }: { width?: number }) {
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
        <Harness />
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

describe('finder chips (SES-31, D38 M8, D41 M8)', () => {
  it('shows each count once its request returns, hides a chip at 0, and shows the failed state for a failed request', async () => {
    serve()
    mount()
    expect(await screen.findByRole('button', { name: /^2\s*waits\s*w$/ })).toBeTruthy()
    expect(screen.getByRole('button', { name: /^1\s*failed\s*e$/ })).toBeTruthy()
    expect(screen.queryByRole('button', { name: /skill loads?\s*s$/ })).toBeNull() // count 0: hidden
    // agents: the request failed, so the chip has the word and key but no count
    const agents = await screen.findByRole('button', { name: /^agents\s*a$/ })
    expect(within(agents).queryByText(/^\d+$/)).toBeNull()
    expect(screen.getByRole('button', { name: /Biggest/ })).toBeTruthy()
  })

  it('a click on the failed chip asks for that finder again', async () => {
    const calls = serve()
    mount()
    const agentsChip = () => screen.getByRole('button', { name: /^agents\s*a$/ })
    await waitFor(() => expect(agentsChip().querySelector('.lucide-circle-alert')).not.toBeNull()) // the request has failed
    const agents = agentsChip()
    const before = calls.filter((u) => u.includes('kind=agents')).length
    fireEvent.click(agents)
    await waitFor(() => expect(calls.filter((u) => u.includes('kind=agents')).length).toBe(before + 1))
  })

  it('counts only rows the search matches (SES-51)', async () => {
    serve()
    // the search matches the second wait only
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        const u = new URL(url, 'http://x')
        if (u.pathname.endsWith('/turns')) return reply(200, turns)
        if (u.pathname.endsWith('/find')) {
          const kind = u.searchParams.get('kind')!
          if (kind === 'search') return reply(200, { ...find('search', [hit(3, 'gap-b', { row: { type: 'gap', id: 'gap-b' } })]), per_turn: { [T(3)]: 1 } })
          if (kind === 'agents') return reply(500, { error: 'boom', code: 'internal' })
          return reply(200, FIND[kind])
        }
        return reply(200, fixtures.sessionDetail)
      }),
    )
    mount('&find=wait')
    expect(await screen.findByRole('button', { name: /^1\s*wait\s*w$/ })).toBeTruthy()
    expect(screen.queryByRole('button', { name: /failed\s*e$/ })).toBeNull()
  })
})

describe('finder keys (SES-50 to SES-57)', () => {
  it('w from the session blade opens the turn that holds the next wait, selects the row, opens the detail and toasts', async () => {
    serve()
    mount()
    await screen.findByRole('button', { name: /^2\s*waits\s*w$/ })
    press('w')
    // the cursor starts on turn 1; the first wait at or after turn 1 is the one in turn 2
    expect(params().get('t')).toBe('2')
    expect(params().get('step')).toBe('gap-a')
    expect(params().get('detail')).toBe('1')
    expect((await screen.findByRole('status')).textContent).toBe('Wait 1 of 2: Nothing running · turn 2 · 1h00m')
  })

  it('the row name in a finder toast is bold prose, not a mono figure; the duration of a wait is a figure', async () => {
    serve()
    mount()
    await screen.findByRole('button', { name: /^2\s*waits\s*w$/ })
    press('w')
    const bolds = Array.from((await screen.findByRole('status')).querySelectorAll('b'))
    expect(bolds.map((b) => [b.textContent, b.classList.contains('fig')])).toEqual([['Nothing running', false], ['1h00m', true]])
  })

  it('e opens the failed tool without opening the detail', async () => {
    serve()
    mount()
    await screen.findByRole('button', { name: /^1\s*failed\s*e$/ })
    press('e')
    expect([params().get('t'), params().get('step'), params().get('detail')]).toEqual(['2', 'tool-x', null])
    expect((await screen.findByRole('status')).textContent).toBe('Failure 1 of 1: Bash: npm test · turn 2')
  })

  it('a finder with no hit toasts and moves nothing', async () => {
    serve()
    mount()
    await screen.findByRole('button', { name: /^2\s*waits\s*w$/ })
    press('s')
    expect((await screen.findByRole('status')).textContent).toBe('No skill load in this session.')
    expect(params().get('t')).toBeNull()
  })

  it('a click on a chip does what its key does', async () => {
    serve()
    mount()
    fireEvent.click(await screen.findByRole('button', { name: /^1\s*failed\s*e$/ }))
    expect(params().get('step')).toBe('tool-x')
  })

  it('b selects the biggest turn by cost (cursor only), then the next; changing the measure starts again from the biggest', async () => {
    serve()
    mount()
    await screen.findByText('second ask')
    const row = (label: string) => screen.getByText(label).closest('[data-turn-key]') as HTMLElement
    press('b')
    expect((await screen.findByRole('status')).textContent).toBe('Biggest turn by cost: $9.00 · turn 2')
    expect(row('second ask').getAttribute('data-cursor')).toBe('true')
    expect(params().get('t')).toBeNull() // Enter opens it; b only selects
    press('b')
    await waitFor(() => expect(screen.getByRole('status').textContent).toBe('2nd biggest turn by cost: $4.00 · turn 3'))
    expect(row('third ask').getAttribute('data-cursor')).toBe('true')
    press('2')
    press('b')
    await waitFor(() => expect(screen.getByRole('status').textContent).toBe('Biggest turn by time: 29m · turn 3'))
  })

  it('B before any b selects the biggest', async () => {
    serve()
    mount()
    await screen.findByText('second ask')
    press('B')
    expect((await screen.findByRole('status')).textContent).toBe('Biggest turn by cost: $9.00 · turn 2')
  })

  it('v switches the view in the URL', async () => {
    serve()
    mount()
    await screen.findByText('second ask')
    press('v')
    expect(params().get('sview')).toBe('waterfall')
    press('v')
    expect(params().get('sview')).toBeNull()
  })
})

describe('keys overlay (SES-72)', () => {
  it('? opens it, ? closes it, and Esc closes it', async () => {
    serve()
    mount()
    await screen.findByText('second ask')
    press('?')
    expect(screen.getByRole('dialog', { name: 'Keyboard' })).toBeTruthy()
    expect(screen.getByText('next / previous skill load, across turns')).toBeTruthy()
    press('?')
    expect(screen.queryByRole('dialog')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Keyboard' }))
    expect(screen.getByRole('dialog', { name: 'Keyboard' })).toBeTruthy()
    act(() => void closeTopOverlay()) // what the router does on Esc
    expect(screen.queryByRole('dialog')).toBeNull()
  })
})

describe('status bar (SES-70)', () => {
  it('shows "Turn N selected" for the cursor turn', async () => {
    serve()
    mount()
    await screen.findByText('second ask')
    expect(await screen.findByText('Turn 1 selected')).toBeTruthy()
    press('j')
    expect(await screen.findByText('Turn 2 selected')).toBeTruthy()
  })
})

describe('Over time and the Waterfall', () => {
  it('Over time shows the caption and an aria-label with the range and totals; the failed fetch shows a card', async () => {
    serve()
    mount('&over=1')
    expect(await screen.findByText('Drag across to show the turns in that range')).toBeTruthy()
    expect(screen.getByRole('img', { name: /^Over time/ })).toBeTruthy()
  })

  it('the Waterfall folds the 1 h wait and the 5 min wait into one merged mark, names the axis, and Real time shows no fold', async () => {
    serve()
    Object.defineProperty(HTMLElement.prototype, 'clientWidth', { configurable: true, get: () => 800 })
    mount('&sview=waterfall')
    expect(await screen.findByText('Waits of 2 min or more folded')).toBeTruthy()
    // on a 25 h axis the two waits end and start 1 min apart, under 10 px: one merged mark in the head, none in the rows
    expect(await screen.findByText('≈×2')).toBeTruthy()
    expect(screen.getAllByText(/≈/)).toHaveLength(1)
    fireEvent.click(screen.getByRole('button', { name: 'Real time' }))
    expect(params().get('sreal')).toBe('1')
    await waitFor(() => expect(screen.getAllByText('Real time')).toHaveLength(2)) // the segment and the axis caption
    expect(screen.queryByText(/≈/)).toBeNull()
    delete (HTMLElement.prototype as { clientWidth?: number }).clientWidth
  })

  it('Waterfall rows open turns like the Ledger rows do', async () => {
    serve()
    Object.defineProperty(HTMLElement.prototype, 'clientWidth', { configurable: true, get: () => 800 })
    mount('&sview=waterfall')
    const row = (await screen.findByText('second ask')).closest('[data-turn-key]') as HTMLElement
    fireEvent.click(row)
    expect(params().get('t')).toBe('2')
    delete (HTMLElement.prototype as { clientWidth?: number }).clientWidth
  })
})

describe('range cost (SES-13, D47)', () => {
  it('with a range set the cost answer is the clock-rule sum of the bins, and the caption says agent calls in range', async () => {
    serve()
    const h = fixtures.sessionHistogram
    // bins 1 and 2 of the fixture: cost 0.1 + 0.2, main 0.05 + 0.1
    const range = `${h.start_ms + h.bin_ms}-${h.start_ms + 3 * h.bin_ms}`
    mount(`&range=${range}`)
    expect(await screen.findByText('$0.30')).toBeTruthy()
    expect(screen.getByText('agent calls in range')).toBeTruthy()
    expect(screen.queryByText('$12.40')).toBeNull() // the session's own cost is not shown
  })

  it('without a range the session cost and "agents it started" show', async () => {
    serve()
    mount()
    expect(await screen.findByText('$12.40')).toBeTruthy()
    expect(screen.getByText('agents it started')).toBeTruthy()
  })
})
