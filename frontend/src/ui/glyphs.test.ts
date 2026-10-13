import { describe, expect, it } from 'vitest'
import { GLYPH, ICON_NAMES } from './glyphs'

describe('glyph map (SYS-28)', () => {
  it('uses only registered icon names', () => {
    for (const v of Object.values(GLYPH)) expect(ICON_NAMES).toContain(v)
  })
  it('never lets two kinds that share a turn list use one glyph (D38 L4)', () => {
    const turnList = [
      GLYPH.turnHuman, GLYPH.turnCommand, GLYPH.turnPlugin, GLYPH.turnAgentMessage, GLYPH.turnResume,
      GLYPH.gapWaiting, GLYPH.gapUsageLimit, GLYPH.gapAgentMessage, GLYPH.fold,
    ]
    expect(new Set(turnList).size).toBe(turnList.length)
  })
  it('keeps the D38 L4 gap glyphs out of every other turn-list row', () => {
    const others = [GLYPH.turnHuman, GLYPH.turnCommand, GLYPH.turnPlugin, GLYPH.turnAgentMessage, GLYPH.turnResume, GLYPH.fold]
    expect(others).not.toContain(GLYPH.gapUsageLimit)
    expect(others).not.toContain(GLYPH.gapAgentMessage)
  })
  it('lists no icon twice', () => {
    expect(new Set(ICON_NAMES).size).toBe(ICON_NAMES.length)
  })
})
