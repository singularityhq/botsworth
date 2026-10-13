// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { useState } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fixtures } from '../../api/__fixtures__'
import type { AgentDetail, AnyRow, CallDetail, Detail, FindResult, GapRow, SourceResponse, ToolDetail, TurnList, TurnRows } from '../../api/types'
import { responseCache } from '../../api/useApi'
import { CrumbProvider } from '../../shell/CrumbProvider'
import { useGlobalKeys } from '../../shell/keys'
import { turn as sessionTurn } from '../session/testRows'
import { agent, at, call, gap, tool } from '../turn/testRows'
import { StatusProvider } from '../shared/status'
import { TurnBlade } from '../turn'
import { DetailBlade } from './index'
import { clearSourceCache } from './useSourceText'

const S = fixtures.sessionDetail.session.session
const P = 'claude_code/a11ce5e5-1111-4222-8333-944455556666'
const T = (n: number) => `s/turn/${n}`
const idOf = (suffix: string) => `${P}/${suffix}`
const FRESH = fixtures.turnRows.freshness as TurnRows['freshness']

// ---- the session: two turns -------------------------------------------------------------------------------------------------

const TURNS: TurnList = {
  freshness: fixtures.sessionTurns.freshness as TurnList['freshness'],
  items: [
    sessionTurn(1, { turn: T(1), label: 'first ask', start_ms: at(0), end_ms: at(600), cost_usd: 25, wall_ms: 600_000 }),
    sessionTurn(2, { turn: T(2), label: 'second ask', start_ms: at(900), end_ms: at(1000), cost_usd: 1 }),
  ],
}

const C1 = call('c1', { sec: 0, cost_usd: 1.2, steps: [{ step_id: 's1', verb: 'say', summary: 'Running the tests now', snippet: '' }], tok: { in: 120, cache_read: 5000, cache_write: null, out: 800, reasoning: null, total: 5920 }, stop_reason: 'tool_use' })
const T1 = tool('t1', { sec: 10, summary: 'npm test', tool_name: 'Bash', verb: 'run', command_src: 'src://cmd1', exit_code: 2, status: 'exit:2', failed: true, result_bytes: 11_264, start_ms: at(10), dur_ms: 4000 })
const RD = tool('rd', { sec: 12, tool_name: 'Read', verb: 'read', summary: 'Read the module', target: 'src/a.ts', command: null, command_src: null, file_path: 'src/a.ts', result_bytes: 0 })
const AG = agent('ag', { sec: 20, cost_usd: 3.28, agent_names: ['auth-readiness'], n_rows: 2, run_ms: 780_000 })
const TURNS_LABEL2 = 'I hit my usage limit earlier, please continue from where you left off'
const GAPL = gap(100, 300, { label: 'limit', state: 'none', turn: T(1), next_turn: { turn: T(2), kind: 'human', origin: 'typed', label: TURNS_LABEL2, display_id: 'T2' }, running: [{ row: AG.id, agent_name: 'auth-readiness', start_ms: at(110), end_ms: at(380), cost_usd: 3.28 }] }) as GapRow
const C2 = call('c2', { sec: 420, cost_usd: null, model: 'mystery-1', steps: [] })
const ROWS1: AnyRow[] = [C1, T1, RD, AG, GAPL, C2]
const ROWS2: AnyRow[] = [call('d1', { sec: 900, steps: [{ step_id: 'x', verb: 'answer', summary: 'All done', snippet: '' }], is_final: true })]

const lines = (n: number) => Array.from({ length: n }, (_, i) => `out ${i + 1}`).join('\n')
const SOURCES: Record<string, { text: string; total?: number; block?: string | null } | 'gone'> = {
  'src://think1': { text: 'Let me think about the test run first.', block: 'thinking' },
  'src://said1': { text: 'Running the tests now.', block: 'text' },
  'src://cmd1': { text: 'cd /tmp/x && npm test --run \\\n  --reporter=dot', block: 'tool_result' },
  'src://out1': { text: lines(214), block: 'tool_result' },
  'src://ask1': { text: 'Check that the auth flow is ready.', block: 'text' },
  'src://msg1': { text: 'Please check the second issue.\nThanks.', block: 'text' },
  'src://in1': { text: '{"file_path":"src/a.ts"}', block: 'tool_use' },
  'src://gone': 'gone',
}

