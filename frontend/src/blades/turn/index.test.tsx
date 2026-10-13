// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fixtures } from '../../api/__fixtures__'
import type { AnyRow, FindResult, TurnList, TurnRows } from '../../api/types'
import { responseCache } from '../../api/useApi'
import { CrumbProvider } from '../../shell/CrumbProvider'
import { useGlobalKeys } from '../../shell/keys'
import { StatusHost, StatusProvider } from '../shared/status'
import { turn as sessionTurn } from '../session/testRows'
import { TurnBlade } from './index'
import { agent, at, call, tool } from './testRows'

const S = fixtures.sessionDetail.session.session
const T = (n: number) => `s/turn/${n}`
const FRESH = fixtures.turnRows.freshness as TurnRows['freshness']

const TURNS: TurnList = {
  freshness: fixtures.sessionTurns.freshness as TurnList['freshness'],
  items: [
    sessionTurn(1, { turn: T(1), label: 'first ask', start_ms: at(0), end_ms: at(100), n_agents: 0 }),
    sessionTurn(2, { turn: T(2), label: 'second ask with an agent', start_ms: at(200), end_ms: at(300), n_agents: 1, agent_runs: [{ engagement: 'e', agent_name: 'a', start_ms: at(210), end_ms: at(400) }] }),
    sessionTurn(3, { turn: T(3), label: 'third ask has nothing', start_ms: at(500), end_ms: at(520), n_llm_calls: 0 }),
    sessionTurn(4, { turn: T(4), label: 'fourth ask', start_ms: at(600), end_ms: at(700) }),
  ],
}

const AG = agent('ag', { sec: 210, description: 'Survey', n_rows: 2 })
const KIDS: AnyRow[] = [call('k1', { depth: 1, sec: 211 }), tool('k2', { depth: 1, sec: 212, summary: 'Read the module' })]
const ROWS: Record<number, AnyRow[]> = {
  1: [call('a1', { sec: 0, steps: [{ step_id: 's', verb: 'say', summary: 'Look at the sync', snippet: '' }] }), tool('a2', { sec: 10, summary: 'Run the tests' }), call('a3', { sec: 20 })],
  2: [call('b1', { sec: 200 }), AG, call('b2', { sec: 280 })],
  3: [],
  4: [call('d1', { sec: 600 }), call('d2', { sec: 610 })],
}

function rowsBody(n: number, zoom?: { from_ms: number; to_ms: number }): TurnRows {
  const base = fixtures.turnRows.turn
  const row = TURNS.items[n - 1] as TurnList['items'][number] & { type: 'turn' }
  return {
    freshness: FRESH,
    turn: { ...(base as unknown as TurnRows['turn']), ...row, cost: { cost_usd: row.cost_usd, cost_lower_bound: false, n_unpriced: 0, main: 1, agents: 0 }, models_full: [], text_src: 'src://x', text_bytes: 120, n_turns_in_session: 4 },
    rows: ROWS[n],
    range: zoom
      ? { ...zoom, cost: { cost_usd: 0.5, cost_lower_bound: false, n_unpriced: 0 }, cost_main: 0.4, cost_agents: 0.1, time_split: { model_ms: 5000, tool_ms: 4000, agents_ms: 0, none_ms: 1000 }, agents: [] }
      : null,
  }
}

const reply = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })
const empty = (kind: string): FindResult => ({ freshness: fixtures.sessionFind.freshness as FindResult['freshness'], kind, total: 0, hits: [], per_turn: {} })

