import { describe, expect, it } from 'vitest'
import type { PeriodSummary } from '../../api/types'
import { fixtures } from '../../api/__fixtures__'
import { DEFAULT_URL_STATE, type UrlState } from '../../state/url'
import {
  allTimeMatchText, canClearAll, clearAllPatch, commitField, emptyPeriodText, fieldText, filterTokens, isNarrowed, middleTruncate,
  nearbyDayText, pageState, periodTitle, presetRange, projectFallbackName, removeTokenPatch, toggleValue, totalsText, totalsView,
} from './explorer.logic'

const TODAY = '2026-10-12'
const EXTENT = { first_day: '2026-09-22', last_day: '2026-10-12' }
const preset = (p: 'today' | 'yesterday' | 'last7' | 'thisweek' | 'lastweek' | 'all') => ({ kind: 'preset', preset: p }) as const
const url = (o: Partial<UrlState> = {}): UrlState => ({ ...DEFAULT_URL_STATE, ...o })

describe('periodTitle (EXP-10)', () => {
  it.each([
    [preset('today'), 'Today · Mon Oct 12'],
    [preset('yesterday'), 'Yesterday · Sun Oct 11'],
    [preset('last7'), 'Last 7 days · Oct 6 – Oct 12'],
    [preset('thisweek'), 'This week · Oct 12'],
    [preset('lastweek'), 'Last week · Oct 5 – Oct 11'],
    [preset('all'), 'All time · Sep 22 – Oct 12'],
    [{ kind: 'custom', from: '2026-10-01', to: '2026-10-08' } as const, 'Oct 1 – Oct 8'],
    [{ kind: 'custom', from: '2026-10-07', to: '2026-10-07' } as const, 'Wed Oct 7'],
  ])('%j reads %s', (p, text) => {
    expect(periodTitle(p, TODAY, EXTENT)).toBe(text)
  })
  it('shows the preset name alone until the store zone is known, and all time alone without an extent', () => {
    expect(periodTitle(preset('last7'), null, EXTENT)).toBe('Last 7 days')
    expect(periodTitle(preset('all'), TODAY, null)).toBe('All time')
    expect(periodTitle(preset('all'), TODAY, { first_day: null, last_day: null })).toBe('All time')
  })
  it('gives the range column of a preset row (EXP-13)', () => {
    expect(presetRange(preset('yesterday'), TODAY, EXTENT)).toBe('Oct 11')
    expect(presetRange(preset('lastweek'), TODAY, EXTENT)).toBe('Oct 5 – Oct 11')
    expect(presetRange(preset('all'), TODAY, EXTENT)).toBe('Sep 22 – Oct 12')
    expect(presetRange(preset('all'), TODAY, null)).toBe('')
  })
})

describe('commitField (EXP-15)', () => {
  const range = { from: '2026-10-06', to: '2026-10-12' }
  it('applies "oct 7" typed in To with From = Oct 6: Oct 6 to Oct 7', () => {
    expect(commitField({ from: '2026-10-06', to: '2026-10-12' }, 'to', 'oct 7', TODAY)).toEqual({ from: '2026-10-06', to: '2026-10-07' })
  })
  it('moves To along when From is set after it, and From along when To is set before it', () => {
    expect(commitField({ from: '2026-10-06', to: '2026-10-09' }, 'from', 'oct 11', TODAY)).toEqual({ from: '2026-10-11', to: '2026-10-11' })
    expect(commitField(range, 'from', 'today', TODAY)).toEqual({ from: '2026-10-12', to: '2026-10-12' })
    expect(commitField(range, 'to', '2026-10-01', TODAY)).toEqual({ from: '2026-10-01', to: '2026-10-01' })
  })
  it('returns null for text that is not a date, so the field shows its previous value', () => {
    expect(commitField(range, 'to', 'banana', TODAY)).toBeNull()
    expect(commitField(range, 'to', '', TODAY)).toBeNull()
  })
  it('formats a field as Oct 6', () => {
    expect(fieldText('2026-10-06')).toBe('Oct 6')
  })
})

