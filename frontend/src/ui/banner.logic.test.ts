import { describe, expect, it } from 'vitest'
import type { IngestStatus } from '../api/types'
import { bannerKind } from './banner.logic'

const base: IngestStatus = {
  store_exists: true,
  store_state: 'ready',
  freshness: { last_ingest_ms: 1, parser_version: 'p', price_fingerprint: 'f', schema_version: 7, api_version: 1 },
  ingest: { running: false, started_ms: null, pid: null },
  sources: [],
  last_run: null,
}

describe('bannerKind (SYS-57, SYS-57a)', () => {
  it('shows no banner for a ready store with no ingest', () => {
    expect(bannerKind(base)).toBeNull()
    expect(bannerKind(undefined)).toBeNull()
  })
  it('shows the no-store banner for a missing store', () => {
    expect(bannerKind({ ...base, store_exists: false, store_state: 'missing' })).toBe('no_store')
  })
  it('shows the rebuild banner for a schema mismatch', () => {
    expect(bannerKind({ ...base, store_state: 'schema_mismatch' })).toBe('rebuild')
  })
  it('shows the ingest banner while running and clears on the first response with running = false', () => {
    const running = { ...base, ingest: { running: true, started_ms: 5, pid: 9 } }
    expect(bannerKind(running)).toBe('ingest')
    expect(bannerKind({ ...running, ingest: { running: false, started_ms: null, pid: null } })).toBeNull()
  })
  it('keeps the no-store banner when a missing store is also ingesting', () => {
    const s = { ...base, store_exists: false, store_state: 'missing' as const, ingest: { running: true, started_ms: 5, pid: 9 } }
    expect(bannerKind(s)).toBe('no_store')
  })
})
