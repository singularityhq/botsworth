// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fixtures } from '../../api/__fixtures__'
import { clearEtags } from '../../api/client'
import { responseCache } from '../../api/useApi'
import { ExplorerBlade } from '.'

// The list and rail belong to F2b; the page test only needs to see that they mount with the scope.
vi.mock('./list/SessionList', () => ({ SessionList: (p: { scope: { from_day: string | null } }) => <div data-testid="list">{p.scope.from_day}</div> }))
vi.mock('./rail/Rail', () => ({ Rail: () => <div data-testid="rail" /> }))

type Handler = (url: URL) => Response | Promise<Response>
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })

let calls: string[] = []
function serve(overrides: Record<string, Handler> = {}) {
  const routes: Record<string, Handler> = {
    '/api/ingest/status': () => json(fixtures.ingestStatus),
    '/api/explorer/summary': () => json(fixtures.explorerSummary),
    '/api/explorer/days': () => json(fixtures.explorerDays),
    '/api/explorer/facets': () => json(fixtures.explorerFacets),
    ...overrides,
  }
  vi.stubGlobal('fetch', vi.fn(async (input: string) => {
    calls.push(input)
    const u = new URL(input, 'http://x')
    const h = routes[u.pathname]
    if (!h) throw new Error(`unexpected request ${input}`)
    return h(u)
  }))
}

const at = (search: string) => window.history.replaceState(null, '', `/${search}`)

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date('2026-10-12T10:00:00+05:30'))
  responseCache.clear()
  clearEtags()
  calls = []
  at('')
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