describe('Clear all (EXP-26)', () => {
  it('shows only when the search text is set or a filter differs from its default', () => {
    expect(canClearAll(url())).toBe(false)
    expect(canClearAll(url({ q: 'rbac' }))).toBe(true)
    expect(canClearAll(url({ models: ['x'] }))).toBe(true)
    expect(canClearAll(url({ projects: ['/p/a'] }))).toBe(true)
    expect(canClearAll(url({ auto: true }))).toBe(true)
  })
  it('does not show for a search of spaces only', () => {
    expect(canClearAll(url({ q: '   ' }))).toBe(false)
  })
  it('resets exactly the search, the four filters and the automated rule, nothing else', () => {
    expect(Object.keys(clearAllPatch()).sort()).toEqual(['auto', 'branches', 'models', 'projects', 'providers', 'q'])
    expect(clearAllPatch()).toEqual({ q: '', providers: [], models: [], branches: [], projects: [], auto: false })
    const cleared = { ...url({ q: 'rbac', models: ['m'], auto: true, measure: 'tokens', sort: 'cost', group: 'day', period: preset('yesterday') }), ...clearAllPatch() }
    expect(cleared).toMatchObject({ measure: 'tokens', sort: 'cost', group: 'day', period: preset('yesterday'), q: '', models: [], auto: false })
  })
})

describe('filterTokens (EXP-25, EXP-27, EXP-29)', () => {
  const name = (k: string) => (k === '/p/biz' ? 'bizdemo' : projectFallbackName(k))
  it('orders providers, models, branches, projects, automated', () => {
    const t = filterTokens(url({ providers: ['codex'], models: ['claude-opus-5-5'], branches: ['main'], projects: ['/p/biz'] }), 12, name)
    expect(t.map((x) => [x.kind, x.label])).toEqual([
      ['provider', 'Codex'], ['model', 'opus 5.5'], ['branch', 'main'], ['project', 'bizdemo'], ['automated', 'automated hidden · 12'],
    ])
  })
  it('shows the automated token only while automated sessions are hidden and N > 0', () => {
    expect(filterTokens(url(), 0, name)).toEqual([])
    expect(filterTokens(url({ auto: true }), 12, name)).toEqual([])
  })
  it('keeps the token of a project the summary does not know, named from its path', () => {
    expect(filterTokens(url({ projects: ['/Users/a/gone'] }), 0, name)[0].label).toBe('gone')
  })
  it('removes one token: the other project stays; the automated token shows automated sessions', () => {
    const u = url({ projects: ['/p/biz', '/p/jio'] })
    const jio = filterTokens(u, 0, name).find((t) => t.value === '/p/jio')!
    expect(removeTokenPatch(u, jio)).toEqual({ projects: ['/p/biz'] })
    expect(removeTokenPatch(url(), { kind: 'automated', value: 'auto', label: '' })).toEqual({ auto: true })
    expect(removeTokenPatch(url({ providers: ['codex', 'omp'] }), { kind: 'provider', value: 'omp', label: '' })).toEqual({ providers: ['codex'] })
  })
  it('toggles a value in a multi-select list', () => {
    expect(toggleValue(['a'], 'b')).toEqual(['a', 'b'])
    expect(toggleValue(['a', 'b'], 'a')).toEqual(['b'])
  })
})

describe('middleTruncate (EXP-23)', () => {
  it('keeps short names and cuts a long one in the middle', () => {
    expect(middleTruncate('main', 30)).toBe('main')
    const t = middleTruncate('conductor/rbac-continuation-2026-09-22', 30)
    expect(t).toHaveLength(30)
    expect(t.startsWith('conductor/')).toBe(true)
    expect(t.endsWith('09-22')).toBe(true)
    expect(t).toContain('…')
  })
})

