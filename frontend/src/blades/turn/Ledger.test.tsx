// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { Ledger, type LedgerProps } from './Ledger'
import { buildNodes, visibleRows, type ViewOpts } from './rowList.logic'
import { toolNamesOfCalls } from './rowName'
import { agent, at, call, gap, tool } from './testRows'
import { ledgerGrid, maxByMeasure } from './turn.logic'
import type { AnyRow } from '../../api/types'

class NoopObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}
beforeEach(() => vi.stubGlobal('ResizeObserver', NoopObserver))
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

const base: ViewOpts = { openAgents: new Set(), zoom: null, search: null, order: null, showAll: false, unfolded: new Set() }

function mount(rows: AnyRow[], o: Partial<ViewOpts> = {}, p: Partial<LedgerProps> = {}, width = 1004) {
  const nodes = buildNodes(rows)
  const opts = { ...base, ...o }
  const items = visibleRows(nodes, opts)
  const calls = { open: vi.fn(), toggle: vi.fn(), unfold: vi.fn(), all: vi.fn(), more: vi.fn(), retry: vi.fn() }
  const grid = ledgerGrid(width)
  const view = render(
    <Ledger
      items={items} turnEmpty={false} loading={false} search="" zoomed={false} order={opts.order}
      env={{ tz: 'Asia/Kolkata', grid, measure: opts.order === 'duration' ? 'time' : 'cost', max: maxByMeasure(items), cursorId: null, onOpen: calls.open }}
      toolNames={toolNamesOfCalls(nodes)} openAgents={opts.openAgents} firstTurnIsHuman maxGapMs={600_000} scroll={{ n: 0, mode: 'nearest' }}
      onToggleAgent={calls.toggle} onUnfold={calls.unfold} onShowAll={calls.all} onBandMore={calls.more} onBandRetry={calls.retry}
      {...p}
    />,
  )
  return { ...view, calls, items }
}
const row = (id: string) => document.querySelector(`[data-row-id$="${id}"]`) as HTMLElement

describe('Ledger rows (TRN-30 to TRN-34)', () => {
  it('draws a call with its first said line, the final tag, the model mark, cost and tokens', () => {
    mount([call('a', { cost_usd: 1.5, steps: [{ step_id: 's', verb: 'answer', summary: 'All done', snippet: '' }] })])
    const r = row('llm/a')
    expect(within(r).getByText('final')).toBeTruthy()
    expect(within(r).getByText('All done')).toBeTruthy()
    expect(within(r).getByText('$1.50')).toBeTruthy()
    expect(within(r).getByText('100')).toBeTruthy()
    expect(within(r).getByText('13:00:00')).toBeTruthy() // 07:30:00Z in Asia/Kolkata
  })
  it('names a call from the tool rows after it', () => {
    mount([call('a'), tool('t1', { tool_name: 'Read', verb: 'read', summary: 'Read it', sec: 1 })])
    expect(within(row('llm/a')).getByText('Tool request: Read')).toBeTruthy()
  })
  it('draws a failed tool with circle-alert and "exit 1", a derived time with ≈ and the cd stripped from its target', () => {
    mount([tool('t', { summary: 'Run tests', command: 'cd /x/y && pytest -q', failed: true, exit_code: 1, status: 'exit:1', time_prov: 'derived', dur_ms: 45_000 })])
    const r = row('tool/t')
    expect(within(r).getByText('exit 1')).toBeTruthy()
    expect(within(r).getByText('pytest -q')).toBeTruthy()
    expect(within(r).getByText('≈45s')).toBeTruthy()
  })
  it('draws a skill load in 600 with its sub-text', () => {
    mount([tool('s', { skill_load: 'writing-specs', skill_via: 'read', summary: 'x' })])
    const r = row('tool/s')
    expect(within(r).getByText('writing-specs').className).toContain('font-semibold')
    expect(within(r).getByText('SKILL.md read')).toBeTruthy()
  })
  it('draws the silence and waits flags in ink3', () => {
    mount([tool('t', { max_silence_ms: 420_000, waits_on_user: true })])
    const r = row('tool/t')
    expect(within(r).getByText('no records 7m')).toBeTruthy()
    expect(within(r).getByText('waits on you')).toBeTruthy()
  })
  it('draws an agent row with its names, sub-text, row count button and whole cost', () => {
    const { calls } = mount([agent('ag', { agent_names: ['explorer'], description: 'Survey', cost_usd: 19.88 })])
    const r = row('engagement/ag')
    expect(within(r).getByText('explorer')).toBeTruthy()
    expect(within(r).getByText('started · Survey')).toBeTruthy()
    expect(within(r).getByText('$19.88')).toBeTruthy()
    fireEvent.click(within(r).getByRole('button', { name: '5 rows' }))
    expect(calls.toggle).toHaveBeenCalledWith(r.dataset.rowId)
    expect(calls.open).not.toHaveBeenCalled() // the toggle does not open the detail
  })
  it('draws a gap with its duration, label and reason on an l2 row', () => {
    mount([gap(0, 3720, { label: 'limit' })])
    const r = document.querySelector('[data-row-id^="gap-"]') as HTMLElement
    expect(r.className).toContain('bg-l2')
    expect(within(r).getAllByText('1h02m')).toHaveLength(2) // the name and the Time column
    expect(within(r).getByText('Usage limit (inferred)')).toBeTruthy()
  })
  it('opens the row on click', () => {
    const { calls } = mount([call('a')])
    fireEvent.click(row('llm/a'))
    expect(calls.open).toHaveBeenCalledWith(row('llm/a').dataset.rowId)
  })
})

