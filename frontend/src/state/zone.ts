import { useMemo } from 'react'

/**
 * The one source of the time zone (QRY-4, FE-9 [D50]): the browser's. The server stores no zone and
 * its responses name none; every request that takes a Scope or counts days sends this as `tz`
 * (client.ts), and every local clock, day and week in the UI is read in it.
 */

let cached: string | null = null

/** The browser's IANA zone name, e.g. `Asia/Kolkata`. */
export function browserZone(): string {
  if (cached === null) {
    const z = Intl.DateTimeFormat().resolvedOptions().timeZone
    if (typeof z !== 'string' || z === '') throw new Error('browserZone: the browser reports no IANA time zone')
    cached = z
  }
  return cached
}

/** The same zone for components. It cannot change during a page's life, so the value is stable across renders. */
export function useBrowserZone(): string {
  return useMemo(browserZone, [])
}