describe('totals line (EXP-12)', () => {
  const summary = (over: Partial<PeriodSummary['totals']>): PeriodSummary => ({
    ...fixtures.explorerSummary,
    totals: { ...fixtures.explorerSummary.totals, ...over },
  }) as PeriodSummary

  it('reads the full line with the unpriced mark, the hidden count and plural words', () => {
    const v = totalsView(summary({ cost_usd: 2570, cost_lower_bound: true, tok_total: 8.69e9, n_sessions: 212, n_sessions_period: 212, n_automated_hidden: 12 }), url())
    expect(totalsText(v)).toBe('$2,570.00+   8.69B tokens   212 sessions   12 automated hidden')
  })
  it('narrows to "7 of 212 sessions" when a filter or the search is on (the scenario line)', () => {
    const s = summary({ cost_usd: 49.71, cost_lower_bound: false, tok_total: 146e6, n_sessions: 7, n_sessions_period: 212, n_automated_hidden: 12 })
    expect(totalsText(totalsView(s, url({ models: ['claude-opus-5-5'] })))).toBe('$49.71   146M tokens   7 of 212 sessions   12 automated hidden')
    expect(totalsText(totalsView(s, url({ q: 'rbac' })))).toContain('7 of 212 sessions')
  })
  it('does not say "of" when nothing narrows the set', () => {
    expect(totalsView(summary({ n_sessions: 7, n_sessions_period: 212 }), url()).of).toBeNull()
  })
  it('shows "N automated hidden" only when N > 0 and automated sessions are hidden', () => {
    expect(totalsText(totalsView(summary({ n_automated_hidden: 0 }), url()))).not.toContain('hidden')
    expect(totalsText(totalsView(summary({ n_automated_hidden: 12 }), url({ auto: true })))).not.toContain('hidden')
  })
  it('reads a real zero for an empty period', () => {
    const v = totalsView(summary({ cost_usd: null, tok_total: null, n_sessions: 0, n_sessions_period: 0, n_automated_hidden: 0, cost_lower_bound: false }), url())
    expect(totalsText(v)).toBe('$0.00   0 tokens   0 sessions')
  })
  it('uses the singular for one session', () => {
    expect(totalsText(totalsView(summary({ n_sessions: 1, n_automated_hidden: 0 }), url()))).toContain('1 session')
    expect(totalsText(totalsView(summary({ n_sessions: 1, n_automated_hidden: 0 }), url()))).not.toContain('1 sessions')
  })
})

describe('page state and empty texts (EXP-79, EXP-80)', () => {
  const zero = { ...fixtures.explorerSummary, totals: { ...fixtures.explorerSummary.totals, n_sessions: 0 } } as PeriodSummary
  it('is loading until a summary arrives', () => {
    expect(pageState(undefined, url())).toBe('loading')
  })
  it('is ready with sessions, no-results when a search or filter leaves none, and an empty period when nothing narrows', () => {
    expect(pageState(fixtures.explorerSummary as PeriodSummary, url())).toBe('ready')
    expect(pageState(zero, url({ q: 'zzz' }))).toBe('no-results')
    expect(pageState(zero, url({ projects: ['/p/a'] }))).toBe('no-results')
    expect(pageState(zero, url())).toBe('empty-period')
  })
  it('names the empty day or range', () => {
    expect(emptyPeriodText('2026-10-04', '2026-10-04')).toBe('No sessions on Sun Oct 4')
    expect(emptyPeriodText('2026-10-01', '2026-10-03')).toBe('No sessions from Oct 1 to Oct 3')
    expect(nearbyDayText('2026-10-03', 12)).toBe('Sat Oct 3 · 12 sessions')
    expect(nearbyDayText('2026-10-03', 1)).toBe('Sat Oct 3 · 1 session')
  })
  it('words the all-time count (EXP-79 scenario)', () => {
    expect(allTimeMatchText('rbacxyz', 3)).toBe('"rbacxyz" matches 3 sessions in all time')
    expect(allTimeMatchText('rbacxyz', 0)).toBe('"rbacxyz" matches 0 sessions in all time')
  })
  it('counts the project filter among the narrowing filters', () => {
    expect(isNarrowed(url({ projects: ['x'] }))).toBe(true)
    expect(isNarrowed(url({ auto: true }))).toBe(false)
  })
})