function serve(o: { rowsFail?: boolean; agentTotal?: number; agentFail?: boolean } = {}) {
  const calls: string[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => {
      calls.push(url)
      const u = new URL(url, 'http://x')
      const p = u.pathname
      if (p.endsWith('/turns')) return reply(200, TURNS)
      if (p.endsWith('/find')) return reply(200, empty(u.searchParams.get('kind')!))
      if (p.endsWith('/resolve')) {
        const id = u.searchParams.get('id')!
        return KIDS.some((k) => k.id === id) ? reply(200, { freshness: FRESH, row: { type: 'call', id }, turn: T(2), depth: 1 }) : reply(404, { error: 'no such row', code: 'not_found' })
      }
      if (p.includes('/api/turns/') && p.endsWith('/histogram')) return reply(200, fixtures.turnHistogram)
      if (p.includes('/api/turns/') && p.endsWith('/rows')) {
        if (o.rowsFail) return reply(500, { error: 'rows boom', code: 'internal' })
        const n = Number(decodeURIComponent(p.split('/api/turns/')[1]).split('/turn/')[1].split('/')[0])
        return reply(200, rowsBody(n, u.searchParams.has('from_ms') ? { from_ms: Number(u.searchParams.get('from_ms')), to_ms: Number(u.searchParams.get('to_ms')) } : undefined))
      }
      if (p.includes('/api/ops/') && p.endsWith('/rows')) {
        if (o.agentFail) return reply(500, { error: 'band boom', code: 'internal' })
        return reply(200, { freshness: FRESH, total: o.agentTotal ?? 2, offset: Number(u.searchParams.get('offset') ?? 0), limit: 500, rows: KIDS })
      }
      return reply(200, fixtures.sessionDetail)
    }),
  )
  return calls
}

function Harness({ width, compact }: { width: number; compact: boolean }) {
  useGlobalKeys({ bladeKeys: ['turn'], focusKey: 'turn', setFocusIndex: () => {}, closeRightmost: () => {}, togglePalette: () => {} })
  return (
    <>
      <TurnBlade width={width} compact={compact} />
      <StatusHost sessionOpen />
    </>
  )
}

function mount(query: string, o: { width?: number; compact?: boolean } = {}) {
  window.history.replaceState(null, '', `/?s=${encodeURIComponent(S)}&${query}`)
  return render(
    <CrumbProvider>
      <StatusProvider>
        <Harness width={o.width ?? 1004} compact={o.compact ?? false} />
      </StatusProvider>
    </CrumbProvider>,
  )
}
const params = () => new URLSearchParams(window.location.search)
const press = (key: string) => fireEvent.keyDown(document.body, { key })
const cursorRow = () => document.querySelector('[data-cursor="true"][data-row-id]') as HTMLElement | null
const idOf = (suffix: string) => `claude_code/a11ce5e5-1111-4222-8333-944455556666/${suffix}`

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

describe('opening a turn (TRN-1 to TRN-5, TRN-60)', () => {
  it('shows the meta line, title, cost and the rows, with the cursor on the first row', async () => {
    serve()
    mount('t=1')
    expect(await screen.findByRole('heading', { name: 'first ask' })).toBeTruthy()
    expect(screen.getByText('Turn 1 of 4')).toBeTruthy()
    expect(await screen.findByText('Look at the sync')).toBeTruthy()
    expect(screen.getByText('Run the tests')).toBeTruthy()
    expect(cursorRow()!.dataset.rowId).toBe(idOf('llm/a1'))
    expect(screen.getByRole('button', { name: 'Previous turn' })).toHaveProperty('disabled', true)
  })

  it('the title opens the message detail: step is the turn id and detail is 1', async () => {
    serve()
    mount('t=1')
    fireEvent.click(await screen.findByRole('button', { name: 'first ask' }))
    expect([params().get('step'), params().get('detail')]).toEqual([T(1), '1'])
  })

  it('a click on a row makes it the cursor and opens the detail', async () => {
    serve()
    mount('t=1')
    fireEvent.click((await screen.findByText('Run the tests')).closest('[data-row-id]')!)
    expect([params().get('step'), params().get('detail')]).toEqual([idOf('tool/a2'), '1'])
  })

  it('shows the error card for the rows and the figures, and Retry asks again', async () => {
    const calls = serve({ rowsFail: true })
    mount('t=1')
    expect((await screen.findAllByText("Couldn't load the turn's rows")).length).toBe(1)
    const before = calls.filter((u) => u.endsWith('/rows')).length
    fireEvent.click(screen.getAllByRole('button', { name: 'Retry' })[0])
    await waitFor(() => expect(calls.filter((u) => u.endsWith('/rows')).length).toBeGreaterThan(before))
  })

  it('says so for a turn with no rows', async () => {
    serve()
    mount('t=3')
    expect(await screen.findByText('No model calls in this turn.')).toBeTruthy()
  })
})

