// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fixtures } from '../../../api/__fixtures__'
import type { Scope, SessionRow } from '../../../api/types'
import { responseCache } from '../../../api/useApi'
import { useGlobalKeys } from '../../../shell/keys'
import { useExplorerRouter } from '../keys'
import { toScope, useUrlState } from '../../../state/url'
import { Toast } from '../../../ui/Toast'
import { SessionList } from './SessionList'

const BASE_SCOPE: Scope = { from_day: '2026-10-04', to_day: '2026-10-04', q: '', providers: [], models: [], branches: [], projects: [], show_automated: false }

class RO {
  observe() {}
  unobserve() {}
  disconnect() {}
}

type Route = unknown | ((u: URL) => unknown)

function stubApi(routes: Record<string, Route>) {
  const f = vi.fn(async (input: RequestInfo | URL) => {
    const u = new URL(String(input), 'http://x')
    const r = routes[u.pathname]
    const body = typeof r === 'function' ? (r as (u: URL) => unknown)(u) : r
    if (body === undefined) return new Response(JSON.stringify({ error: `no route ${u.pathname}`, code: 'not_found' }), { status: 404, headers: { 'content-type': 'application/json' } })
    return new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } })
  })
  vi.stubGlobal('fetch', f)
  return f
}

/** What F2a does: read sort, dir and group from the URL and pass them down. The key router stands in for the shell's. */
function Connected({ onHover }: { onHover: (r: SessionRow | null) => void }) {
  const url = useUrlState()
  useGlobalKeys({ bladeKeys: ['explorer'], focusKey: 'explorer', setFocusIndex() {}, closeRightmost() {}, togglePalette() {} })
  useExplorerRouter({ key: () => false, escape: () => false }) // the page's router, with no page keys of its own
  return (
    <div style={{ overflowY: 'auto', height: 600 }}>
      <SessionList scope={toScope(url, '2026-10-12')} sort={url.sort} dir={url.dir} group={url.group} onHoverSession={onHover} />
    </div>
  )
}

/** jsdom has no layout. Put the scroller at the top of a 600 px viewport and the list 100 px below its top, as the head line and band do. */
function fakeLayout() {
  vi.spyOn(Element.prototype, 'getBoundingClientRect').mockImplementation(function (this: Element) {
    const scroller = (this as HTMLElement).style?.overflowY === 'auto'
    const top = scroller ? 0 : 100
    const height = scroller ? 600 : 0
    return { top, bottom: top + height, left: 0, right: 1000, width: 1000, height, x: 0, y: top, toJSON() {} } as DOMRect
  })
}

beforeEach(() => {
  fakeLayout()
  vi.stubGlobal('ResizeObserver', RO)
  Element.prototype.animate = vi.fn() as unknown as Element['animate']
  vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {} }))
  window.history.replaceState(null, '', '/')
  responseCache.clear()
})
afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

const press = (key: string) => act(() => void fireEvent.keyDown(window, { key }))

