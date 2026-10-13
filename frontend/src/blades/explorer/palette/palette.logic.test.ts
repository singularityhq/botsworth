import { describe, expect, it } from 'vitest'
import type { PaletteResult, SessionRow } from '../../../api/types'
import { DEFAULT_URL_STATE, type UrlState } from '../../../state/url'
import {
  datePeriod, emptySections, factsText, FILTER_PREFIXES, flatItems, moveSelection, planFor, typedSections,
  type DateItem, type PaletteSession,
} from './palette.logic'

const TODAY = '2026-10-12' // Mon
const url = (o: Partial<UrlState> = {}): UrlState => ({ ...DEFAULT_URL_STATE, ...o })

const sess = (o: Partial<PaletteSession> = {}): PaletteSession => ({
  session: 'claude_code/aaaa/session/-', session_id: 'aaaa', provider: 'claude_code', name: 'Fix rbac sync', title_source: 'first_ask',
  project: '/p/bizdemo', project_name: 'bizdemo', color_slot: 1, start_ms: 1, cost_usd: 12, cost_lower_bound: false, n_unpriced: 0,
  is_automated: false, in_period: true, in_filters: true, first_day: '2026-10-06', ...o,
})
const largestRow = (session: string, cost: number): SessionRow => ({
  project: '/p', project_name: 'p', color_slot: 1, cost_usd: cost, cost_lower_bound: false, n_unpriced: 0, session, session_id: session,
  provider: 'codex', name: session, title_source: 'first_ask', start_ms: 1, end_ms: 2, ends_after_days: 0, tok_total: 1, wall_ms: 1,
  active_ms: 1, is_automated: false, n_agents: 0, git_branch: null, activity: { bin_ms: 1, tok: [] }, day_split: [],
})
/** A session as API-14 sends it: `first_day` is always present. */
const wire = (s: PaletteSession) => ({ ...s, first_day: s.first_day ?? '2026-10-06' })
const freshness = { last_ingest_ms: 1, parser_version: 'p', price_fingerprint: 'f', tz: 'Asia/Kolkata', schema_version: 7, api_version: 1 as const }
const data = (o: Partial<PaletteResult> = {}): PaletteResult => ({ freshness, filter_values: [], projects: [], sessions: [], ...o })

describe('sections (EXP-68, EXP-69)', () => {
  it('empty input: Dates (Today, Yesterday, Last 7 days), Filter by…, Largest in this period', () => {
    const s = emptySections(TODAY, [largestRow('a', 9), largestRow('b', 8)])
    expect(s.map((x) => x.title)).toEqual(['Dates', 'Filter by…', 'Largest in this period'])
    expect(s[0].items.map((i) => i.kind === 'date' && i.label)).toEqual(['Today', 'Yesterday', 'Last 7 days'])
    expect(s[1].items.map((i) => i.kind === 'prefix' && i.example)).toEqual(['model:opus', 'branch:main', 'provider:codex', 'project:bizdemo'])
    expect(s[2].items).toHaveLength(2)
  })
  it('Largest keeps the 6 rows the server sent, in order, and is left out until they arrive', () => {
    const rows = Array.from({ length: 8 }, (_, i) => largestRow(`r${i}`, 100 - i))
    expect(emptySections(TODAY, rows)[2].items).toHaveLength(6)
    expect(emptySections(TODAY, []).map((x) => x.id)).toEqual(['dates', 'filter-by'])
  })
  it('a Largest row is in period and in filters by construction', () => {
    const item = emptySections(TODAY, [largestRow('a', 1)])[2].items[0]
    expect(item.kind === 'session' && item.row).toMatchObject({ in_period: true, in_filters: true })
  })
  it('the four filter prefixes in order', () => {
    expect(FILTER_PREFIXES.map((f) => f.kind)).toEqual(['model', 'branch', 'provider', 'project'])
  })
  it('typed input: Filters, Dates, Projects, Sessions, in that order, only those with results', () => {
    const d = data({
      filter_values: [{ kind: 'model', value: 'claude-opus-5-5', label: 'opus 5.5', n: 150 }],
      projects: [{ project: '/p/oct', project_name: 'oct', color_slot: 2, cost_usd: 1, cost_lower_bound: false, n_unpriced: 0, n_sessions: 3, n_automated: 0 }],
      sessions: [wire(sess())],
    })
    expect(typedSections('oct 7', TODAY, d).map((s) => s.title)).toEqual(['Filters', 'Dates', 'Projects', 'Sessions'])
    expect(typedSections('rbac', TODAY, data({ sessions: [wire(sess())] })).map((s) => s.title)).toEqual(['Sessions'])
    expect(typedSections('zzz', TODAY, data())).toEqual([])
  })
  it('input that matches a date and sessions lists the dates first', () => {
    const t = typedSections('oct 7', TODAY, data({ sessions: [wire(sess())] })).map((s) => s.id)
    expect(t).toEqual(['dates', 'sessions'])
  })
  it('date forms: oct 7, 7 oct, 10/07, 2026-10-07 all give Wed Oct 7', () => {
    for (const text of ['oct 7', '7 oct', '10/07', '2026-10-07']) {
      const dates = typedSections(text, TODAY, undefined)[0].items
      expect(dates).toHaveLength(1)
      expect(dates[0]).toMatchObject({ kind: 'date', label: 'Wed Oct 7', from: '2026-10-07', to: '2026-10-07' })
    }
  })
  it('today, yesterday and weekday names give the latest such day up to today', () => {
    expect(typedSections('yesterday', TODAY, undefined)[0].items[0]).toMatchObject({ from: '2026-10-11' })
    expect(typedSections('wed', TODAY, undefined)[0].items[0]).toMatchObject({ from: '2026-10-07' })
    expect(typedSections('mon', TODAY, undefined)[0].items[0]).toMatchObject({ from: TODAY })
  })
  it('range forms: this week, last week, last 7 / 7d, all', () => {
    const range = (text: string) => typedSections(text, TODAY, undefined)[0].items[0] as DateItem
    expect(range('this week')).toMatchObject({ preset: 'thisweek', from: '2026-10-12', to: TODAY })
    expect(range('last week')).toMatchObject({ preset: 'lastweek', from: '2026-10-05', to: '2026-10-11' })
    expect(range('7d')).toMatchObject({ preset: 'last7', from: '2026-10-06', to: TODAY })
    expect(range('last 7')).toMatchObject({ preset: 'last7' })
    expect(range('all')).toMatchObject({ preset: 'all', from: null, to: null })
  })
  it('typing a filter prefix shows the server values as Filters with their counts', () => {
    const d = data({ filter_values: [{ kind: 'branch', value: 'main', label: 'main', n: 42 }] })
    const s = typedSections('branch:ma', TODAY, d)
    expect(s[0]).toMatchObject({ id: 'filters', title: 'Filters' })
    expect(s[0].items[0]).toMatchObject({ kind: 'filter', filter: 'branch', value: 'main', n: 42 })
  })
  it('flattens sections in display order for the selection', () => {
    expect(flatItems(emptySections(TODAY, [largestRow('a', 1)]))).toHaveLength(3 + 4 + 1)
  })
})