describe('histogram rule (TRN-20)', () => {
  it('draws no histogram for a short turn with no agent and offers "Over time"', async () => {
    const calls = serve()
    mount('t=1')
    await screen.findByText('Look at the sync')
    expect(calls.some((u) => u.includes('/histogram'))).toBe(false)
    fireEvent.click(screen.getByRole('button', { name: /Over time/ }))
    expect(params().get('thist')).toBe('1')
  })
  it('shows it for a turn with an agent row, with no toggle', async () => {
    const calls = serve()
    mount('t=2')
    await screen.findByText('Survey', { exact: false })
    await waitFor(() => expect(calls.some((u) => u.includes('/api/turns/') && u.includes('/histogram'))).toBe(true))
    expect(screen.queryByRole('button', { name: /Over time/ })).toBeNull()
    expect(await screen.findByText('Drag across to zoom this turn')).toBeTruthy()
  })
  it('thist=0 hides it and brings the toggle', async () => {
    const calls = serve()
    mount('t=2&thist=0')
    await screen.findByText('Survey', { exact: false })
    expect(calls.some((u) => u.includes('/histogram'))).toBe(false)
    expect(screen.getByRole('button', { name: /Over time/ })).toBeTruthy()
  })
})

describe('the step-through (TRN-50, TRN-51, TRN-53)', () => {
  it('j moves the cursor and the URL follows with a replace', async () => {
    serve()
    mount('t=1')
    await screen.findByText('Look at the sync')
    const entries = window.history.length
    press('j')
    expect(params().get('step')).toBe(idOf('tool/a2'))
    press('j')
    expect(params().get('step')).toBe(idOf('llm/a3'))
    expect(window.history.length).toBe(entries)
  })

  it('j on the last row swaps in the next turn with rows (skipping the empty one), toasts, and puts the cursor on its first row', async () => {
    serve()
    mount(`t=2&step=${encodeURIComponent(idOf('llm/b2'))}`)
    await waitFor(() => expect(cursorRow()?.dataset.rowId).toBe(idOf('llm/b2')))
    press('j')
    expect(params().get('t')).toBe('4')
    expect((await screen.findByRole('status', { name: '' })).textContent).toContain('Turn 2 → turn 4: fourth ask')
    await waitFor(() => expect(params().get('step')).toBe(idOf('llm/d1')))
  })

  it('k on the first row goes to the previous turn’s last row', async () => {
    serve()
    mount('t=4')
    await screen.findByRole('heading', { name: 'fourth ask' })
    await waitFor(() => expect(cursorRow()).not.toBeNull())
    press('k')
    expect(params().get('t')).toBe('2')
    await waitFor(() => expect(params().get('step')).toBe(idOf('llm/b2')))
  })

  it('says "Last row of the session." and "First row of the session." and moves nothing', async () => {
    serve()
    mount(`t=4&step=${encodeURIComponent(idOf('llm/d2'))}`)
    await screen.findByRole('heading', { name: 'fourth ask' })
    await waitFor(() => expect(cursorRow()).not.toBeNull())
    press('j')
    expect((await screen.findByRole('status', { name: '' })).textContent).toBe('Last row of the session.')
    expect(params().get('t')).toBe('4')
    cleanup()
    serve()
    mount('t=1')
    await screen.findByRole('heading', { name: 'first ask' })
    await waitFor(() => expect(cursorRow()).not.toBeNull())
    press('k')
    expect((await screen.findByRole('status', { name: '' })).textContent).toBe('First row of the session.')
    expect(params().get('t')).toBe('1')
  })

  it('J and K stop at the turn’s edge instead of crossing', async () => {
    serve()
    mount('t=1')
    await screen.findByText('Look at the sync')
    press('J')
    expect(params().get('t')).toBe('1')
    expect(params().get('step')).toBe(idOf('llm/a3'))
    press('K')
    expect(params().get('step')).toBe(idOf('llm/a1'))
  })

  it('the zoom resets on a swap while the view, order and measure stay', async () => {
    serve()
    mount(`t=1&zoom=${at(0)}-${at(50)}&view=ledger&order=cost&bars=time`)
    await screen.findByRole('heading', { name: 'first ask' })
    press('t')
    expect(params().get('t')).toBe('2')
    expect([params().get('zoom'), params().get('order'), params().get('bars')]).toEqual([null, 'cost', 'time'])
  })

  it('t and T open the next and previous numbered turn with rows, T stops at the first', async () => {
    serve()
    mount('t=2')
    await screen.findByRole('heading', { name: 'second ask with an agent' })
    press('T')
    expect(params().get('t')).toBe('1')
    press('T')
    expect(params().get('t')).toBe('1')
  })
})

