import { afterEach, describe, expect, it, vi } from 'vitest'

type Zone = typeof import('./zone')
const real = () => vi.importActual<Zone>('./zone')

afterEach(() => {
  vi.restoreAllMocks()
  vi.resetModules()
})

describe('browserZone (FE-9 [D50])', () => {
  it('returns the zone the browser reports', async () => {
    const spy = vi.spyOn(Intl, 'DateTimeFormat').mockImplementation(
      () => ({ resolvedOptions: () => ({ timeZone: 'America/New_York' }) }) as Intl.DateTimeFormat,
    )
    vi.resetModules()
    const z = await real()
    expect(z.browserZone()).toBe('America/New_York')
    expect(z.browserZone()).toBe('America/New_York')
    expect(spy).toHaveBeenCalledTimes(1)
  })

  it('throws when the browser reports no zone, instead of guessing UTC', async () => {
    vi.spyOn(Intl, 'DateTimeFormat').mockImplementation(
      () => ({ resolvedOptions: () => ({ timeZone: undefined }) }) as unknown as Intl.DateTimeFormat,
    )
    vi.resetModules()
    const z = await real()
    expect(() => z.browserZone()).toThrow(/no IANA time zone/)
  })
})