describe('selection (EXP-71)', () => {
  it('moves and wraps in both directions', () => {
    expect(moveSelection(0, 1, 3)).toBe(1)
    expect(moveSelection(2, 1, 3)).toBe(0)
    expect(moveSelection(0, -1, 3)).toBe(2)
  })
  it('stays at 0 with no rows', () => {
    expect(moveSelection(0, 1, 0)).toBe(0)
  })
})

describe('date period', () => {
  const day = (from: string): DateItem => ({ kind: 'date', key: from, label: '', preset: null, from, to: from })
  it('today and yesterday become their presets, any other day a one-day custom range', () => {
    expect(datePeriod(day(TODAY), TODAY)).toEqual({ kind: 'preset', preset: 'today' })
    expect(datePeriod(day('2026-10-11'), TODAY)).toEqual({ kind: 'preset', preset: 'yesterday' })
    expect(datePeriod(day('2026-10-07'), TODAY)).toEqual({ kind: 'custom', from: '2026-10-07', to: '2026-10-07' })
  })
  it('a range row keeps its preset', () => {
    expect(datePeriod({ kind: 'date', key: 'x', label: '', preset: 'last7', from: '2026-10-06', to: TODAY }, TODAY)).toEqual({ kind: 'preset', preset: 'last7' })
  })
})

