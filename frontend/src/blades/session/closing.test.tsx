// @vitest-environment jsdom
import { Component, type ReactNode } from 'react'
import { act, cleanup, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from '../../App'
import { fixtures } from '../../api/__fixtures__'
import { responseCache } from '../../api/useApi'
import { createNav, type Nav } from '../../state/nav'
import { browserUrl } from '../../state/url'

const S = fixtures.sessionDetail.session.session

function reply(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })
}

const caught: Error[] = []
class Catch extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false }
  static getDerivedStateFromError() {
    return { failed: true }
  }
  componentDidCatch(e: Error) {
    caught.push(e)
  }
  render() {
    return this.state.failed ? <p>boundary</p> : this.props.children
  }
}

class NoopObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}

beforeEach(() => {
  caught.length = 0
  responseCache.clear()
  vi.stubGlobal('ResizeObserver', NoopObserver)
  vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} }))
  // The strip times its slide with performance.now(); jsdom hands rAF callbacks a different clock.
  vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => setTimeout(() => cb(performance.now()), 16))
  vi.stubGlobal('cancelAnimationFrame', (id: number) => clearTimeout(id))
  vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(1440)
  Element.prototype.scrollIntoView = () => {}
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => {
      const u = new URL(url, 'http://x')
      if (u.pathname.endsWith('/turns')) return reply(200, fixtures.sessionTurns)
      if (u.pathname.endsWith('/find')) return reply(200, fixtures.sessionFind)
      if (u.pathname.endsWith(`/sessions/${encodeURIComponent(S)}`) || u.pathname.endsWith(`/sessions/${S}`)) return reply(200, fixtures.sessionDetail)
      return reply(404, { error: 'not served in this test' })
    }),
  )
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

/** Session, turn and detail blades open. The closing blades slide out as a second, freshly mounted copy (SYS-58), after the URL has lost their state. */
async function openAll(): Promise<Nav> {
  window.history.replaceState(null, '', `/?s=${encodeURIComponent(S)}&t=2&detail=1`)
  render(
    <Catch>
      <App />
    </Catch>,
  )
  await screen.findByLabelText('Session')
  return createNav(browserUrl)
}

const settle = () => new Promise((r) => setTimeout(r, 400)) // BLADE_MS is 280

describe('closing blades remount their content as a ghost (SES-3, SYS-58)', () => {
  for (const level of ['turn', 'session', 'explorer'] as const) {
    it(`closing to the ${level} blade (the blades after it slide out) does not throw`, async () => {
      const nav = await openAll()
      await act(async () => {
        nav.closeTo(level)
        await settle()
      })
      expect(caught.map((e) => e.message)).toEqual([])
    })
  }

  it('closing in two steps (detail, then turn, then session) one blade at a time does not throw', async () => {
    const nav = await openAll()
    for (const level of ['turn', 'session', 'explorer'] as const) {
      await act(async () => {
        nav.closeTo(level)
        await settle()
      })
    }
    expect(caught.map((e) => e.message)).toEqual([])
  })
})
