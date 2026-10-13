// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fixtures } from '../../../api/__fixtures__'
import { responseCache } from '../../../api/useApi'
import { useGlobalKeys } from '../../../shell/keys'
import { PaletteHost } from './index'

function stubApi(palette: unknown = fixtures.palette) {
  const f = vi.fn(async (input: RequestInfo | URL) => {
    const u = new URL(String(input), 'http://x')
    const routes: Record<string, unknown> = {
      '/api/ingest/status': fixtures.ingestStatus,
      '/api/explorer/sessions': fixtures.explorerSessions,
      '/api/explorer/days': fixtures.explorerDays,
      '/api/explorer/summary': fixtures.explorerSummary,
      '/api/palette': palette,
    }
    const body = routes[u.pathname]
    return new Response(JSON.stringify(body ?? { error: 'no route', code: 'not_found' }), { status: body ? 200 : 404, headers: { 'content-type': 'application/json' } })
  })
  vi.stubGlobal('fetch', f)
  return f
}

function Harness({ onClose }: { onClose: () => void }) {
  useGlobalKeys({ bladeKeys: ['explorer'], focusKey: 'explorer', setFocusIndex() {}, closeRightmost() {}, togglePalette() {} })
  return <PaletteHost open onClose={onClose} />
}

beforeEach(() => {
  Element.prototype.scrollIntoView = vi.fn()
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date('2026-10-12T06:00:00Z')) // Mon Oct 12 in Asia/Kolkata
  window.history.replaceState(null, '', '/')
  responseCache.clear()
})
afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

const input = () => screen.getByPlaceholderText('Jump to a session, project or date') as HTMLInputElement

