import type { FindOpts } from '../../api/client'
import type { FindResult } from '../../api/types'
import { cursorStops, type ListEntry } from './turnList.logic'

/** SES-32 session search: the request, the turns it keeps, the no-match state. FE-5 sets the debounce. */
export const SEARCH_DEBOUNCE_MS = 150

/** The find request for the typed text, or null when there is nothing to search for. */
export function searchRequest(text: string): FindOpts | null {
  return text.trim() === '' ? null : { kind: 'search', text }
}

/** Turn op ids with at least one matching row, or null while no result applies (no search, or still loading). */
export function keptTurns(result: FindResult | undefined): ReadonlySet<string> | null {
  if (!result) return null
  return new Set(Object.entries(result.per_turn).filter(([, n]) => n > 0).map(([turn]) => turn))
}

/** Matching rows in a turn: the `search N` flag of SES-34 item 4. */
export function matchCount(result: FindResult | undefined, turn: string): number | null {
  if (!result) return null
  return result.per_turn[turn] ?? 0 // FALLBACK-OK: interfaces.md API-25 (per_turn lists only turns with hits; a turn absent from it has none)
}

export function noMatch(result: FindResult | undefined): boolean {
  return result !== undefined && result.total === 0
}

/** Enter or ↓ in the field puts the cursor on the first kept turn (SES-32). */
export function firstKept(entries: readonly ListEntry[]): string | null {
  return cursorStops(entries)[0]?.key ?? null // FALLBACK-OK: SES-32 (no kept turn: the cursor stays where it is)
}
