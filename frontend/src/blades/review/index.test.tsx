// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fixtures } from '../../api/__fixtures__'
import { clearEtags } from '../../api/client'
import type { ReviewReport } from '../../api/types'
import { responseCache } from '../../api/useApi'
import { useGlobalKeys } from '../../shell/keys'
import { createNav } from '../../state/nav'
import { browserUrl } from '../../state/url'
import { ScopeReviewBlade } from '.'

const S = fixtures.scopeReview as ReviewReport
const FIRST = S.findings[0].sessions[0]
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })

let calls: string[] = []
function serve(review: (url: URL) => Response | Promise<Response> = () => json(S)) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: string) => {
      calls.push(input)
      const u = new URL(input, 'http://x')
      if (u.pathname === '/api/ingest/status') return json(fixtures.ingestStatus)
      if (u.pathname === '/api/explorer/summary') return json(fixtures.explorerSummary)
      if (u.pathname === '/api/explorer/review') return review(u)
      throw new Error(`unexpected request ${input}`)
    }),
  )
}

const params = () => new URLSearchParams(window.location.search)
const at = (search: string) => window.history.replaceState(null, '', `/${search}`)
const reviewCalls = () => calls.filter((c) => c.startsWith('/api/explorer/review'))

class NoopObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}

beforeEach(() => {
  vi.stubGlobal('ResizeObserver', NoopObserver)
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date('2026-10-12T10:00:00+05:30'))
  responseCache.clear()
  clearEtags()
  calls = []
  at('?review=1')
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

describe('scope review blade: header and claim (REV-22)', () => {
  it('shows the title, the period, the session count and the scope claim', async () => {
    serve()
    render(<ScopeReviewBlade />)
    expect(await screen.findByRole('heading', { level: 1, name: 'Review' })).toBeTruthy()
    expect(await screen.findByText('Last 7 days · Oct 6 – Oct 12 · 35 sessions')).toBeTruthy()
    expect(screen.getByRole('heading', { level: 2 }).textContent).toBe('27h47m of session time in 35 sessions. 2h00m was lost to stalls (3 not measured). The largest cause was tests (8h20m).')
  })

  it('lists the period, each filter value in EXP-25 order, the quoted search and the session count, joined by " · " (no "filtered")', async () => {
    at('?review=1&q=rbac&branch=main&provider=codex')
    serve()
    render(<ScopeReviewBlade />)
    expect(await screen.findByText('Last 7 days · Oct 6 – Oct 12 · Codex · main · "rbac" · 35 sessions')).toBeTruthy()
    expect(screen.queryByText(/filtered/)).toBeNull()
  })

  it('shows the line from the scope state while the review is loading and when it failed, without the count', async () => {
    at('?review=1&q=rbac')
    serve(() => new Promise<Response>(() => {}))
    const loading = render(<ScopeReviewBlade />)
    expect(await screen.findByText('Last 7 days · Oct 6 – Oct 12 · "rbac"')).toBeTruthy()
    loading.unmount()
    responseCache.clear()
    serve(() => json({ error: 'boom', code: 'internal' }, 500))
    render(<ScopeReviewBlade />)
    await screen.findByRole('alert')
    expect(screen.getByText('Last 7 days · Oct 6 – Oct 12 · "rbac"')).toBeTruthy()
  })

  it('requests the explorer scope of the URL (REV-20)', async () => {
    at('?review=1&period=today&q=rbac&project=%2Fa')
    serve()
    render(<ScopeReviewBlade />)
    await screen.findByRole('heading', { level: 2 })
    expect(reviewCalls()).toEqual(['/api/explorer/review?from=2026-10-12&project=%2Fa&q=rbac&to=2026-10-12&tz=Asia%2FKolkata'])
  })
})

describe('scope review blade: cards (REV-22)', () => {
  it('lists the "Most in:" sessions of a card in the order of `sessions`, as links with their time', async () => {
    serve()
    render(<ScopeReviewBlade />)
    await screen.findByRole('heading', { level: 2 })
    const card = document.querySelector('[data-finding="stalled"]') as HTMLElement
    const line = within(card).getByText(/^Most in:/)
    expect(line.textContent).toBe('Most in: master-control-acme 1h35m · acme-wf-tui-graph 15m · acme-session-analysis 10m')
    expect(Array.from(card.querySelectorAll('[data-session-link]')).map((b) => b.textContent)).toEqual(['master-control-acme', 'acme-wf-tui-graph', 'acme-session-analysis'])
  })

  it('shows no "Most in:" line for a card with no sessions', async () => {
    serve()
    render(<ScopeReviewBlade />)
    await screen.findByRole('heading', { level: 2 })
    expect((document.querySelector('[data-finding="you"]') as HTMLElement).textContent).not.toContain('Most in:')
  })

  it('opens a "Most in:" session in Review as blade 1, replacing the scope review', async () => {
    serve()
    render(<ScopeReviewBlade />)
    await screen.findByRole('heading', { level: 2 })
    fireEvent.click(screen.getByRole('button', { name: 'master-control-acme' }))
    expect(params().get('s')).toBe(FIRST.session)
    expect(params().get('sview')).toBe('review')
    expect(params().get('review')).toBeNull()
  })

  it('returns to the scope review with Back from an opened session', async () => {
    serve()
    render(<ScopeReviewBlade />)
    await screen.findByRole('heading', { level: 2 })
    fireEvent.click(screen.getByRole('button', { name: 'master-control-acme' }))
    expect(params().get('review')).toBeNull()
    window.history.back()
    await waitFor(() => expect(params().get('review')).toBe('1'))
    expect(params().get('s')).toBeNull()
  })

  it('names the session in the evidence line and opens it at the row in Ledger', async () => {
    serve()
    render(<ScopeReviewBlade />)
    const link = await screen.findByRole('button', { name: /^Longest: master-control-acme, Mon Oct 12 \d\d:\d\d–\d\d:\d\d, 1h02m$/ })
    fireEvent.click(link)
    expect(params().get('s')).toBe(S.findings[0].longest!.session)
    expect(params().get('t')).toBe('2')
    expect(params().get('step')).toBe('gap-1791786480000')
    expect(params().get('detail')).toBe('1')
    expect(params().get('sview')).toBeNull() // Ledger
    expect(params().get('review')).toBeNull()
  })

  it('shows no evidence line when longest is null', async () => {
    serve()
    render(<ScopeReviewBlade />)
    await screen.findByRole('heading', { level: 2 })
    expect((document.querySelector('[data-finding="tests"]') as HTMLElement).textContent).not.toContain('Longest')
  })
})

describe('scope review blade: footnote (REV-22)', () => {
  it('adds the Codex or omp line with the unmeasured count', async () => {
    serve()
    render(<ScopeReviewBlade />)
    expect(await screen.findByText('3 sessions are Codex or omp: stalls are not measured for them, because their logs do not record when a background task finishes.')).toBeTruthy()
  })

  it('leaves it out when every session is measured', async () => {
    serve(() => json({ ...S, n_unmeasured: 0 }))
    render(<ScopeReviewBlade />)
    await screen.findByRole('heading', { level: 2 })
    expect(screen.queryByText(/Codex or omp/)).toBeNull()
  })
})

describe('scope review blade: scope changes, empty, loading, error (REV-23, REV-24)', () => {
  it('refetches when the scope changes and keeps the previous content until the new data arrives', async () => {
    let release: (r: Response) => void = () => {}
    const gate = new Promise<Response>((r) => (release = r))
    serve((u) => (u.searchParams.get('q') === 'rbac' ? gate : json(S)))
    render(<ScopeReviewBlade />)
    await screen.findByText(/27h47m of session time in 35 sessions/)
    act(() => createNav(browserUrl).replace({ q: 'rbac' }))
    await waitFor(() => expect(reviewCalls()).toHaveLength(2))
    expect(reviewCalls()[1]).toContain('q=rbac')
    expect(screen.getByText(/27h47m of session time in 35 sessions/)).toBeTruthy() // EXP-78: not a skeleton
    expect(screen.queryByRole('status', { name: 'Loading' })).toBeNull()
    release(json({ ...S, wall_ms: 3_600_000, lost_ms: 0, n_sessions: 2, findings: [] }))
    expect(await screen.findByText(/1h00m of session time in 2 sessions\. Nothing was lost to a stall \(3 not measured\)\./)).toBeTruthy()
  })

  it('shows "No sessions in this scope." and no bars or cards with 0 in-scope sessions', async () => {
    serve(() => json({ ...S, wall_ms: 0, lost_ms: 0, n_sessions: 0, n_unmeasured: 0, findings: [], causes: S.causes.map((c) => ({ ...c, ms: 0 })) }))
    render(<ScopeReviewBlade />)
    expect(await screen.findByText('No sessions in this scope.')).toBeTruthy()
    expect(document.querySelector('[data-bar]')).toBeNull()
    expect(document.querySelector('[data-finding]')).toBeNull()
  })

  it('shows skeletons while the review loads', async () => {
    serve(() => new Promise<Response>(() => {}))
    render(<ScopeReviewBlade />)
    expect(await screen.findByRole('status', { name: 'Loading' })).toBeTruthy()
    expect(screen.queryByRole('heading', { level: 2 })).toBeNull()
  })

  it('shows the error card with a working retry', async () => {
    let fail = true
    serve(() => (fail ? json({ error: 'boom', code: 'internal' }, 500) : json(S)))
    render(<ScopeReviewBlade />)
    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain("Couldn't load the review")
    fail = false
    fireEvent.click(within(alert).getByRole('button', { name: 'Retry' }))
    expect(await screen.findByText(/27h47m of session time/)).toBeTruthy()
  })
})

describe('scope review blade: same-named links, keys and crumb (REV-21, REV-22)', () => {
  const dup: ReviewReport = {
    ...S,
    findings: [{ ...S.findings[0], sessions: [S.findings[0].sessions[0], { ...S.findings[0].sessions[1], name: S.findings[0].sessions[0].name }, S.findings[0].sessions[2]] }],
  }

  it('adds the start "Ddd HH:MM" to each of two links that share a name, in the text and the accessible name, and to no other', async () => {
    serve(() => json(dup))
    render(<ScopeReviewBlade />)
    await screen.findByRole('heading', { level: 2 })
    const links = Array.from(document.querySelectorAll('[data-session-link]')).map((b) => b.textContent)
    expect(links).toEqual(['master-control-acme Mon 11:58', 'master-control-acme Sun 11:56', 'acme-session-analysis'])
    expect(screen.getByRole('button', { name: 'master-control-acme Mon 11:58' })).toBeTruthy()
  })

  function Keys() {
    useGlobalKeys({ bladeKeys: ['explorer', 'review'], focusKey: 'review', setFocusIndex: () => {}, closeRightmost: () => {}, togglePalette: () => {} })
    return <ScopeReviewBlade />
  }

  it('closes on r (back to the explorer)', async () => {
    serve()
    createNav(browserUrl).openBlade('review', {})
    render(<Keys />)
    await screen.findByRole('heading', { level: 2 })
    fireEvent.keyDown(document.body, { key: 'r' })
    await waitFor(() => expect(params().get('review')).toBeNull())
  })

  it('closes every blade for the "Sessions" crumb after a session was opened from the review', async () => {
    serve()
    const nav = createNav(browserUrl)
    nav.openBlade('review', {})
    nav.openBlade('session', { s: 'a', sview: 'review' })
    nav.closeTo('explorer') // what the crumb calls
    expect(params().get('s')).toBeNull()
    expect(params().get('review')).toBeNull()
  })
})
