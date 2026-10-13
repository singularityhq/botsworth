// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fixtures } from '../../../api/__fixtures__'
import type { Scope } from '../../../api/types'
import { responseCache } from '../../../api/useApi'
import { useGlobalKeys } from '../../../shell/keys'
import { useExplorerRouter } from '../keys'
import { toScope, useUrlState } from '../../../state/url'
import { Rail } from './Rail'

class RO {
  observe() {}
  unobserve() {}
  disconnect() {}
}

function stubApi(sessions: unknown) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const u = new URL(String(input), 'http://x')
      const body = u.pathname === '/api/explorer/sessions' ? sessions : { error: 'no route', code: 'not_found' }
      return new Response(JSON.stringify(body), { status: u.pathname === '/api/explorer/sessions' ? 200 : 404, headers: { 'content-type': 'application/json' } })
    }),
  )
}

function Connected() {
  const url = useUrlState()
  useGlobalKeys({ bladeKeys: ['explorer', 'session'], focusKey: 'explorer', setFocusIndex() {}, closeRightmost() {}, togglePalette() {} })
  useExplorerRouter({ key: () => false, escape: () => false }) // the page's router, with no page keys of its own
  const scope: Scope = toScope(url, '2026-10-12')
  return (
    <div style={{ height: 700 }}>
      <Rail scope={scope} sort={url.sort} dir={url.dir} group={url.group} onHoverSession={() => {}} />
    </div>
  )
}

beforeEach(() => {
  vi.spyOn(Element.prototype, 'getBoundingClientRect').mockImplementation(function (this: Element) {
    const scroller = (this as HTMLElement).style?.overflowY === 'auto'
    const top = scroller ? 0 : 100
    const height = scroller ? 600 : 0
    return { top, bottom: top + height, left: 0, right: 400, width: 400, height, x: 0, y: top, toJSON() {} } as DOMRect
  })
  Element.prototype.animate = vi.fn() as unknown as Element['animate']
  vi.stubGlobal('ResizeObserver', RO)
  vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {} }))
  responseCache.clear()
})
afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

const [A, B] = fixtures.explorerSessions.rows
const open = (s: string) => window.history.replaceState(null, '', `/?s=${encodeURIComponent(s)}`)
const press = (key: string) => act(() => void fireEvent.keyDown(window, { key }))

describe('Rail', () => {
  it('EXP-61/62: header with the position, the search field, compact rows and the key hints', async () => {
    open(A.session)
    stubApi(fixtures.explorerSessions)
    render(<Connected />)
    await screen.findByText('Fix rbac sync')
    expect(screen.getByText('1 / 212')).toBeTruthy()
    expect(screen.getByPlaceholderText('Search sessions')).toBeTruthy()
    expect(screen.getByText('move')).toBeTruthy()
    expect(screen.getByText('back')).toBeTruthy()
    const row = document.querySelector(`[data-session="${A.session}"]`)!
    expect(within(row as HTMLElement).getByText('bizdemo')).toBeTruthy()
    expect(within(row as HTMLElement).getByText('$12.40')).toBeTruthy()
  })

  it('EXP-64: the open session has the open state and the cursor', async () => {
    open(A.session)
    stubApi(fixtures.explorerSessions)
    render(<Connected />)
    await screen.findByText('Fix rbac sync')
    const row = document.querySelector(`[data-session="${A.session}"]`)!
    expect(row.getAttribute('data-open')).toBe('true')
    expect(row.getAttribute('data-cursor')).toBe('true')
    expect(document.querySelector(`[data-session="${B.session}"]`)!.getAttribute('data-open')).toBe('false')
  })

  it('EXP-63: j opens the next session in blade 1 and the header reads 2 / 212', async () => {
    open(A.session)
    stubApi(fixtures.explorerSessions)
    render(<Connected />)
    await screen.findByText('Fix rbac sync')
    press('j')
    await waitFor(() => expect(new URLSearchParams(window.location.search).get('s')).toBe(B.session))
    await screen.findByText('2 / 212')
    // replace, not push: moving the cursor adds no history entry (SES-10)
    expect(window.history.length).toBeLessThan(5)
    press('k')
    await waitFor(() => expect(new URLSearchParams(window.location.search).get('s')).toBe(A.session))
    await screen.findByText('1 / 212')
  })

  it('EXP-63: j drops the old session\'s blade parameters', async () => {
    window.history.replaceState(null, '', `/?s=${encodeURIComponent(A.session)}&t=2&detail=1&find=x`)
    stubApi(fixtures.explorerSessions)
    render(<Connected />)
    await screen.findByText('Fix rbac sync')
    press('j')
    await waitFor(() => expect(window.location.search).toBe(`?s=${encodeURIComponent(B.session)}`))
  })

  it('EXP-63: clicking a row does the same as moving the cursor to it', async () => {
    open(A.session)
    stubApi(fixtures.explorerSessions)
    render(<Connected />)
    fireEvent.click(await screen.findByText('Port the parser'))
    await waitFor(() => expect(new URLSearchParams(window.location.search).get('s')).toBe(B.session))
  })

  it('EXP-65: an open session that is not in the list keeps its blade; no row is open and j starts at row 1', async () => {
    open('claude_code/gone/session/-')
    stubApi(fixtures.explorerSessions)
    render(<Connected />)
    await screen.findByText('Fix rbac sync')
    expect(screen.getByText('– / 212')).toBeTruthy()
    expect(document.querySelectorAll('[data-open="true"]')).toHaveLength(0)
    expect(new URLSearchParams(window.location.search).get('s')).toBe('claude_code/gone/session/-')
    press('j')
    await waitFor(() => expect(new URLSearchParams(window.location.search).get('s')).toBe(A.session))
  })

  it('EXP-61: the search field is bound to the URL search text', async () => {
    open(A.session)
    stubApi(fixtures.explorerSessions)
    render(<Connected />)
    await screen.findByText('Fix rbac sync')
    fireEvent.change(screen.getByPlaceholderText('Search sessions'), { target: { value: 'rbac' } })
    await waitFor(() => expect(new URLSearchParams(window.location.search).get('q')).toBe('rbac'))
    fireEvent.click(screen.getByRole('button', { name: 'Clear search' }))
    await waitFor(() => expect(new URLSearchParams(window.location.search).get('q')).toBeNull())
  })

  it('EXP-62: grouped by project the rail rows drop the project and the band shows the group cost', async () => {
    window.history.replaceState(null, '', `/?s=${encodeURIComponent(A.session)}&group=project`)
    stubApi(fixtures.explorerSessionsGrouped)
    render(<Connected />)
    await screen.findByText('Fix rbac sync')
    const row = document.querySelector(`[data-session="${fixtures.explorerSessionsGrouped.groups![0].rows[0].session}"]`) as HTMLElement
    // the group band holds the name; the row does not repeat it
    expect(within(row).queryByText('bizdemo')).toBeNull()
    expect(screen.getByText('$1,500.50')).toBeTruthy()
  })
})