describe('Ledger header, states and folds', () => {
  it('names the order in the column head', () => {
    mount([call('a')], { order: 'cost' })
    expect(screen.getByText('Top by cost')).toBeTruthy()
    expect(screen.getByText('by cost')).toBeTruthy()
    cleanup()
    mount([call('a')])
    expect(screen.getByText('As it ran')).toBeTruthy()
  })
  it('drops the bar column under 860 px', () => {
    mount([call('a')], {}, {}, 640)
    expect(screen.queryByText('by cost')).toBeNull()
    expect(screen.getByText('Tokens')).toBeTruthy()
  })
  it('shows a quiet fold and unfolds it on click', () => {
    const rows = [0, 1, 2, 3].map((i) => tool(`q${i}`, { sec: i, dur_ms: 1000 }))
    const { calls } = mount(rows)
    const btn = screen.getByText('Show 4 quiet rows').closest('button')!
    expect(within(btn).getByText(/0 calls · 4 tools/)).toBeTruthy()
    fireEvent.click(btn)
    expect(calls.unfold).toHaveBeenCalledWith(btn.dataset.foldKey)
  })
  it('shows "Show N more" with the hidden cost in the Cost order', () => {
    const rows = Array.from({ length: 17 }, (_, i) => call(`m${i}`, { sec: i, cost_usd: 17 - i }))
    const { calls } = mount(rows, { order: 'cost' })
    const btn = screen.getByText('Show 2 more').closest('button')!
    expect(within(btn).getByText('$3.00')).toBeTruthy() // 2 + 1
    fireEvent.click(btn)
    expect(calls.all).toHaveBeenCalled()
  })
  it('shows the empty messages', () => {
    mount([call('a', { sec: 0 })], { zoom: { start_ms: at(500), end_ms: at(600) } }, { zoomed: true })
    expect(screen.getByText('No rows in this range.')).toBeTruthy()
    cleanup()
    mount([call('a')], { search: new Set() }, { search: 'zzz' })
    expect(screen.getByText('No rows match “zzz”.')).toBeTruthy()
    cleanup()
    mount([], {}, { turnEmpty: true })
    expect(screen.getByText('No model calls in this turn.')).toBeTruthy()
  })
  it('shows skeleton rows while loading', () => {
    mount([], {}, { loading: true })
    expect(screen.getByRole('status', { name: 'Loading' })).toBeTruthy()
  })
})

describe('Ledger agent bands (TRN-35)', () => {
  it('draws band items: a skeleton, an error card with Retry, and "Show N more"', () => {
    const ag = agent('ag')
    const nodes = buildNodes([ag])
    const open = new Set([ag.id])
    const make = (bands: ViewOpts['bands']) => visibleRows(nodes, { ...base, openAgents: open, bands })
    const common = { turnEmpty: false, loading: false, search: '', zoomed: false, order: null, toolNames: new Map(), openAgents: open, firstTurnIsHuman: true, maxGapMs: 1, scroll: { n: 0, mode: 'nearest' as const }, onToggleAgent: () => {}, onUnfold: () => {}, onShowAll: () => {}, onBandMore: vi.fn(), onBandRetry: vi.fn() }
    const env = { tz: 'UTC', grid: ledgerGrid(1004), measure: 'cost' as const, max: { cost: 1, time: 1, tokens: 1 }, cursorId: null, onOpen: () => {} }
    const { rerender } = render(<Ledger {...common} env={env} items={make(new Map([[ag.id, { status: 'loading' }]]))} />)
    expect(screen.getByRole('status', { name: 'Loading' })).toBeTruthy()
    rerender(<Ledger {...common} env={env} items={make(new Map([[ag.id, { status: 'error', message: 'boom' }]]))} />)
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(common.onBandRetry).toHaveBeenCalledWith(ag.id)
    expect(screen.getByText('boom')).toBeTruthy()
  })

  it('has no "N rows" control on an agent row in the Cost and Duration orders (TRN-14)', () => {
    const ag = agent('ag', { n_rows: 3 })
    const nodes = buildNodes([ag])
    const common = { turnEmpty: false, loading: false, search: '', zoomed: false, toolNames: new Map(), openAgents: new Set<string>(), firstTurnIsHuman: true, maxGapMs: 1, scroll: { n: 0, mode: 'nearest' as const }, onToggleAgent: () => {}, onUnfold: () => {}, onShowAll: () => {}, onBandMore: () => {}, onBandRetry: () => {} }
    const env = { tz: 'UTC', grid: ledgerGrid(1004), measure: 'cost' as const, max: { cost: 1, time: 1, tokens: 1 }, cursorId: null, onOpen: () => {} }
    const view = (order: 'cost' | 'duration' | null) => <Ledger {...common} order={order} env={env} items={visibleRows(nodes, { ...base, order })} />
    const { rerender } = render(view(null))
    expect(screen.getByRole('button', { name: '3 rows' })).toBeTruthy()
    for (const order of ['cost', 'duration'] as const) {
      rerender(view(order))
      expect(screen.queryByRole('button', { name: /rows/ })).toBeNull()
    }
  })
})
