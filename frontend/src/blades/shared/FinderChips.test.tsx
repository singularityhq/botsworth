// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import type { FindResult } from '../../api/types'
import { FinderChips } from './FinderChips'
import type { FinderKind } from './finders'
import type { FinderSet } from './useFinders'

afterEach(cleanup)

type Hit = FindResult['hits'][number]
const hit = (turn: string, id: string): Hit => ({ row: { type: 'tool', id }, turn, display_id: null, depth: 0, label: id, value: null, start_ms: null })
const set = (pool: Hit[]): FinderSet => ({ pool, failed: false, retry: () => {} })
const sets: Record<FinderKind, FinderSet> = {
  waits: set([hit('A', 'w1'), hit('B', 'w2'), hit('B', 'w3')]),
  failures: set([hit('B', 'f1')]),
  skills: set([]),
  agents: set([hit('A', 'a1')]),
}
const noop = () => {}

describe('FinderChips (SES-31, TRN-10)', () => {
  it('draws the Biggest chip when the blade can rank (session blade)', () => {
    render(<FinderChips sets={sets} onFind={noop} onBiggest={noop} />)
    expect(screen.getByText('Biggest')).toBeTruthy()
  })

  it('draws no Biggest chip without an onBiggest handler (turn blade has none)', () => {
    render(<FinderChips sets={sets} onFind={noop} />)
    expect(screen.queryByText('Biggest')).toBeNull()
  })

  it('counts only this turn\'s hits when given a turn, and hides a kind at 0 there', () => {
    render(<FinderChips sets={sets} onFind={noop} turn="B" />)
    expect(screen.getByText('waits').previousElementSibling?.textContent).toBe('2')
    expect(screen.getByText('failed').previousElementSibling?.textContent).toBe('1')
    expect(screen.queryByText('agent')).toBeNull()
    expect(screen.queryByText('agents')).toBeNull()
  })
})
