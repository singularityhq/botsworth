import { describe, expect, it } from 'vitest'
import { DEFAULT_URL_STATE, type UrlState } from '../state/url'
import { bladeKeys, focusAfterChange } from './blades'

const url = (o: Partial<UrlState>): UrlState => ({ ...DEFAULT_URL_STATE, ...o })

describe('bladeKeys (contract: blade list from the URL)', () => {
  it('shows the explorer alone with no blade parameters', () => {
    expect(bladeKeys(url({}))).toEqual(['explorer'])
  })
  it('adds the session for s, the turn for t, the detail for detail=1', () => {
    expect(bladeKeys(url({ s: 'x' }))).toEqual(['explorer', 'session'])
    expect(bladeKeys(url({ s: 'x', t: 1, detail: true }))).toEqual(['explorer', 'session', 'turn', 'detail'])
  })
  it('counts turn "0" as open', () => {
    expect(bladeKeys(url({ s: 'x', t: 0 }))).toEqual(['explorer', 'session', 'turn'])
  })
})

describe('focusAfterChange (SES-5)', () => {
  it('gives focus to the newest blade when one opens', () => {
    expect(focusAfterChange(['explorer'], ['explorer', 'session'], 'explorer')).toBe('session')
  })
  it('gives focus to the newest blade when one closes', () => {
    expect(focusAfterChange(['explorer', 'session', 'turn'], ['explorer', 'session'], 'turn')).toBe('session')
  })
  it('keeps the focus the user moved with the arrows when nothing opened or closed', () => {
    const keys = ['explorer', 'session', 'turn']
    expect(focusAfterChange(keys, keys, 'explorer')).toBe('explorer')
  })
})