function callDetail(c: ReturnType<typeof call>, sections: CallDetail['sections'], o: Partial<CallDetail> = {}): CallDetail {
  const { type: _t, ...row } = c
  void _t
  return {
    freshness: FRESH, kind: 'call', position: { session: S, turn: T(1), display_id: 'T1', row: { type: 'call', id: c.id }, depth: 0 }, turn_cost_usd: 25,
    cost_in_usd: null, cost_out_usd: null, cost_cache_read_usd: null, cost_cache_write_usd: null, cost_basis: null, context_tokens: null, speed_tok_s: null,
    speed_basis: 'present', sections, ...row, ...o,
  }
}
function toolDetail(t: ReturnType<typeof tool>, o: Partial<ToolDetail> = {}): ToolDetail {
  const { type: _t, ...row } = t
  void _t
  return { freshness: FRESH, kind: 'tool', position: { session: S, turn: T(1), display_id: 'T1', row: { type: 'tool', id: t.id }, depth: 0 }, input_src: 'src://in1', output_src: 'src://out1', neighbours: { before: [], after: [] }, ...row, ...o }
}
const TOP = (n: number, usd: number) => call(`k${n}`, { depth: 1, cost_usd: usd, steps: [{ step_id: `k${n}`, verb: 'say', summary: `Call ${n}`, snippet: '' }] })
function agentDetail(): AgentDetail {
  const { type: _t, runs: _r, ...row } = AG
  void _t
  void _r
  return {
    freshness: FRESH, kind: 'agent', position: { session: S, turn: T(1), display_id: 'T1', row: { type: 'agent', id: AG.id }, depth: 0 }, turn_cost_usd: 25.0,
    focus_engagement: AG.id, runs: [{ engagement: AG.id, agent_name: 'auth-readiness', start_ms: at(20), end_ms: at(800), wall_ms: 780_000, ask_src: 'src://ask1' }],
    top_calls: [TOP(1, 1.114), TOP(2, 0.785), TOP(3, 0.552), TOP(4, 0.333), TOP(5, 0.2)], other_calls: { n: 7, cost_usd: 0.296 }, ...row,
  }
}

const DETAILS: Record<string, Detail> = {
  [C1.id]: callDetail(C1, [
    { part: 'thinking', src: 'src://think1', snippet: 'Let me think', bytes: 40, row: null },
    { part: 'text', src: 'src://said1', snippet: 'Running', bytes: 22, row: null },
    { part: 'tool_use', src: 'src://tu1', snippet: 'npm test', bytes: 60, row: { type: 'tool', id: T1.id } },
  ]),
  [T1.id]: toolDetail(T1),
  [RD.id]: toolDetail(RD, { input_src: 'src://in1' }),
  [AG.id]: agentDetail(),
  [C2.id]: callDetail(C2, []),
}

const reply = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })
const empty = (kind: string): FindResult => ({ freshness: fixtures.sessionFind.freshness as FindResult['freshness'], kind, total: 0, hits: [], per_turn: {} })

