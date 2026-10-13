import { describe, expect, it } from 'vitest'
import { freshnessText } from './freshness.logic'

const T = 1_000_000_000_000
describe('freshnessText (EXP-8)', () => {
  it('reads "Never ingested" for null', () => {
    expect(freshnessText(null, T)).toBe('Never ingested')
  })
  it('counts minutes, hours, days', () => {
    expect(freshnessText(T - 30_000, T)).toBe('Ingested just now')
    expect(freshnessText(T - 12 * 60_000, T)).toBe('Ingested 12 min ago')
    expect(freshnessText(T - 59 * 60_000, T)).toBe('Ingested 59 min ago')
    expect(freshnessText(T - 60 * 60_000, T)).toBe('Ingested 1 h ago')
    expect(freshnessText(T - 47 * 3600_000, T)).toBe('Ingested 47 h ago')
    expect(freshnessText(T - 72 * 3600_000, T)).toBe('Ingested 3 d ago')
  })
  it('treats a future timestamp (clock skew) as just now', () => {
    expect(freshnessText(T + 5000, T)).toBe('Ingested just now')
  })
})