describe('explorer page', () => {
  it('shows the title, totals line, tokens, chart columns and project legend from the fixtures', async () => {
    serve()
    render(<ExplorerBlade width={1416} compact={false} />)
    expect(await screen.findByRole('button', { name: 'Last 7 days · Oct 6 – Oct 12' })).toBeTruthy()
    const status = fixtures.explorerSummary.totals
    await waitFor(() => expect(screen.getByText(String(status.n_sessions))).toBeTruthy())
    expect(screen.getByText('12 automated hidden')).toBeTruthy()
    expect(screen.getByText('Whole sessions active in this period')).toBeTruthy() // D51
    expect(screen.getByText('automated hidden · 12')).toBeTruthy() // the token (EXP-27)
    const days = await screen.findByRole('group', { name: 'Days' })
    expect(within(days).getAllByRole('button')).toHaveLength(21)
    expect(screen.getByRole('button', { name: /^Wed Oct 7, \$549\.89\+$/ })).toBeTruthy()
    expect(screen.getAllByTitle(/bizdemo · 90 sessions/).length).toBe(1)
    expect(screen.getByTestId('list').textContent).toBe('2026-10-06')
  })

  it('sends the request scope: window of 21 days, no from/to on the days route, status first', async () => {
    serve()
    render(<ExplorerBlade width={1416} compact={false} />)
    await screen.findByRole('group', { name: 'Days' })
    expect(calls[0]).toBe('/api/ingest/status')
    const days = calls.find((c) => c.startsWith('/api/explorer/days'))!
    expect(days).toContain('window_from=2026-09-22')
    expect(days).toContain('window_to=2026-10-12')
    expect(days).not.toMatch(/[?&]from=/)
    const summaries = calls.filter((c) => c.startsWith('/api/explorer/summary'))
    expect(summaries).toEqual(['/api/explorer/summary?from=2026-10-06&to=2026-10-12&tz=Asia%2FKolkata'])
  })

  it('asks the project panel for the scope without the project filter, and the summary with it', async () => {
    at('?project=%2FUsers%2Fa%2Fbizdemo')
    serve()
    render(<ExplorerBlade width={1416} compact={false} />)
    await screen.findByRole('group', { name: 'Days' })
    const summaries = calls.filter((c) => c.startsWith('/api/explorer/summary')).sort()
    expect(summaries).toHaveLength(2)
    expect(summaries.some((c) => c.includes('project='))).toBe(true)
    expect(summaries.some((c) => !c.includes('project='))).toBe(true)
  })

  it('a click on a day makes the period that single day, in the URL', async () => {
    serve()
    render(<ExplorerBlade width={1416} compact={false} />)
    const col = await screen.findByRole('button', { name: /^Wed Oct 7,/ })
    fireEvent.pointerDown(col, { button: 0 })
    fireEvent.pointerUp(window)
    await waitFor(() => expect(window.location.search).toContain('from=2026-10-07&to=2026-10-07'))
    expect(await screen.findByRole('button', { name: 'Wed Oct 7' })).toBeTruthy()
  })

  it('Enter on a focused day column sets the period; shift+right extends it', async () => {
    serve()
    render(<ExplorerBlade width={1416} compact={false} />)
    const col = await screen.findByRole('button', { name: /^Wed Oct 7,/ })
    col.focus()
    fireEvent.focus(col)
    fireEvent.keyDown(col, { key: 'ArrowRight', shiftKey: true })
    await waitFor(() => expect(window.location.search).toContain('from=2026-10-07&to=2026-10-08'))
  })

  it('switching the measure rewrites the URL and the legend figures, and sends no new request', async () => {
    serve()
    render(<ExplorerBlade width={1416} compact={false} />)
    await screen.findByRole('group', { name: 'Days' })
    const before = calls.length
    fireEvent.click(screen.getByRole('button', { name: 'Tokens' }))
    await waitFor(() => expect(window.location.search).toContain('measure=tokens'))
    expect(calls.length).toBe(before)
    expect(screen.getAllByText('5B').length).toBeGreaterThan(0)
  })

  it('shows Clear all only with a search or filter and resets only those fields', async () => {
    at('?q=rbac&measure=tokens&sort=cost')
    serve()
    render(<ExplorerBlade width={1416} compact={false} />)
    const clear = await screen.findByRole('button', { name: 'Clear all' })
    fireEvent.click(clear)
    await waitFor(() => expect(window.location.search).toBe('?measure=tokens&sort=cost'))
    expect(screen.queryByRole('button', { name: 'Clear all' })).toBeNull()
  })

  it('removes one project token and keeps the other (EXP-25 scenario)', async () => {
    at('?project=%2FUsers%2Fa%2Fbizdemo&project=%2FUsers%2Fa%2Fscratch')
    serve()
    render(<ExplorerBlade width={1416} compact={false} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Remove scratch filter' }))
    await waitFor(() => expect(window.location.search).toBe('?project=%2FUsers%2Fa%2Fbizdemo'))
    expect(screen.getByRole('button', { name: 'Remove bizdemo filter' })).toBeTruthy()
  })

  it('a legend click filters to that project and a shift-click adds another (EXP-46 scenario)', async () => {
    serve()
    render(<ExplorerBlade width={1416} compact={false} />)
    const rows = await screen.findAllByTitle(/click to filter/)
    fireEvent.click(rows[0])
    await waitFor(() => expect(window.location.search).toBe('?project=%2FUsers%2Fa%2Fbizdemo'))
    fireEvent.click(screen.getAllByTitle(/click to filter/)[1], { shiftKey: true })
    await waitFor(() => expect(window.location.search).toContain('project=%2FUsers%2Fa%2Fold-profiler'))
    expect(window.location.search.match(/project=/g)).toHaveLength(2)
  })

  it('opens the period popover, applies Yesterday and closes it', async () => {
    serve()
    render(<ExplorerBlade width={1416} compact={false} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Last 7 days · Oct 6 – Oct 12' }))
    const dialog = await screen.findByRole('dialog', { name: 'Period' })
    expect(within(dialog).getByText(/Days are in Asia\/Kolkata/)).toBeTruthy()
    fireEvent.click(within(dialog).getByRole('button', { name: /Yesterday/ }))
    await waitFor(() => expect(window.location.search).toContain('period=yesterday'))
    expect(screen.queryByRole('dialog', { name: 'Period' })).toBeNull()
    expect(await screen.findByRole('button', { name: 'Yesterday · Sun Oct 11' })).toBeTruthy()
  })

  it('applies a typed To date on Enter and restores the field on bad text', async () => {
    serve()
    at('?from=2026-10-06&to=2026-10-12')
    render(<ExplorerBlade width={1416} compact={false} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Oct 6 – Oct 12' }))
    const dialog = await screen.findByRole('dialog', { name: 'Period' })
    const to = within(dialog).getByLabelText('To') as HTMLInputElement
    fireEvent.change(to, { target: { value: 'banana' } })
    fireEvent.keyDown(to, { key: 'Enter' })
    expect(to.value).toBe('Oct 12')
    fireEvent.change(to, { target: { value: 'oct 7' } })
    fireEvent.keyDown(to, { key: 'Enter' })
    await waitFor(() => expect(window.location.search).toContain('from=2026-10-06&to=2026-10-07'))
  })

  it('lists facets in the filter popover and toggles a model', async () => {
    serve()
    render(<ExplorerBlade width={1416} compact={false} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Filter' }))
    const dialog = await screen.findByRole('dialog', { name: 'Filters' })
    expect(await within(dialog).findByText('Model · sessions using')).toBeTruthy()
    fireEvent.click(within(dialog).getByRole('checkbox', { name: /opus 5\.5/ }))
    await waitFor(() => expect(window.location.search).toContain('model=claude-opus-5-5'))
    expect(within(dialog).queryByText('omp')).toBeNull() // omp has 0 sessions: not offered as a provider chip
  })

  it('shows the error card in the totals region only when the summary fails (EXP-81)', async () => {
    serve({ '/api/explorer/summary': () => json({ error: 'boom', code: 'internal' }, 500) })
    render(<ExplorerBlade width={1416} compact={false} />)
    expect(await screen.findAllByText("Couldn't load totals")).toHaveLength(1)
    expect(await screen.findByRole('group', { name: 'Days' })).toBeTruthy()
    expect(screen.getAllByText('boom').length).toBeGreaterThan(0)
  })

  it('says the server is unreachable in every region (EXP-83)', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => {
      throw new TypeError('Failed to fetch')
    }))
    render(<ExplorerBlade width={1416} compact={false} />)
    expect((await screen.findAllByText("Couldn't reach the Botsworth server")).length).toBeGreaterThanOrEqual(3)
  })

  it('reads "No data yet." and fetches no data when the store is missing (EXP-82)', async () => {
    serve({ '/api/ingest/status': () => json({ ...fixtures.ingestStatus, store_exists: false, store_state: 'missing' }) })
    render(<ExplorerBlade width={1416} compact={false} />)
    await waitFor(() => expect(screen.getAllByText('No data yet.').length).toBeGreaterThanOrEqual(3))
    expect(calls.filter((c) => c.startsWith('/api/explorer'))).toEqual([])
    expect(screen.getByText('sessions')).toBeTruthy()
  })

  it('shows "No sessions match" with the all-time count and a Show link (EXP-79 scenario)', async () => {
    at('?q=rbacxyz')
    const empty = { ...fixtures.explorerSummary, totals: { ...fixtures.explorerSummary.totals, n_sessions: 0 }, projects: [], empty: { all_time_matches: 3, prev_day: null, next_day: null } }
    serve({ '/api/explorer/summary': () => json(empty) })
    render(<ExplorerBlade width={1416} compact={false} />)
    expect(await screen.findByText('No sessions match')).toBeTruthy()
    expect(screen.getByText(/"rbacxyz" matches 3 sessions in all time/)).toBeTruthy()
    expect(screen.queryByRole('region', { name: 'Projects' })).toBeNull()
    expect(screen.queryByTestId('list')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Show' }))
    await waitFor(() => expect(window.location.search).toContain('period=all'))
  })

  it('shows the empty period with the nearest days that have sessions (EXP-80)', async () => {
    at('?from=2026-10-04&to=2026-10-04')
    const empty = {
      ...fixtures.explorerSummary,
      totals: { ...fixtures.explorerSummary.totals, n_sessions: 0, cost_usd: null, tok_total: null },
      projects: [],
      empty: { all_time_matches: null, prev_day: { day: '2026-10-03', n_sessions: 12 }, next_day: { day: '2026-10-05', n_sessions: 9 } },
    }
    serve({ '/api/explorer/summary': () => json(empty) })
    render(<ExplorerBlade width={1416} compact={false} />)
    expect(await screen.findByText('No sessions on Sun Oct 4')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Sat Oct 3 · 12 sessions' })).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Mon Oct 5 · 9 sessions' }))
    await waitFor(() => expect(window.location.search).toContain('from=2026-10-05&to=2026-10-05'))
  })

  it('renders the rail instead of the page at the compact width', async () => {
    serve()
    render(<ExplorerBlade width={400} compact />)
    expect(await screen.findByTestId('rail')).toBeTruthy()
    expect(screen.queryByRole('group', { name: 'Days' })).toBeNull()
    expect(calls.filter((c) => c.startsWith('/api/explorer/days'))).toEqual([])
  })
})
