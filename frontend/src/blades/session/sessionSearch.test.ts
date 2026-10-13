import { describe, expect, it } from 'vitest'
import { fixtures } from '../../api/__fixtures__'
import type { FindResult } from '../../api/types'
import { firstKept, keptTurns, matchCount, noMatch, searchRequest } from './sessionSearch'
import { gap, turn } from './testRows'
import { buildEntries } from './turnList.logic'

const result = (per_turn: Record<string, number>, total = 5): FindResult => ({ ...(fixtures.sessionFind as unknown as FindResult), kind: 'search', total, per_turn })

describe('session search (SES-32)', () => {
  it('sends no request for empty or blank text', () => {
    expect(searchRequest('')).toBeNull()
    expect(searchRequest('   ')).toBeNull()
    expect(searchRequest('pytest')).toEqual({ kind: 'search', text: 'pytest' })
  })

  it('keeps the turns with at least one matching row', () => {
    const kept = keptTurns(result({ a: 2, b: 0, c: 1 }))
    expect([...kept!]).toEqual(['a', 'c'])
    expect(keptTurns(undefined)).toBeNull()
  })

  it('counts matching rows per turn for the search flag', () => {
    const r = result({ a: 2 })
    expect(matchCount(r, 'a')).toBe(2)
    expect(matchCount(r, 'zz')).toBe(0)
    expect(matchCount(undefined, 'a')).toBeNull()
  })

  it('reports the no-match state when there are no hits', () => {
    expect(noMatch(result({}, 0))).toBe(true)
    expect(noMatch(result({ a: 1 }, 1))).toBe(false)
    expect(noMatch(undefined)).toBe(false)
  })

  it('Enter or the down arrow lands on the first kept turn', () => {
    const t1 = turn(1)
    const t2 = turn(2)
    const t3 = turn(3)
    const entries = buildEntries([t1, gap(500_000, 200_000), t2, t3], { kept: keptTurns(result({ [t2.turn]: 1, [t3.turn]: 4 })), range: null })
    expect(firstKept(entries)).toBe(t2.turn)
    expect(firstKept([])).toBeNull()
  })
})