describe('actions (EXP-72)', () => {
  const dateRow = (): DateItem => typedSections('oct 7', TODAY, undefined)[0].items[0] as DateItem

  it('a date row sets the period and nothing else: search and filters stay, all blades close', () => {
    const cur = url({ models: ['claude-opus-5-5'], q: 'rbac', s: 'x', t: 2 })
    const plan = planFor(dateRow(), { url: cur, today: TODAY })
    expect(plan).toEqual({ type: 'scope', patch: { period: { kind: 'custom', from: '2026-10-07', to: '2026-10-07' }, s: null } })
    // the keys that must not be in the patch
    expect(Object.keys((plan as { patch: object }).patch).sort()).toEqual(['period', 's'])
  })

  it('a filter row adds the value to its filter, keeps the others, closes blades, leaves the period', () => {
    const item = { kind: 'filter', key: 'k', filter: 'model', value: 'claude-opus-5-5', label: 'opus 5.5', n: 1 } as const
    const plan = planFor(item, { url: url({ models: ['claude-sonnet-5-5'], s: 'x' }), today: TODAY })
    expect(plan).toEqual({ type: 'scope', patch: { models: ['claude-sonnet-5-5', 'claude-opus-5-5'], s: null } })
  })
  it('a filter value already selected is not added twice', () => {
    const item = { kind: 'filter', key: 'k', filter: 'branch', value: 'main', label: 'main', n: 1 } as const
    const plan = planFor(item, { url: url({ branches: ['main'] }), today: TODAY })
    expect(plan).toMatchObject({ patch: { branches: ['main'] } })
  })
  it('each filter kind writes its own list', () => {
    const f = (filter: 'provider' | 'branch' | 'project', value: string) =>
      planFor({ kind: 'filter', key: 'k', filter, value, label: value, n: 1 }, { url: url(), today: TODAY })
    expect(f('provider', 'codex')).toMatchObject({ patch: { providers: ['codex'] } })
    expect(f('branch', 'dev')).toMatchObject({ patch: { branches: ['dev'] } })
    expect(f('project', '/p')).toMatchObject({ patch: { projects: ['/p'] } })
  })

  const projectRow = (n_sessions: number, n_automated: number) => ({
    kind: 'project' as const, key: 'p', row: { project: '/p/b', project_name: 'b', color_slot: 1 as const, cost_usd: 1, cost_lower_bound: false, n_unpriced: 0, n_sessions, n_automated },
  })
  it('a project row sets the project filter to that project only, period unchanged', () => {
    const plan = planFor(projectRow(5, 1), { url: url({ projects: ['/other'] }), today: TODAY })
    expect(plan).toEqual({ type: 'scope', patch: { projects: ['/p/b'], s: null } })
  })
  it('a project whose sessions are all automated also shows automated sessions (D38 M5)', () => {
    expect(planFor(projectRow(4, 4), { url: url(), today: TODAY })).toMatchObject({ patch: { projects: ['/p/b'], auto: true } })
  })
  it('a project with no sessions does not turn automated on (D41 N2)', () => {
    const plan = planFor(projectRow(0, 0), { url: url(), today: TODAY })
    expect((plan as { patch: object }).patch).not.toHaveProperty('auto')
  })

  it('a session in the period and the filters just opens', () => {
    const plan = planFor({ kind: 'session', key: 's', row: sess() }, { url: url(), today: TODAY })
    expect(plan).toEqual({ type: 'session', session: 'claude_code/aaaa/session/-', patch: { s: 'claude_code/aaaa/session/-' }, toast: null })
  })
  it('a session outside the period sets the period to its first day with a model call, not its start day', () => {
    const plan = planFor({ kind: 'session', key: 's', row: sess({ in_period: false, first_day: '2026-10-06' }) }, { url: url(), today: TODAY })
    expect(plan).toMatchObject({ patch: { s: 'claude_code/aaaa/session/-', period: { kind: 'custom', from: '2026-10-06', to: '2026-10-06' } }, toast: null })
  })
  it('a session hidden by the search or filters clears them and offers Undo that restores search, filters and period', () => {
    const cur = url({ q: 'rbac', models: ['claude-opus-5-5'], branches: ['dev'], projects: ['/x'], providers: ['codex'], period: { kind: 'preset', preset: 'yesterday' } })
    const plan = planFor({ kind: 'session', key: 's', row: sess({ in_period: false, in_filters: false, first_day: '2026-10-06' }) }, { url: cur, today: TODAY })
    if (plan.type !== 'session') throw new Error('expected a session plan')
    expect(plan.patch).toMatchObject({ q: '', models: [], branches: [], projects: [], providers: [], period: { kind: 'custom', from: '2026-10-06', to: '2026-10-06' } })
    expect(plan.toast?.message).toBe('Filters cleared to show this session')
    expect(plan.toast?.patch).toEqual({
      q: 'rbac', models: ['claude-opus-5-5'], branches: ['dev'], projects: ['/x'], providers: ['codex'], auto: false, period: { kind: 'preset', preset: 'yesterday' },
    })
    // Undo never touches the open session
    expect(plan.toast?.patch).not.toHaveProperty('s')
  })
  it('an automated session hidden by the automated rule turns automated on, and Undo turns it off again', () => {
    const plan = planFor({ kind: 'session', key: 's', row: sess({ in_filters: false, is_automated: true }) }, { url: url(), today: TODAY })
    if (plan.type !== 'session') throw new Error('expected a session plan')
    expect(plan.patch.auto).toBe(true)
    expect(plan.toast?.patch.auto).toBe(false)
  })
  it('a session outside the period with no first_day is a server bug and fails loudly', () => {
    expect(() => planFor({ kind: 'session', key: 's', row: sess({ in_period: false, first_day: null }) }, { url: url(), today: TODAY })).toThrow(/first_day/)
  })

  it('a Filter by… row puts its prefix into the input and keeps the palette open', () => {
    const item = emptySections(TODAY, [])[1].items[2]
    expect(planFor(item, { url: url(), today: TODAY })).toEqual({ type: 'prefix', text: 'provider:' })
  })
})

describe('date facts (EXP-70)', () => {
  it('reads like 19 sessions · $559.00 · 1.93B tokens', () => {
    expect(factsText(19, 559, false, 1_930_000_000)).toBe('19 sessions · $559.00 · 1.93B tokens')
  })
  it('one session is singular, a lower bound gets +, no cost or tokens are left out', () => {
    expect(factsText(1, 12.4, true, 290)).toBe('1 session · $12.40+ · 290 tokens')
    expect(factsText(2, null, false, null)).toBe('2 sessions')
  })
})
