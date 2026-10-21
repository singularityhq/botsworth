import { describe, expect, it } from 'vitest'
import { DEFAULT_URL_STATE, openBlades, parseUrl, serializeUrl, toScope, type UrlState } from './url'

/** Parse, then serialise: the canonical form of a query string. */
const canon = (search: string) => serializeUrl(parseUrl(search))

describe('parseUrl defaults (EXP-74)', () => {
  it('gives every default for an empty query', () => {
    expect(parseUrl('')).toEqual(DEFAULT_URL_STATE)
    expect(parseUrl('?')).toEqual(DEFAULT_URL_STATE)
    expect(DEFAULT_URL_STATE.period).toEqual({ kind: 'preset', preset: 'last7' })
  })

  it('serialises the defaults to nothing', () => {
    expect(serializeUrl(DEFAULT_URL_STATE)).toBe('')
  })
})

describe('explorer parameters round trip (EXP-74)', () => {
  const cases: [string, Partial<UrlState>][] = [
    ['period=yesterday', { period: { kind: 'preset', preset: 'yesterday' } }],
    ['period=today', { period: { kind: 'preset', preset: 'today' } }],
    ['period=thisweek', { period: { kind: 'preset', preset: 'thisweek' } }],
    ['period=lastweek', { period: { kind: 'preset', preset: 'lastweek' } }],
    ['period=all', { period: { kind: 'preset', preset: 'all' } }],
    ['from=2026-10-06&to=2026-10-12', { period: { kind: 'custom', from: '2026-10-06', to: '2026-10-12' } }],
    ['q=rbac+sync', { q: 'rbac sync' }],
    ['provider=codex&provider=omp', { providers: ['codex', 'omp'] }],
    ['model=claude-opus-5-5&model=gpt-5.4', { models: ['claude-opus-5-5', 'gpt-5.4'] }],
    ['branch=main&branch=feature%2Fx', { branches: ['main', 'feature/x'] }],
    ['project=%2FUsers%2Fa%2Fbizdemo', { projects: ['/Users/a/bizdemo'] }],
    ['auto=show', { auto: true }],
    ['measure=tokens', { measure: 'tokens' }],
    ['sort=cost', { sort: 'cost' }],
    ['sort=wall&dir=asc', { sort: 'wall', dir: 'asc' }],
    ['group=project', { group: 'project' }],
    ['group=day', { group: 'day' }],
  ]
  for (const [query, expected] of cases) {
    it(query, () => {
      expect(parseUrl(query)).toEqual({ ...DEFAULT_URL_STATE, ...expected })
      expect(serializeUrl(parseUrl(query))).toBe(query)
    })
  }

  it('reads EXP-74\'s scenario ?period=yesterday&sort=wall', () => {
    const u = parseUrl('?period=yesterday&sort=wall')
    expect(u.period).toEqual({ kind: 'preset', preset: 'yesterday' })
    expect(u.sort).toBe('wall')
    expect(u.dir).toBe('desc')
  })

  it('serialises in a fixed order whatever the input order', () => {
    expect(canon('sort=cost&q=a&period=all')).toBe('period=all&q=a&sort=cost')
  })

  it('omits a parameter written at its default', () => {
    expect(canon('period=last7&measure=cost&sort=start&dir=desc')).toBe('')
  })
})