describe('SessionList, flat', () => {
  it('draws the head line, the column band and the rows of the first page', async () => {
    stubApi({ '/api/explorer/sessions': fixtures.explorerSessions })
    render(<Connected onHover={() => {}} />)
    await screen.findByText('Fix rbac sync')
    expect(screen.getByRole('heading', { name: 'Sessions' })).toBeTruthy()
    expect(screen.getByText('212')).toBeTruthy()
    for (const h of ['Started', 'Project', 'Session', 'Activity', 'Cost', 'Wall', 'Active', 'Tokens']) expect(screen.getAllByText(h).length).toBeGreaterThan(0)
    const rows = screen.getAllByRole('listitem').filter((r) => r.getAttribute('data-session'))
    expect(rows).toHaveLength(2)
    const first = within(rows[0])
    expect(first.getByText('bizdemo')).toBeTruthy()
    expect(first.getByText('$12.40')).toBeTruthy()
  })

  it('starts sorted by Started, newest first, with the arrow on that head only', async () => {
    stubApi({ '/api/explorer/sessions': fixtures.explorerSessions })
    render(<Connected onHover={() => {}} />)
    await screen.findByText('Fix rbac sync')
    expect(screen.getByRole('button', { name: 'Started' }).getAttribute('aria-sort')).toBe('descending')
    expect(screen.getByRole('button', { name: 'Cost' }).getAttribute('aria-sort')).toBe('none')
  })

  it('EXP-51: Cost sorts by cost, highest first, and a second click reverses it', async () => {
    const f = stubApi({ '/api/explorer/sessions': fixtures.explorerSessions })
    render(<Connected onHover={() => {}} />)
    await screen.findByText('Fix rbac sync')
    fireEvent.click(screen.getByRole('button', { name: 'Cost' }))
    await waitFor(() => expect(window.location.search).toBe('?sort=cost'))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Cost' }).getAttribute('aria-sort')).toBe('descending'))
    expect(f.mock.calls.some(([u]) => String(u).includes('sort=cost'))).toBe(true)
    fireEvent.click(screen.getByRole('button', { name: 'Cost' }))
    await waitFor(() => expect(window.location.search).toBe('?sort=cost&dir=asc'))
  })

  it('EXP-54: j twice then Enter opens the second row in blade 1; the cursor follows to the chart', async () => {
    stubApi({ '/api/explorer/sessions': fixtures.explorerSessions })
    const hovered: (string | null)[] = []
    render(<Connected onHover={(r) => hovered.push(r ? r.session_id : null)} />)
    await screen.findByText('Fix rbac sync')
    press('j')
    await waitFor(() => expect(document.querySelector('[data-cursor="true"]')).not.toBeNull())
    expect(document.querySelector('[data-cursor="true"]')!.getAttribute('data-session')).toBe(fixtures.explorerSessions.rows[0].session)
    press('j')
    await waitFor(() => expect(document.querySelector('[data-cursor="true"]')!.getAttribute('data-session')).toBe(fixtures.explorerSessions.rows[1].session))
    expect(hovered.at(-1)).toBe(fixtures.explorerSessions.rows[1].session_id)
    press('Enter')
    await waitFor(() => expect(new URLSearchParams(window.location.search).get('s')).toBe(fixtures.explorerSessions.rows[1].session))
  })

  it('D45: j still moves the cursor while a toast shows', async () => {
    stubApi({ '/api/explorer/sessions': fixtures.explorerSessions })
    render(
      <>
        <Connected onHover={() => {}} />
        <Toast message="Filters cleared to show this session" onUndo={() => {}} onClose={() => {}} />
      </>,
    )
    await screen.findByText('Fix rbac sync')
    press('j')
    await waitFor(() => expect(document.querySelector('[data-cursor="true"]')).not.toBeNull())
  })

  it('a row click opens the session blade', async () => {
    stubApi({ '/api/explorer/sessions': fixtures.explorerSessions })
    render(<Connected onHover={() => {}} />)
    fireEvent.click(await screen.findByText('Fix rbac sync'))
    await waitFor(() => expect(new URLSearchParams(window.location.search).get('s')).toBe(fixtures.explorerSessions.rows[0].session))
  })

  it('reports the hovered row and clears it on leave', async () => {
    stubApi({ '/api/explorer/sessions': fixtures.explorerSessions })
    const seen: (string | null)[] = []
    render(<Connected onHover={(r) => seen.push(r ? r.name : null)} />)
    const row = (await screen.findByText('Fix rbac sync')).closest('[data-session]')!
    fireEvent.pointerEnter(row)
    fireEvent.pointerLeave(row)
    expect(seen.slice(-2)).toEqual(['Fix rbac sync', null])
  })

  it('EXP-47: the group control sets the group in the URL', async () => {
    stubApi({ '/api/explorer/sessions': (u: URL) => (u.searchParams.has('group') ? fixtures.explorerSessionsGrouped : fixtures.explorerSessions) })
    render(<Connected onHover={() => {}} />)
    await screen.findByText('Fix rbac sync')
    fireEvent.click(screen.getByRole('button', { name: /Group: None/ }))
    fireEvent.click(await screen.findByRole('button', { name: 'Project' }))
    await waitFor(() => expect(window.location.search).toBe('?group=project'))
  })

  it('shows skeleton rows on the first load, never zeros', async () => {
    let release!: () => void
    vi.stubGlobal('fetch', vi.fn(() => new Promise((res) => (release = () => res(new Response(JSON.stringify(fixtures.explorerSessions), { status: 200, headers: { 'content-type': 'application/json' } }))))))
    render(<Connected onHover={() => {}} />)
    expect(screen.getByRole('status', { name: 'Loading' })).toBeTruthy()
    release()
    await screen.findByText('Fix rbac sync')
  })

  it('EXP-81: a failed request shows the error card with Retry in the list region', async () => {
    stubApi({})
    render(<Connected onHover={() => {}} />)
    await screen.findByRole('alert')
    expect(screen.getByRole('button', { name: 'Retry' })).toBeTruthy()
  })

  it('EXP-79: no sessions with a filter active says so, offers Clear filters, and states the all-time count', async () => {
    const empty = { ...fixtures.explorerSessions, total: 0, rows: [] }
    const summary = { ...fixtures.explorerSummary, empty: { all_time_matches: 3, prev_day: null, next_day: null } }
    stubApi({ '/api/explorer/sessions': empty, '/api/explorer/summary': summary })
    window.history.replaceState(null, '', '/?q=rbacxyz&model=claude-opus-5-5')
    render(<Connected onHover={() => {}} />)
    await screen.findByText('No sessions match')
    await screen.findByText(/"rbacxyz" matches 3 sessions in all time/)
    expect(screen.getByRole('button', { name: 'Show' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Remove opus 5.5 filter' })).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Clear filters' }))
    await waitFor(() => expect(window.location.search).toBe(''))
  })

  it('EXP-80: an empty period names the day and offers the nearest days with sessions', async () => {
    const empty = { ...fixtures.explorerSessions, total: 0, rows: [] }
    const summary = {
      ...fixtures.explorerSummary,
      empty: { all_time_matches: null, prev_day: { day: '2026-10-03', n_sessions: 12 }, next_day: { day: '2026-10-05', n_sessions: 9 } },
    }
    stubApi({ '/api/explorer/sessions': empty, '/api/explorer/summary': summary })
    function One() {
      return (
        <div style={{ overflowY: 'auto' }}>
          <SessionList scope={{ ...BASE_SCOPE, from_day: '2026-10-04', to_day: '2026-10-04' }} sort="start" dir="desc" group={null} onHoverSession={() => {}} />
        </div>
      )
    }
    render(<One />)
    await screen.findByText('No sessions on Sun Oct 4')
    fireEvent.click(await screen.findByRole('button', { name: 'Sat Oct 3 · 12 sessions' }))
    await waitFor(() => expect(window.location.search).toBe('?from=2026-10-03&to=2026-10-03'))
    expect(screen.getByRole('button', { name: 'Mon Oct 5 · 9 sessions' })).toBeTruthy()
  })
})

