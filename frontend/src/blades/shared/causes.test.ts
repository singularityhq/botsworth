import { describe, expect, it } from 'vitest'
import { causeFill, gapFill, isStalled } from './causes'
import { stateFill } from './states'

describe('gapFill (SES-19a, SES-20 [D56])', () => {
  it('draws a none/stalled stretch with the REV-1 stalled fill, from the same table as Review', () => {
    expect(isStalled({ state: 'none', substate: 'stalled' })).toBe(true)
    expect(gapFill({ state: 'none', substate: 'stalled' })).toEqual(causeFill('stalled'))
    expect(gapFill({ state: 'none', substate: 'stalled' }).boxShadow).toBe('inset 0 0 0 1px var(--color-error)')
  })

  it('leaves every other stretch with its state fill', () => {
    expect(gapFill({ state: 'none', substate: 'none' })).toEqual(stateFill('none'))
    expect(gapFill({ state: 'none', substate: 'before_first' })).toEqual(stateFill('none'))
    expect(gapFill({ state: 'tool', substate: 'background' })).toEqual(stateFill('tool'))
    expect(gapFill({ state: 'you', substate: 'reply' })).toEqual(stateFill('you'))
  })
})