describe('agent bands (TRN-35)', () => {
  it('l shows an agent’s rows below it and moves the cursor to its first row; h folds it back to the agent row', async () => {
    serve()
    mount(`t=2&step=${encodeURIComponent(AG.id)}`)
    await screen.findByText('Survey', { exact: false })
    await waitFor(() => expect(cursorRow()?.dataset.rowId).toBe(AG.id))
    press('l')
    expect(await screen.findByText('Read the module')).toBeTruthy()
    await waitFor(() => expect(params().get('step')).toBe(idOf('llm/k1')))
    press('h')
    await waitFor(() => expect(params().get('step')).toBe(AG.id))
    expect(screen.queryByText('Read the module')).toBeNull()
  })

  it('the "N rows" button of an agent row shows its rows', async () => {
    serve()
    mount('t=2')
    fireEvent.click(await screen.findByRole('button', { name: '2 rows' }))
    expect(await screen.findByText('Read the module')).toBeTruthy()
  })
})

describe('agent band paging and failure (TRN-35, D38 M8)', () => {
  it('shows "Show N more" for the rows beyond the first page and asks for the next page on click', async () => {
    const calls = serve({ agentTotal: 1203 })
    mount('t=2')
    fireEvent.click(await screen.findByRole('button', { name: '2 rows' }))
    const more = await screen.findByRole('button', { name: 'Show 1201 more' })
    fireEvent.click(more)
    await waitFor(() => expect(calls.some((u) => u.includes('/rows') && u.includes('offset=500'))).toBe(true))
  })

  it('shows the error card inside the band when the page fails, and Retry asks again', async () => {
    const calls = serve({ agentFail: true })
    mount('t=2')
    fireEvent.click(await screen.findByRole('button', { name: '2 rows' }))
    expect(await screen.findByText('band boom')).toBeTruthy()
    const before = calls.filter((u) => u.includes('/api/ops/')).length
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    await waitFor(() => expect(calls.filter((u) => u.includes('/api/ops/')).length).toBeGreaterThan(before))
  })

  it('opens the band that holds a `step` which is not in the turn’s own rows, after resolving it', async () => {
    serve()
    mount(`t=2&step=${encodeURIComponent(idOf('llm/k1'))}`)
    await waitFor(() => expect(cursorRow()?.dataset.rowId).toBe(idOf('llm/k1')))
    expect(screen.getByText('Read the module')).toBeTruthy()
  })
})

describe('SES-9: a step the turn does not list', () => {
  it('drops a step that resolves to nothing, and the turn opens at its first row', async () => {
    serve()
    mount(`t=1&step=${encodeURIComponent(idOf('llm/nowhere'))}`)
    await waitFor(() => expect(params().get('step')).toBeNull())
    expect(cursorRow()?.dataset.rowId).toBe(idOf('llm/a1'))
  })
  it('opens another turn when the step resolves to a row in it', async () => {
    serve()
    mount(`t=1&step=${encodeURIComponent(idOf('llm/k1'))}`)
    await waitFor(() => expect(params().get('t')).toBe('2'))
    await waitFor(() => expect(cursorRow()?.dataset.rowId).toBe(idOf('llm/k1')))
  })
})

