import { describe, expect, it } from 'vitest'
import { assemblePages, pageKey, type PageState } from './agentPages'
import { call } from './testRows'
import { BAND_PAGE } from './rowList.logic'

const ready = (n: number, total: number, from = 0): PageState => ({ status: 'ready', rows: Array.from({ length: n }, (_, i) => call(`r${from + i}`, { depth: 1 })), total, message: null, retry: () => {} })
const loading: PageState = { status: 'loading', rows: [], total: 0, message: null, retry: () => {} }

describe('assemblePages (TRN-35, D38 M8)', () => {
  it('shows a skeleton while the first page loads, or before it is reported', () => {
    expect(assemblePages(new Map([[pageKey('a', 0), loading]]), new Map([['a', 1]])).bands.get('a')).toEqual({ status: 'loading' })
    expect(assemblePages(new Map(), new Map([['a', 1]])).bands.get('a')).toEqual({ status: 'loading' })
  })
  it('shows the error card with the server message and keeps the retry', () => {
    const retry = () => {}
    const a = assemblePages(new Map([[pageKey('a', 0), { ...loading, status: 'error', message: 'boom', retry }]]), new Map([['a', 1]]))
    expect(a.bands.get('a')).toEqual({ status: 'error', message: 'boom' })
    expect(a.retries.get('a')).toBe(retry)
    expect(a.pages.has('a')).toBe(false)
  })
  it('holds the rows of a ready page and counts what is missing', () => {
    const a = assemblePages(new Map([[pageKey('a', 0), ready(500, 1203)]]), new Map([['a', 1]]))
    expect(a.pages.get('a')!.rows).toHaveLength(500)
    expect(a.bands.get('a')).toEqual({ status: 'ready', total: 1203, loaded: 500 })
  })
  it('appends the next page when it arrives and waits for it while it loads', () => {
    const states = new Map([[pageKey('a', 0), ready(500, 1203)], [pageKey('a', BAND_PAGE), ready(500, 1203, 500)]])
    expect(assemblePages(states, new Map([['a', 2]])).bands.get('a')).toEqual({ status: 'ready', total: 1203, loaded: 1000 })
    states.set(pageKey('a', BAND_PAGE), loading)
    expect(assemblePages(states, new Map([['a', 2]])).bands.get('a')).toEqual({ status: 'ready', total: 1203, loaded: 500 })
  })
})
