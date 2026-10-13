import { describe, expect, it } from 'vitest'
import { markShape } from './model-mark.logic'

describe('markShape (SYS-41)', () => {
  const cases: [string, string, string][] = [
    ['claude-opus-5-5', 'opus', '◆'],
    ['claude-sonnet-5-5', 'sonnet', '●'],
    ['claude-haiku-4-5-20251001', 'haiku', '▲'],
    ['claude-fable-5-1', 'fable', '■'],
    ['gpt-5.4', 'gpt', '⬡'],
    ['deepseek-v4', 'other', '●'],
    ['mimo-v2', 'other', '●'],
    ['codex-mini-latest', 'other', '●'],
  ]
  it.each(cases)('%s is the %s family with shape %s', (id, family, char) => {
    const s = markShape(id)
    expect(s.family).toBe(family)
    expect(s.char).toBe(char)
  })
  it('draws "other" smaller than sonnet', () => {
    expect(markShape('x').d).not.toBe(markShape('claude-sonnet-5').d)
  })
})
