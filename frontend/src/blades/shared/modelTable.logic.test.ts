import { describe, expect, it } from 'vitest'
import { fixtures } from '../../api/__fixtures__'
import type { ModelLine } from '../../api/types'
import { showUnattributed } from './answers.logic'
import { derivedNote, modelsByCost, speedText, ttftText } from './modelTable.logic'

const line = (o: Partial<ModelLine>): ModelLine => ({ ...(fixtures.sessionDetail.session.models[0] as ModelLine), ...o })

describe('model table (SES-28)', () => {
  it('lists models by cost, highest first', () => {
    const rows = modelsByCost([line({ model: 'a', cost_usd: 1 }), line({ model: 'b', cost_usd: 9 }), line({ model: 'c', cost_usd: null })])
    expect(rows.map((r) => r.model)).toEqual(['b', 'a', 'c'])
  })

  it('speed is tok/s rounded, blank without model time or without a figure', () => {
    expect(speedText({ speed_tok_s: 55.2, model_ms: 120000 })).toBe('55 tok/s')
    expect(speedText({ speed_tok_s: 55.2, model_ms: 0 })).toBe('')
    expect(speedText({ speed_tok_s: null, model_ms: 10 })).toBe('')
  })

  it('first token is milliseconds under a second, seconds above', () => {
    expect(ttftText(900.5)).toBe('901ms')
    expect(ttftText(1500)).toBe('1.5s')
  })

  it('the approximation note appears when any model time is derived, and names the provider', () => {
    expect(derivedNote([line({ speed_basis: 'derived' })], 'claude_code')).toBe(
      'Model time for Claude Code is taken from log record times, so speed is approximate.',
    )
    expect(derivedNote([line({ speed_basis: 'present' }), line({ speed_basis: 'derived' })], 'codex')).toContain('Codex')
    expect(derivedNote([line({ speed_basis: 'present' })], 'codex')).toBeNull()
  })
})

describe('the unattributed line (SES-12, SES-28)', () => {
  it('shows from $0.01 and only when known', () => {
    expect(showUnattributed({ unattributed: 0.01 })).toBe(true)
    expect(showUnattributed({ unattributed: 0.0099 })).toBe(false)
    expect(showUnattributed({ unattributed: 0 })).toBe(false)
    expect(showUnattributed({ unattributed: null })).toBe(false)
  })
})