describe('SessionList, grouped', () => {
  it('EXP-56/58: a project group band with its totals and "Show N more · $X"', async () => {
    stubApi({ '/api/explorer/sessions': fixtures.explorerSessionsGrouped })
    window.history.replaceState(null, '', '/?group=project')
    render(<Connected onHover={() => {}} />)
    await screen.findByText('Fix rbac sync')
    expect(screen.getByRole('button', { name: /bizdemo/ }).getAttribute('aria-expanded')).toBe('true')
    // 90 sessions, 1 shown in the fixture group; the fold row reads from the group totals
    expect(screen.getByRole('button', { name: /Show 85 more/ })).toBeTruthy()
    // the Project column is hidden when grouped by project
    expect(screen.queryByText('Project')).toBeNull()
    expect(screen.getByText('$1,500.50')).toBeTruthy()
  })

  it('EXP-59: clicking the band collapses the group', async () => {
    stubApi({ '/api/explorer/sessions': fixtures.explorerSessionsGrouped })
    window.history.replaceState(null, '', '/?group=project')
    render(<Connected onHover={() => {}} />)
    await screen.findByText('Fix rbac sync')
    fireEvent.click(screen.getByRole('button', { name: /bizdemo/ }))
    await waitFor(() => expect(screen.queryByText('Fix rbac sync')).toBeNull())
  })
})