describe('Palette', () => {
  it('EXP-67/68: empty input shows Dates, Filter by… and Largest in this period, with the key hints', async () => {
    stubApi()
    render(<Harness onClose={() => {}} />)
    expect(await screen.findByRole('heading', { name: 'Dates' })).toBeTruthy()
    expect(screen.getByRole('heading', { name: 'Filter by…' })).toBeTruthy()
    expect(await screen.findByRole('heading', { name: 'Largest in this period' })).toBeTruthy()
    for (const w of ['Today', 'Yesterday', 'Last 7 days']) expect(screen.getByText(w)).toBeTruthy()
    for (const e of ['model:opus', 'branch:main', 'provider:codex', 'project:bizdemo']) expect(screen.getByText(e)).toBeTruthy()
    expect(screen.getByText('move')).toBeTruthy()
    expect(screen.getByText('close')).toBeTruthy()
    expect(document.activeElement).toBe(input())
  })

  it('EXP-72: Enter on a date sets the period only; filters and search stay', async () => {
    window.history.replaceState(null, '', '/?q=rbac&model=claude-opus-5-5')
    stubApi()
    const close = vi.fn()
    render(<Harness onClose={close} />)
    await screen.findByText('Yesterday')
    fireEvent.keyDown(input(), { key: 'ArrowDown' })
    fireEvent.keyDown(input(), { key: 'Enter' })
    await waitFor(() => expect(close).toHaveBeenCalled())
    expect(window.location.search).toBe('?period=yesterday&q=rbac&model=claude-opus-5-5')
  })

  it('EXP-69: typing "oct 7" lists the date Wed Oct 7; picking it sets a one-day period', async () => {
    stubApi({ ...fixtures.palette, sessions: [], projects: [], filter_values: [] })
    render(<Harness onClose={() => {}} />)
    fireEvent.change(input(), { target: { value: 'oct 7' } })
    const row = await screen.findByText('Wed Oct 7')
    fireEvent.click(row)
    await waitFor(() => expect(window.location.search).toBe('?from=2026-10-07&to=2026-10-07'))
  })

  it('EXP-69: nothing matches says so, in sentence case, once the answer is in', async () => {
    stubApi({ ...fixtures.palette, sessions: [], projects: [], filter_values: [] })
    render(<Harness onClose={() => {}} />)
    fireEvent.change(input(), { target: { value: 'zzqq' } })
    expect(await screen.findByText('Nothing matches “zzqq”.')).toBeTruthy()
  })

  it('EXP-69: a typed filter prefix lists the values with counts; Enter adds the value and keeps the period', async () => {
    window.history.replaceState(null, '', '/?period=yesterday')
    stubApi()
    render(<Harness onClose={() => {}} />)
    fireEvent.change(input(), { target: { value: 'model:op' } })
    expect(await screen.findByText('opus 5.5')).toBeTruthy()
    expect(screen.getByText('150 sessions')).toBeTruthy()
    fireEvent.keyDown(input(), { key: 'Enter' })
    await waitFor(() => expect(window.location.search).toBe('?period=yesterday&model=claude-opus-5-5'))
  })

  it('EXP-72: a project row sets the project filter and, when all its sessions are automated, shows them', async () => {
    const p = { ...fixtures.palette, filter_values: [], sessions: [], projects: [{ ...fixtures.palette.projects[0], n_sessions: 4, n_automated: 4 }] }
    stubApi(p)
    render(<Harness onClose={() => {}} />)
    fireEvent.change(input(), { target: { value: 'biz' } })
    fireEvent.click(await screen.findByText('4 sessions'))
    await waitFor(() => expect(window.location.search).toBe('?project=%2FUsers%2Fa%2Fbizdemo&auto=show'))
  })

  it('EXP-72: a session row opens in blade 1', async () => {
    stubApi()
    render(<Harness onClose={() => {}} />)
    fireEvent.change(input(), { target: { value: 'rbac' } })
    await screen.findByRole('heading', { name: 'Sessions' })
    fireEvent.click(screen.getByText('Fix rbac sync'))
    await waitFor(() => expect(new URLSearchParams(window.location.search).get('s')).toBe(fixtures.palette.sessions[0].session))
  })

  it('EXP-72/73: a session the filters hide clears them, shows the toast, and Undo restores them while the session stays open', async () => {
    window.history.replaceState(null, '', '/?q=zzz&model=claude-sonnet-5-5&period=yesterday')
    const hidden = { ...fixtures.palette, sessions: [{ ...fixtures.palette.sessions[0], in_period: false, in_filters: false, first_day: '2026-10-06' }] }
    stubApi(hidden)
    render(<Harness onClose={() => {}} />)
    fireEvent.change(input(), { target: { value: 'rbac' } })
    await screen.findByRole('heading', { name: 'Sessions' })
    fireEvent.click(screen.getByText('Fix rbac sync'))
    await screen.findByText('Filters cleared to show this session')
    const now = new URLSearchParams(window.location.search)
    expect(now.get('s')).toBe(fixtures.palette.sessions[0].session)
    expect(now.get('q')).toBeNull()
    expect(now.getAll('model')).toEqual([])
    expect(now.get('from')).toBe('2026-10-06')
    fireEvent.click(screen.getByRole('button', { name: 'Undo' }))
    await waitFor(() => expect(new URLSearchParams(window.location.search).get('q')).toBe('zzz'))
    const back = new URLSearchParams(window.location.search)
    expect(back.getAll('model')).toEqual(['claude-sonnet-5-5'])
    expect(back.get('period')).toBe('yesterday')
    expect(back.get('from')).toBeNull()
    expect(back.get('s')).toBe(fixtures.palette.sessions[0].session)
    await waitFor(() => expect(screen.queryByText('Filters cleared to show this session')).toBeNull())
  })

  it('EXP-71: Esc closes, a click outside closes, a click inside does not', async () => {
    stubApi()
    const close = vi.fn()
    render(<Harness onClose={close} />)
    await screen.findByText('Yesterday')
    fireEvent.pointerDown(screen.getByRole('dialog'))
    expect(close).not.toHaveBeenCalled()
    fireEvent.pointerDown(screen.getByRole('dialog').parentElement!)
    expect(close).toHaveBeenCalledTimes(1)
    act(() => void fireEvent.keyDown(input(), { key: 'Escape' }))
    expect(close).toHaveBeenCalledTimes(2)
  })

  it('EXP-71: ArrowUp from the first row wraps to the last; pointer movement selects a row', async () => {
    stubApi()
    render(<Harness onClose={() => {}} />)
    await screen.findByRole('heading', { name: 'Largest in this period' })
    const rows = () => screen.getAllByRole('button').filter((b) => b.hasAttribute('data-selected'))
    expect(rows()[0].getAttribute('data-selected')).toBe('true')
    fireEvent.keyDown(input(), { key: 'ArrowUp' })
    expect(rows().at(-1)!.getAttribute('data-selected')).toBe('true')
    fireEvent.pointerMove(rows()[1])
    expect(rows()[1].getAttribute('data-selected')).toBe('true')
  })

  it('EXP-70: a day row shows that day\'s facts; Enter on a Filter by… row puts its prefix in the input', async () => {
    stubApi()
    render(<Harness onClose={() => {}} />)
    await screen.findByText('Yesterday')
    // the fixture day series answers every day window with its first bar: 18 sessions
    await waitFor(() => expect(screen.getAllByText(/^18 sessions · \$120\.50/).length).toBeGreaterThan(0))
    for (let i = 0; i < 3 + 2; i++) fireEvent.keyDown(input(), { key: 'ArrowDown' }) // 3 dates, then model:, branch:, provider:
    fireEvent.keyDown(input(), { key: 'Enter' })
    expect(input().value).toBe('provider:')
  })
})