interface Opts {
  detailFail?: boolean
  detailDelay?: Promise<void>
  sources?: typeof SOURCES
  details?: Record<string, Detail>
  /** Rows added to turn 1: the turn blade drops a step its rows do not hold. */
  extra?: AnyRow[]
}
function serve(o: Opts = {}) {
  const calls: string[] = []
  const sources = o.sources ?? SOURCES
  const details = o.details ?? DETAILS
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => {
      calls.push(url)
      const u = new URL(url, 'http://x')
      const p = u.pathname
      if (p.endsWith('/turns')) return reply(200, TURNS)
      if (p.endsWith('/find')) return reply(200, empty(u.searchParams.get('kind')!))
      if (p === '/api/source') {
        const s = sources[u.searchParams.get('src')!]
        if (s === undefined || s === 'gone') return reply(404, { error: 'source file not found', code: 'source_missing' })
        const total = s.total ?? new TextEncoder().encode(s.text).length
        const off = Number(u.searchParams.get('offset') ?? 0)
        const text = off > 0 ? s.text.slice(-200) : s.text
        const body: SourceResponse = { freshness: fixtures.source.freshness as SourceResponse['freshness'], text, total_bytes: total, drift: false, file: 'f', locator: 'l', block: s.block ?? 'text' }
        return reply(200, body)
      }
      if (p.includes('/api/turns/') && p.endsWith('/rows')) {
        const n = Number(decodeURIComponent(p.split('/api/turns/')[1]).split('/turn/')[1].split('/')[0])
        const row = TURNS.items[n - 1] as TurnList['items'][number] & { type: 'turn' }
        const body: TurnRows = {
          freshness: FRESH,
          turn: { ...(fixtures.turnRows.turn as unknown as TurnRows['turn']), ...row, cost: { cost_usd: row.cost_usd, cost_lower_bound: false, n_unpriced: 0, main: 1, agents: 0 }, models_full: [], text_src: 'src://msg1', text_bytes: 36, n_turns_in_session: 2 },
          rows: n === 1 ? [...ROWS1, ...(o.extra ?? [])] : ROWS2,
          range: null,
        }
        return reply(200, body)
      }
      if (p.includes('/api/ops/') && p.endsWith('/detail')) {
        await o.detailDelay
        if (o.detailFail) return reply(500, { error: 'detail boom', code: 'internal' })
        const id = decodeURIComponent(p.slice('/api/ops/'.length, -'/detail'.length))
        const d = details[id]
        return d ? reply(200, d) : reply(404, { error: 'no such op', code: 'not_found' })
      }
      if (p.includes('/api/ops/') && p.endsWith('/rows')) {
        const kids = decodeURIComponent(p).includes('engagement/ag') ? [1, 2, 3, 4, 5].map((n) => TOP(n, 0.1 * n)) : []
        return reply(200, { freshness: FRESH, total: kids.length, offset: 0, limit: 500, rows: kids })
      }
      if (p.endsWith('/resolve')) return reply(404, { error: 'no such row', code: 'not_found' })
      return reply(200, fixtures.sessionDetail)
    }),
  )
  return calls
}

/** The key router sends keys to the focused blade; the turn blade stays mounted beside the detail, as in the app, and owns the cursor. */
let focus: 'turn' | 'detail' = 'detail'
let setFocusTo: (f: 'turn' | 'detail') => void = () => {}
function Harness() {
  const [f, setF] = useState(focus)
  setFocusTo = setF
  useGlobalKeys({ bladeKeys: ['turn', 'detail'], focusKey: f, setFocusIndex: () => {}, closeRightmost: () => {}, togglePalette: () => {} })
  return (
    <>
      <div data-blade="turn">
        <TurnBlade width={400} compact />
      </div>
      <DetailBlade width={900} compact={false} />
    </>
  )
}
function mount(query: string) {
  window.history.replaceState(null, '', `/?s=${encodeURIComponent(S)}&detail=1&${query}`)
  return render(
    <CrumbProvider>
      <StatusProvider>
        <Harness />
      </StatusProvider>
    </CrumbProvider>,
  )
}
/** Queries scoped to the detail blade: the turn blade beside it shows the same rows. */
const ui = new Proxy({} as typeof screen, {
  get: (_, k: string) => (...a: unknown[]) => (within(document.querySelector('[data-detail]') ?? document.body) as unknown as Record<string, (...x: unknown[]) => unknown>)[k](...a),
})
const params = () => new URLSearchParams(window.location.search)
const press = (key: string) => fireEvent.keyDown(document.body, { key })

class NoopObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}
beforeEach(() => {
  focus = 'detail'
  Element.prototype.scrollIntoView = () => {}
  responseCache.clear()
  clearSourceCache()
  vi.stubGlobal('ResizeObserver', NoopObserver)
  vi.stubGlobal('navigator', { ...navigator, clipboard: { writeText: vi.fn(async () => {}) } })
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('model call (DET-10 to DET-13, DET-70)', () => {
  it('draws the title, hero and strip from the turn row before the op detail arrives, with skeletons in the sections', async () => {
    let release!: () => void
    serve({ detailDelay: new Promise<void>((r) => (release = r)) })
    mount(`t=1&step=${encodeURIComponent(C1.id)}`)
    expect(await ui.findByRole('heading', { name: 'Running the tests now' })).toBeTruthy()
    expect(ui.getByText('$1.20')).toBeTruthy()
    expect(ui.getByText("5% of the turn's cost")).toBeTruthy()
    expect(ui.getByText('model time')).toBeTruthy()
    expect(ui.getAllByRole('status', { name: 'Loading' }).length).toBeGreaterThan(0)
    expect(ui.queryByText('Let me think about the test run first.')).toBeNull()
    await act(async () => release())
    expect(await ui.findByText('Let me think about the test run first.')).toBeTruthy()
    expect(ui.getByText('Running the tests now.')).toBeTruthy()
  })

  it('shows the token line without the null figure, the stop reason and the tool-call link', async () => {
    serve()
    mount(`t=1&step=${encodeURIComponent(C1.id)}`)
    const line = (await ui.findByText(/cache read/)).closest('p')!
    expect(line.textContent).toBe('in 120 · cache read 5k · out 800')
    expect(ui.getByText('stop tool_use')).toBeTruthy()
    const link = await ui.findByRole('button', { name: /npm test/ })
    expect(link.textContent).toContain('4s')
    fireEvent.click(link)
    expect(params().get('step')).toBe(T1.id)
  })

  it('an unpriced call says "No price" and which model has none', async () => {
    serve()
    mount(`t=1&step=${encodeURIComponent(C2.id)}`)
    expect(await ui.findByText('No price')).toBeTruthy()
    expect(ui.getByText('No price for mystery-1. This call is not in the cost totals.')).toBeTruthy()
  })

  it('a failed detail request shows "Couldn\'t load details" with Retry; the hero stays', async () => {
    const calls = serve({ detailFail: true })
    mount(`t=1&step=${encodeURIComponent(C1.id)}`)
    expect(await ui.findByText("Couldn't load details")).toBeTruthy()
    expect(ui.getByText('$1.20')).toBeTruthy()
    const n = calls.filter((u) => u.includes('/detail')).length
    fireEvent.click(ui.getByRole('button', { name: 'Retry' }))
    await waitFor(() => expect(calls.filter((u) => u.includes('/detail')).length).toBeGreaterThan(n))
  })

  it('Codex encrypted reasoning, redacted and empty thinking, each in its own words', async () => {
    const mk = (src: string, block: string, text: string) => ({ ...SOURCES, [src]: { text, block } })
    const detail = (reasoning: number | null) => ({
      [C1.id]: callDetail(C1, [{ part: 'thinking', src: 'src://tk', snippet: '', bytes: null, row: null }], { tok: { ...C1.tok, reasoning } }),
    })
    serve({ sources: mk('src://tk', 'reasoning', ''), details: detail(512) })
    mount(`t=1&step=${encodeURIComponent(C1.id)}`)
    expect(await ui.findByText('Reasoning is encrypted in the Codex log.')).toBeTruthy()
    expect(ui.getByText(/reasoning tokens/).textContent).toBe('512 reasoning tokens')
    expect(ui.getByText(/cache read/).closest('p')!.textContent).toContain('reasoning 512') // and in the token line (DET-12)
    cleanup()
    responseCache.clear()
    clearSourceCache()
    serve({ sources: mk('src://tk', 'redacted_thinking', ''), details: detail(null) })
    mount(`t=1&step=${encodeURIComponent(C1.id)}`)
    expect(await ui.findByText('Thinking redacted in the source.')).toBeTruthy()
    cleanup()
    responseCache.clear()
    clearSourceCache()
    serve({ sources: mk('src://tk', 'thinking', ''), details: detail(null) })
    mount(`t=1&step=${encodeURIComponent(C1.id)}`)
    expect(await ui.findByText('No thinking text in the source.')).toBeTruthy()
  })

  it('a Codex reasoning summary shows under "Reasoning summary"', async () => {
    serve({ sources: { ...SOURCES, 'src://tk': { text: 'Short summary', block: 'reasoning' } }, details: { [C1.id]: callDetail(C1, [{ part: 'thinking', src: 'src://tk', snippet: '', bytes: null, row: null }]) } })
    mount(`t=1&step=${encodeURIComponent(C1.id)}`)
    expect(await ui.findByRole('heading', { name: 'Reasoning summary' })).toBeTruthy()
    expect(ui.getByText('Short summary')).toBeTruthy()
  })
})

describe('tool (DET-20 to DET-24, DET-65, DET-66)', () => {
  it('a failed Bash: the exit status in the error colour, the full command, and the output head, divider and tail', async () => {
    serve()
    mount(`t=1&step=${encodeURIComponent(T1.id)}`)
    expect(await ui.findByRole('heading', { name: 'npm test' })).toBeTruthy()
    const status = ui.getByText('exit 2')
    expect(status.className).toContain('text-error')
    expect(ui.getByText('11 KB output')).toBeTruthy()
    expect((await ui.findByLabelText('command')).textContent).toBe('cd /tmp/x && npm test --run \\\n  --reporter=dot') // the source's command, not the stored preview
    const out = await ui.findByTestId('output-lines')
    expect(within(out).getByText(/out 1\b/)).toBeTruthy()
    expect(out.textContent).toContain('out 20')
    expect(out.textContent).not.toContain('out 21\n')
    const divider = ui.getByRole('button', { name: '… 189 more lines' })
    expect(divider.getAttribute('aria-expanded')).toBe('false')
    expect(ui.getByText(/out 214/)).toBeTruthy()
    expect(ui.getByText(/11 KB · 214 lines/)).toBeTruthy()
    fireEvent.click(divider)
    expect(ui.getByTestId('output-lines').textContent).toContain('out 100')
    expect(ui.getByRole('button', { name: 'Hide 189 lines' })).toBeTruthy()
  })

  it('exit 0 reads "exit 0" in ink3; a tool with no status shows no status', async () => {
    const ok = tool('t2', { sec: 1, exit_code: 0, status: 'ok', result_bytes: 0 })
    const none = tool('t3', { sec: 2, exit_code: null, status: null, result_bytes: 0 })
    serve({ details: { [ok.id]: toolDetail(ok), [none.id]: toolDetail(none) }, extra: [ok, none] })
    mount(`t=1&step=${encodeURIComponent(ok.id)}`)
    const s = await ui.findByText('exit 0')
    expect(s.className).toContain('text-ink3')
    expect(await ui.findByText('Empty')).toBeTruthy()
    cleanup()
    responseCache.clear()
    mount(`t=1&step=${encodeURIComponent(none.id)}`)
    await ui.findByText('Empty')
    expect(ui.queryByText(/^exit/)).toBeNull()
    expect(ui.queryByText('ok')).toBeNull()
  })

  it('source missing: one line and the pointer, and the other sections keep working', async () => {
    serve({ sources: { ...SOURCES, 'src://out1': 'gone' } })
    mount(`t=1&step=${encodeURIComponent(T1.id)}`)
    expect(await ui.findByText('Text not available: source file not found')).toBeTruthy()
    expect(ui.getByText('src://out1')).toBeTruthy()
    expect(await ui.findByLabelText('command')).toBeTruthy()
  })

  it('output larger than the 256 KB cut: the head, the real tail and "Text too large to show in full"', async () => {
    serve({ sources: { ...SOURCES, 'src://out1': { text: lines(300), total: 3_145_728, block: 'tool_result' } } })
    mount(`t=1&step=${encodeURIComponent(T1.id)}`)
    expect(await ui.findByText('Text too large to show in full: 3 MB')).toBeTruthy()
    expect(ui.getByText('src://out1')).toBeTruthy()
    expect(ui.queryByRole('button', { name: /more lines/ })).toBeNull()
  })

  it('a derived duration carries ≈ and a skill load names the skill', async () => {
    const d = tool('t4', { sec: 3, time_prov: 'derived', dur_ms: 2000, skill_load: 'writing-specs', skill_via: 'tool', tool_name: 'Skill', verb: 'skill', result_bytes: 0, exit_code: null, status: null, command_src: null, command: null, file_path: null, target: 'writing-specs' })
    serve({ details: { [d.id]: toolDetail(d) }, extra: [d] })
    mount(`t=1&step=${encodeURIComponent(d.id)}`)
    expect(await ui.findByText('≈2s')).toBeTruthy()
    expect(ui.getByText(/loaded here\./).textContent).toBe('writing-specs loaded here.')
  })

  it('a silent or waiting tool says so under the hero', async () => {
    const d = tool('t5', { sec: 3, max_silence_ms: 420_000, waits_on_user: true, result_bytes: 0 })
    serve({ details: { [d.id]: toolDetail(d) }, extra: [d] })
    mount(`t=1&step=${encodeURIComponent(d.id)}`)
    expect(await ui.findByText('No records for 7m inside this call. Its duration includes that time.')).toBeTruthy()
    expect(ui.getByText('This tool waits for your answer. Its duration includes your reply.')).toBeTruthy()
  })
})

describe('data states without a source (DET-69)', () => {
  it('a tool with no pointer for any section says so in one line', async () => {
    const d = tool('t6', { sec: 3, result_bytes: null, command: null, command_src: null, file_path: null, target: '', tool_name: 'Mystery', verb: 'x' })
    serve({ details: { [d.id]: toolDetail(d, { input_src: '', output_src: '' }) }, extra: [d] })
    mount(`t=1&step=${encodeURIComponent(d.id)}`)
    expect(await ui.findByText('No text recorded for this tool call.')).toBeTruthy()
  })

  it('a model call with no content blocks says so in one line', async () => {
    serve()
    mount(`t=1&step=${encodeURIComponent(C2.id)}`)
    expect(await ui.findByText('No text recorded for this model call.')).toBeTruthy()
  })
})

describe('a detail with no turn behind it', () => {
  it('drops detail=1 from the URL and draws nothing', async () => {
    serve()
    window.history.replaceState(null, '', `/?s=${encodeURIComponent(S)}&detail=1`)
    const { container } = render(
      <CrumbProvider>
        <StatusProvider>
          <Harness />
        </StatusProvider>
      </CrumbProvider>,
    )
    await waitFor(() => expect(params().get('detail')).toBeNull())
    expect(container.querySelector('[data-detail]')).toBeNull()
  })
})

describe('agent (DET-30 to DET-34)', () => {
  it('shows $3.28 and 13% of the turn, the ask, and five calls plus "7 other calls" that add up to the agent cost', async () => {
    serve({ details: { ...DETAILS, [AG.id]: { ...agentDetail(), turn_cost_usd: 25.2 } } })
    mount(`t=1&step=${encodeURIComponent(AG.id)}`)
    expect(await ui.findByText('$3.28')).toBeTruthy()
    expect(await ui.findByText("13% of the turn's cost")).toBeTruthy()
    expect(await ui.findByText('Check that the auth flow is ready.')).toBeTruthy()
    const rows = await ui.findAllByRole('button', { name: /^\$\d/ })
    expect(rows).toHaveLength(5)
    const cents = rows.reduce((s, r) => s + Math.round(Number(/\$(\d+\.\d\d)/.exec(r.textContent!)![1]) * 100), 0)
    const other = ui.getByText(/^7 other calls · \$/)
    const rest = Math.round(Number(/\$(\d+\.\d\d)/.exec(other.textContent!)![1]) * 100)
    expect(cents + rest).toBe(328)
  })

  it('the rows button shows and folds its rows', async () => {
    serve()
    mount(`t=1&step=${encodeURIComponent(AG.id)}`)
    const show = await ui.findByRole('button', { name: /Show its 2 rows in the turn/ })
    expect(show.textContent).toContain('l')
    fireEvent.click(show)
    expect(await ui.findByRole('button', { name: /Fold its rows/ })).toBeTruthy()
  })

  it('clicking a call moves the cursor to it', async () => {
    serve()
    mount(`t=1&step=${encodeURIComponent(AG.id)}`)
    fireEvent.click(await ui.findByRole('button', { name: /Call 1/ }))
    expect(params().get('step')).toBe(idOf('llm/k1'))
  })
})

describe('gap (DET-40 to DET-43)', () => {
  it('a usage-limit gap: duration, share of the turn, the reason, the inference line, before, after and the agents running', async () => {
    serve()
    mount(`t=1&step=${encodeURIComponent(GAPL.id)}`)
    expect(await ui.findByRole('heading', { name: '5m Usage limit (inferred)' })).toBeTruthy()
    expect(ui.getByText("50% of the turn's wall time")).toBeTruthy()
    expect(ui.getByText('Usage limit (inferred): the next turn resumes after a usage limit.')).toBeTruthy()
    expect(ui.getByText(`Inferred: the next turn starts with “${TURNS_LABEL2.slice(0, 60)}”.`)).toBeTruthy()
    const section = ui.getByRole('region', { name: 'Agents running during it' })
    const link = within(section).getByRole('button', { name: /auth-readiness/ })
    expect(link.textContent).toContain('$3.28')
    fireEvent.click(link)
    expect(params().get('step')).toBe(AG.id)
  })

  it('names the row before and after, linking to each', async () => {
    serve()
    mount(`t=1&step=${encodeURIComponent(GAPL.id)}`)
    const before = within(await ui.findByRole('region', { name: 'Before' })).getByRole('button')
    expect(before.textContent).toContain('auth-readiness')
    const after = within(ui.getByRole('region', { name: 'After' })).getByRole('button')
    fireEvent.click(after)
    expect(params().get('step')).toBe(C2.id)
  })
})

describe('message (DET-50)', () => {
  it('shows the full text, its character count and no stepper', async () => {
    serve()
    mount(`t=1&step=${encodeURIComponent(T(1))}`)
    expect(await ui.findByRole('heading', { name: 'Your message' })).toBeTruthy()
    expect(await ui.findByLabelText('message')).toBeTruthy()
    expect(ui.getByLabelText('message').textContent).toBe('Please check the second issue.\nThanks.')
    expect(ui.getByText('38 characters')).toBeTruthy()
    expect(ui.queryByRole('button', { name: 'Previous' })).toBeNull()
    expect(ui.getByRole('button', { name: 'Close' })).toBeTruthy()
  })

  it('j on the message goes to the turn’s first row; k to its last', async () => {
    serve()
    mount(`t=1&step=${encodeURIComponent(T(1))}`)
    await ui.findByLabelText('message')
    press('j')
    expect(params().get('step')).toBe(C1.id)
    cleanup()
    mount(`t=1&step=${encodeURIComponent(T(1))}`)
    await ui.findByLabelText('message')
    press('k')
    expect(params().get('step')).toBe(C2.id)
  })
})

describe('finder keys from the detail (SES-73)', () => {
  // The detail has the keys once a wait opens it; the finders are the turn's, so each key must still reach them.
  it.each([
    ['w', 'No wait in this session.'],
    ['W', 'No wait in this session.'],
    ['e', 'No failure in this session.'],
    ['s', 'No skill load in this session.'],
    ['S', 'No skill load in this session.'],
    ['a', 'No agent in this session.'],
  ])('%s runs its finder', async (key, text) => {
    serve()
    mount(`t=1&step=${encodeURIComponent(C1.id)}`)
    await ui.findByRole('heading', { name: 'Running the tests now' })
    await waitFor(() => {
      press(key)
      expect(screen.getByText(text)).toBeTruthy()
    })
  })
})

describe('stepping (DET-2, DET-4)', () => {
  it('shows "R of M" from the turn’s list; j and k move the cursor and the detail follows', async () => {
    serve()
    mount(`t=1&step=${encodeURIComponent(C1.id)}`)
    await ui.findByRole('heading', { name: 'Running the tests now' })
    expect(ui.getByLabelText('Position in the turn').textContent).toBe('1 of 6') // six rows: two calls, two tools, the agent and the gap
    press('j')
    expect(params().get('step')).toBe(T1.id)
    expect(await ui.findByRole('heading', { name: 'npm test' })).toBeTruthy()
    expect(ui.getByLabelText('Position in the turn').textContent).toBe('2 of 6')
    press('k')
    expect(params().get('step')).toBe(C1.id)
    expect(await ui.findByRole('heading', { name: 'Running the tests now' })).toBeTruthy()
  })

  it('section disclosures reset for the new row', async () => {
    serve()
    mount(`t=1&step=${encodeURIComponent(RD.id)}`)
    const raw = await ui.findByRole('button', { name: 'Raw input' })
    fireEvent.click(raw)
    expect(ui.getByRole('button', { name: 'Raw input' }).getAttribute('aria-expanded')).toBe('true')
    press('k')
    await ui.findByRole('heading', { name: 'npm test' })
    press('j')
    await ui.findByRole('heading', { name: 'Read the module' })
    expect((await ui.findByRole('button', { name: 'Raw input' })).getAttribute('aria-expanded')).toBe('false')
  })

  it('j past the last row hands off to the next turn’s first row, with a toast', async () => {
    serve()
    mount(`t=1&step=${encodeURIComponent(C2.id)}`)
    await ui.findByText('No price')
    press('j')
    expect(params().get('t')).toBe('2')
    expect((await screen.findByRole('status', { name: '' })).textContent).toContain('Turn 1 → turn 2: second ask')
    await waitFor(() => expect(params().get('step')).toBe(idOf('llm/d1')))
    expect(await ui.findByRole('heading', { name: 'All done' })).toBeTruthy()
  })

  it('the stepper buttons and Close', async () => {
    serve()
    mount(`t=1&step=${encodeURIComponent(C1.id)}`)
    await ui.findByRole('heading', { name: 'Running the tests now' })
    fireEvent.click(ui.getByRole('button', { name: 'Next' }))
    expect(params().get('step')).toBe(T1.id)
  })
})

describe('copy (DET-23)', () => {
  it('the copy button copies the shown command and shows a check for 1.2 s', async () => {
    serve()
    mount(`t=1&step=${encodeURIComponent(T1.id)}`)
    await ui.findByLabelText('command')
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    fireEvent.click(ui.getByRole('button', { name: 'Copy command' }))
    await act(async () => {})
    expect((navigator.clipboard.writeText as ReturnType<typeof vi.fn>).mock.calls[0][0]).toBe('cd /tmp/x && npm test --run \\\n  --reporter=dot')
    expect(ui.getByRole('button', { name: 'Copied' })).toBeTruthy()
    await act(async () => vi.advanceTimersByTime(1250))
    expect(ui.getByRole('button', { name: 'Copy command' })).toBeTruthy()
    vi.useRealTimers()
  })
})

describe('the turn cursor bridge (DET-4, DET-5, DET-33, DET-34)', () => {
  const last = async (f: 'turn' | 'detail') => {
    focus = f
    serve()
    mount(`t=1&step=${encodeURIComponent(C2.id)}&detail=1`)
    await ui.findByText('No price')
    press('j')
    await waitFor(() => expect(params().get('t')).toBe('2'))
    await waitFor(() => expect(params().get('step')).toBe(idOf('llm/d1')))
    const toast = (await screen.findByRole('status', { name: '' })).textContent
    const out = { t: params().get('t'), step: params().get('step'), toast }
    cleanup()
    responseCache.clear()
    return out
  }

  it('j on the last row from the detail hands off exactly as the turn blade’s own j does', async () => {
    const fromTurn = await last('turn')
    const fromDetail = await last('detail')
    expect(fromDetail).toEqual(fromTurn)
    expect(fromDetail.toast).toContain('Turn 1 → turn 2: second ask')
  })

  it('goTo on a row inside a loaded but folded band opens the band and does not bounce to row 1', async () => {
    serve()
    mount(`t=1&step=${encodeURIComponent(AG.id)}`)
    await ui.findByRole('button', { name: /Show its 2 rows in the turn/ })
    focus = 'turn'
    act(() => setFocusTo('turn'))
    press('l') // the turn loads the band and puts the cursor on its first row
    await waitFor(() => expect(params().get('step')).toBe(idOf('llm/k1')))
    press('h') // folds it: the pages stay loaded
    await waitFor(() => expect(params().get('step')).toBe(AG.id))
    act(() => setFocusTo('detail'))
    fireEvent.click(await ui.findByRole('button', { name: /Call 3/ }))
    await new Promise((r) => setTimeout(r, 400))
    expect(params().get('step')).toBe(idOf('llm/k3'))
    expect(params().get('t')).toBe('1')
    expect(screen.getAllByText('Call 3').length).toBeGreaterThan(1) // and the turn's list now shows the band row
  })

  it('"R of M" is the turn’s own count: opening a band in the turn changes it', async () => {
    serve()
    mount(`t=1&step=${encodeURIComponent(AG.id)}`)
    const before = (await ui.findByLabelText('Position in the turn')).textContent!
    act(() => setFocusTo('turn'))
    press('l')
    await waitFor(() => expect(params().get('step')).toBe(idOf('llm/k1')))
    await waitFor(() => expect(ui.getByLabelText('Position in the turn').textContent).not.toBe(before))
  })
})