describe('unknown values are dropped (EXP-75, SES-11)', () => {
  it('drops unknown enum values', () => {
    expect(parseUrl('period=lastmonth')).toEqual(DEFAULT_URL_STATE)
    expect(parseUrl('sort=banana&dir=up&measure=time&group=week')).toEqual(DEFAULT_URL_STATE)
    expect(canon('sort=banana&auto=hide&auto=0')).toBe('')
  })

  it('drops an unknown provider but keeps free-text filter values that match nothing', () => {
    expect(parseUrl('provider=claude_code&provider=gemini').providers).toEqual(['claude_code'])
    expect(parseUrl('branch=deleted-branch').branches).toEqual(['deleted-branch'])
    expect(parseUrl('model=no-such-model').models).toEqual(['no-such-model'])
  })

  it('needs both from and to to be real days with from <= to', () => {
    expect(parseUrl('from=2026-10-06').period).toEqual(DEFAULT_URL_STATE.period)
    expect(parseUrl('from=2026-10-06&to=oct12').period).toEqual(DEFAULT_URL_STATE.period)
    expect(parseUrl('from=2026-10-12&to=2026-10-06').period).toEqual(DEFAULT_URL_STATE.period)
    expect(parseUrl('from=2026-02-30&to=2026-03-01').period).toEqual(DEFAULT_URL_STATE.period)
  })

  it('lets a valid custom range replace the period parameter', () => {
    expect(parseUrl('period=all&from=2026-10-06&to=2026-10-12').period).toEqual({
      kind: 'custom', from: '2026-10-06', to: '2026-10-12',
    })
  })

  it('removes repeated values and empty values', () => {
    expect(parseUrl('provider=omp&provider=omp').providers).toEqual(['omp'])
    expect(parseUrl('model=&model=x').models).toEqual(['x'])
  })

  it('drops unrelated parameters when it rewrites the URL', () => {
    expect(canon('utm=1&sort=cost')).toBe('sort=cost')
  })
})

describe('blade parameters (SES-8)', () => {
  const SCENARIO = '?s=a11ce5e5&t=2&step=gap-1791790200000&detail=1'

  it('reads the SES-8 scenario URL', () => {
    const u = parseUrl(SCENARIO)
    expect(u).toMatchObject({ s: 'a11ce5e5', t: 2, step: 'gap-1791790200000', detail: true })
    expect(openBlades(u)).toEqual(['explorer', 'session', 'turn', 'detail'])
    expect(serializeUrl(u)).toBe('s=a11ce5e5&t=2&step=gap-1791790200000&detail=1')
  })

  it('round trips every blade parameter', () => {
    const q =
      's=a11ce5e5&bars=time&find=rbac&range=1000-2000&over=1&sview=waterfall&sreal=1&info=1' +
      '&t=12&step=abc&view=waterfall&order=cost&real=1&zoom=1500-1800&thist=0&tinfo=1&detail=1'
    const u = parseUrl(q)
    expect(u).toMatchObject({
      s: 'a11ce5e5', bars: 'time', find: 'rbac', range: { start_ms: 1000, end_ms: 2000 }, over: true,
      sview: 'waterfall', sreal: true, info: true, t: 12, step: 'abc', view: 'waterfall', order: 'cost',
      real: true, zoom: { start_ms: 1500, end_ms: 1800 }, thist: false, tinfo: true, detail: true,
    })
    expect(serializeUrl(u)).toBe(q)
  })

  it('tells thist=1 from thist=0 from absent', () => {
    expect(parseUrl('s=a&thist=1').thist).toBe(true)
    expect(parseUrl('s=a&thist=0').thist).toBe(false)
    expect(parseUrl('s=a').thist).toBeNull()
    expect(parseUrl('s=a&thist=2').thist).toBeNull()
  })

  it('accepts the session ids the server accepts: op id and 8-character prefix', () => {
    const op = 'claude_code/a11ce5e5-1111-4222-8333-944455556666/session/-'
    expect(parseUrl(`s=${encodeURIComponent(op)}`).s).toBe(op)
    expect(serializeUrl(parseUrl(`s=${encodeURIComponent(op)}`))).toBe(`s=${encodeURIComponent(op)}`)
  })

  it('drops unknown or malformed blade values', () => {
    const u = parseUrl('s=a&bars=money&t=zero&range=5-3&zoom=abc&order=slow&view=table&sview=table&over=true&detail=yes')
    expect(u).toMatchObject({
      s: 'a', bars: 'cost', t: null, range: null, zoom: null, order: null, view: 'ledger', sview: 'ledger',
      over: false, detail: false,
    })
    expect(canon('s=a&t=0')).toBe('s=a')
    expect(canon('s=a&t=-1')).toBe('s=a')
    expect(canon('s=a&t=1.5')).toBe('s=a')
  })

  it('treats ledger and cost, the defaults, as omitted', () => {
    expect(canon('s=a&bars=cost&sview=ledger&view=ledger')).toBe('s=a')
  })

  it('resets every blade parameter when there is no session', () => {
    expect(canon('t=2&step=x&detail=1&find=abc&over=1&zoom=1-2')).toBe('')
    expect(openBlades(parseUrl('t=2&detail=1'))).toEqual(['explorer'])
  })

  it('keeps explorer parameters next to blade parameters', () => {
    expect(canon('s=a&sort=cost&q=x&t=3')).toBe('q=x&sort=cost&s=a&t=3')
  })

  it('derives the blade list from the parameters', () => {
    expect(openBlades(parseUrl(''))).toEqual(['explorer'])
    expect(openBlades(parseUrl('s=a'))).toEqual(['explorer', 'session'])
    expect(openBlades(parseUrl('s=a&t=2'))).toEqual(['explorer', 'session', 'turn'])
    expect(openBlades(parseUrl('s=a&t=2&detail=1'))).toEqual(['explorer', 'session', 'turn', 'detail'])
  })

  it('keeps search text exactly, including spaces at the ends (EXP-19 typing)', () => {
    expect(parseUrl('q=rbac+').q).toBe('rbac ')
    expect(parseUrl(`find=${encodeURIComponent(' a&b=c ')}`).find).toBe('')
    expect(parseUrl(`s=a&find=${encodeURIComponent(' a&b=c ')}`).find).toBe(' a&b=c ')
  })
})