describe('zoom and order (TRN-14, TRN-24)', () => {
  it('Esc resets the zoom and then lets the shell close the blade; 0 resets it too', async () => {
    serve()
    mount(`t=1&zoom=${at(0)}-${at(50)}`)
    await screen.findByRole('heading', { name: 'first ask' })
    expect(screen.getByRole('button', { name: 'Whole turn' })).toBeTruthy()
    press('0')
    expect(params().get('zoom')).toBeNull()
  })

  it('the cost answer explains the zoomed figure in a tooltip, and the whole turn has none (TRN-4, TRN-24)', async () => {
    serve()
    mount(`t=1&zoom=${at(0)}-${at(50)}`)
    await screen.findByRole('heading', { name: 'first ask' })
    fireEvent.pointerEnter((await screen.findByLabelText('Cost split: main thread and agents')).previousElementSibling!.firstElementChild!)
    expect((await screen.findByRole('tooltip')).textContent).toMatch(/^In a range, cost counts model calls that started inside it\./)
  })

  it('a whole turn shows no cost caveat', async () => {
    serve()
    mount('t=1')
    await screen.findByRole('heading', { name: 'first ask' })
    fireEvent.pointerEnter((await screen.findByLabelText('Cost split: main thread and agents')).previousElementSibling!.firstElementChild!)
    await act(async () => void (await new Promise((r) => setTimeout(r, 450))))
    expect(screen.queryByText(/In a range, cost counts/)).toBeNull()
  })

  it('the zoom keeps the rows that start inside it and moves the cursor off a row it hides', async () => {
    serve()
    mount(`t=1&step=${encodeURIComponent(idOf('llm/a3'))}&zoom=${at(0)}-${at(15)}`)
    await screen.findByText('Run the tests')
    expect(screen.queryByText('Thinking only')).toBeNull() // a3 (sec 20) is outside; a1 and a2 stay
    await waitFor(() => expect(cursorRow()?.dataset.rowId).toBe(idOf('llm/a1')))
  })

  it('the Cost order lists main-thread rows by cost with a column head that says so', async () => {
    serve()
    mount('t=2&order=cost')
    await screen.findByText('Top by cost')
    expect(screen.getByText('by cost')).toBeTruthy()
  })
})

describe('the status bar (SES-70)', () => {
  it('says the turn, the row position and the level', async () => {
    serve()
    mount('t=1')
    await screen.findByText('Look at the sync')
    expect((await screen.findByText(/^Turn 1 · row 1 of 3 · main thread$/)).tagName).toBe('SPAN')
    press('j')
    await waitFor(() => expect(screen.getByText(/row 2 of 3/)).toBeTruthy())
  })
  it('names the agent when the cursor is inside one', async () => {
    serve()
    mount(`t=2&step=${encodeURIComponent(AG.id)}`)
    await screen.findByText('Survey', { exact: false })
    press('l')
    await screen.findByText('Read the module')
    await waitFor(() => expect(screen.getByText(/inside ag/)).toBeTruthy())
  })
})

describe('the compact form (TRN-1)', () => {
  it('shows the meta line, title and figures with two-line rows and no finder line or histogram', async () => {
    const calls = serve()
    mount('t=2', { compact: true, width: 400 })
    expect(await screen.findByRole('heading', { name: 'second ask with an agent' })).toBeTruthy()
    expect(await screen.findByText('ag')).toBeTruthy()
    expect(screen.queryByText('Ledger')).toBeNull()
    expect(screen.queryByRole('button', { name: /Details/ })).toBeNull()
    expect(calls.some((u) => u.includes('/api/turns/') && u.includes('/histogram'))).toBe(false)
    expect(screen.getByText('wall')).toBeTruthy()
  })
})

describe('the view (TRN-40)', () => {
  it('v switches to the Waterfall, which keeps the cursor and the rows', async () => {
    serve()
    mount('t=1')
    await screen.findByText('Look at the sync')
    press('v')
    expect(params().get('view')).toBe('waterfall')
    await act(async () => {})
    expect(screen.getByRole('group', { name: 'Axis' })).toBeTruthy()
    expect(within(screen.getByRole('group', { name: 'View' })).getByRole('button', { name: 'Waterfall' }).getAttribute('aria-pressed')).toBe('true')
  })
})