describe('canonical form is stable', () => {
  it('re-serialising a canonical query changes nothing', () => {
    for (const q of [
      'period=lastweek&q=a+b&provider=omp&model=m&auto=show&measure=tokens&sort=cost&dir=asc&group=day',
      's=a&bars=tokens&find=z&t=1&step=x&detail=1',
      'from=2026-10-01&to=2026-10-02&project=%2Fa%2Fb',
    ]) {
      expect(canon(q)).toBe(q)
    }
  })
})

describe('toScope', () => {
  it('resolves the period against today and carries the filters (QRY-3)', () => {
    const u = parseUrl('period=lastweek&q=rbac&provider=codex&model=m1&branch=main&project=%2Fa&auto=show')
    expect(toScope(u, '2026-10-12')).toEqual({
      from_day: '2026-10-05', to_day: '2026-10-11', q: 'rbac', providers: ['codex'], models: ['m1'],
      branches: ['main'], projects: ['/a'], show_automated: true,
    })
  })

  it('gives null days for all time', () => {
    expect(toScope(parseUrl('period=all'), '2026-10-12')).toMatchObject({ from_day: null, to_day: null, show_automated: false })
  })
})

describe('scope review blade (REV-21, EXP-74)', () => {
  it('reads review=1 and writes it back; it is omitted when closed', () => {
    expect(parseUrl('review=1').review).toBe(true)
    expect(canon('review=1&period=today')).toBe('period=today&review=1')
    expect(canon('review=0')).toBe('')
    expect(canon('')).toBe('')
  })

  it('opens the review as blade 1 and puts no session blade beside it', () => {
    expect(openBlades(parseUrl('review=1'))).toEqual(['explorer', 'review'])
  })

  it('never has review and s together: the session wins, and review is dropped from the URL', () => {
    const u = parseUrl('review=1&s=a')
    expect(u.review).toBe(false)
    expect(openBlades(u)).toEqual(['explorer', 'session'])
    expect(canon('review=1&s=a')).toBe('s=a')
  })

  it('drops turn blade parameters under a review, since there is no session', () => {
    expect(canon('review=1&t=3&detail=1&find=x')).toBe('review=1')
  })
})
